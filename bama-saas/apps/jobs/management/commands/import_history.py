"""Replay ``bama-scraper/data/history.db`` to build price-through-time.

Maps each source fetch_run to a Django FetchRun (source=history_replay), then
processes every observation in global observed-time order so the change-only
price series comes out clean. Per-observation publish-complete filtering is
applied (same rule as import_scraped).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone as djtz

from apps.history.models import FetchRun
from apps.jobs.services.dimensions import reset_cache
from apps.jobs.services.ingest import ingest_ad, reset_price_cache
from apps.parsing import extract_ad, parse_publish_time, unpack_payload


def _ts(value) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


class Command(BaseCommand):
    help = "Replay bama-scraper history.db to build price-through-time"

    def add_arguments(self, parser):
        parser.add_argument("--db", default=settings.BAMA_HISTORY_DB_PATH)
        parser.add_argument("--limit", type=int, default=None, help="Max observations")
        parser.add_argument("--batch-size", type=int, default=200)

    def handle(self, *args, **opts):
        reset_cache()
        reset_price_cache()

        db_path = Path(opts["db"]).expanduser()
        if not db_path.exists():
            self.stderr.write(self.style.ERROR(f"history.db not found: {db_path}"))
            return

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        # Map each source run to a Django FetchRun.
        run_map = {}
        for row in conn.execute(
            "SELECT id, origin, started_ts, finished_ts, fetched_count, status FROM fetch_runs"
        ):
            run_map[row["id"]] = FetchRun.objects.create(
                source=FetchRun.Source.HISTORY_REPLAY,
                status=FetchRun.Status.SUCCEEDED,
                started_at=_ts(row["started_ts"]) or djtz.now(),
                finished_at=_ts(row["finished_ts"]),
                fetched_count=row["fetched_count"] or 0,
            )
        self.stdout.write(f"Created {len(run_map)} replay FetchRuns")

        cur = conn.execute(
            """
            SELECT o.code, o.run_id, o.observed_ts, o.publish_phrase,
                   v.payload_zlib, v.semantic_hash, v.raw_hash
            FROM ad_observations o
            JOIN ad_versions v ON v.id = o.version_id
            ORDER BY o.observed_ts ASC, o.id ASC
            """
        )

        processed = skipped = price_changes = 0
        batch_size = opts["batch_size"]
        limit = opts["limit"]

        while True:
            chunk = cur.fetchmany(batch_size)
            if not chunk:
                break
            with transaction.atomic():
                for row in chunk:
                    run = run_map.get(row["run_id"])
                    if run is None:
                        skipped += 1
                        continue
                    observed_at = _ts(row["observed_ts"])
                    try:
                        payload = unpack_payload(row["payload_zlib"])
                    except Exception:  # noqa: BLE001 — skip corrupt blobs
                        skipped += 1
                        continue
                    extracted = extract_ad(payload, observed_at)
                    if not extracted:
                        skipped += 1
                        continue
                    phrase = extracted.get("publish_phrase") or row["publish_phrase"]
                    publish_at = parse_publish_time(phrase, observed_at)
                    price = extracted.get("current_price")
                    if publish_at is None or not price or price <= 0:
                        skipped += 1
                        continue
                    _, _, pc = ingest_ad(
                        extracted,
                        run=run,
                        observed_at=observed_at,
                        publish_at=publish_at,
                        dealer=payload.get("dealer"),
                    )
                    if pc:
                        price_changes += 1
                    processed += 1
                    if limit and processed >= limit:
                        break
            self.stdout.write(
                f"  processed={processed} skipped={skipped} price_changes={price_changes}"
            )
            if limit and processed >= limit:
                break

        conn.close()
        reset_cache()
        reset_price_cache()
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. processed={processed} skipped={skipped} price_changes={price_changes}"
            )
        )
