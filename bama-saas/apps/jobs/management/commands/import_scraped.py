"""Bulk-import publish-complete scraped ads.

Walks ``BAMA ADS/**/ads.json`` under ``BAMA_SCRAPED_DATA_ROOT`` and ingests
every ad whose publish time could be parsed AND whose price is positive
(decision #4: publish-complete only). Re-running is idempotent for
Ad / AdVersion / PriceObservation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone as djtz

from apps.history.models import FetchRun, UnknownTimePhrase
from apps.jobs.services.dimensions import reset_cache
from apps.jobs.services.ingest import ingest_ad
from apps.parsing import extract_ad, parse_publish_time


class Command(BaseCommand):
    help = "Import publish-complete scraped ads from BAMA ADS/**/ads.json"

    def add_arguments(self, parser):
        parser.add_argument("--root", default=settings.BAMA_SCRAPED_DATA_ROOT)
        parser.add_argument("--limit", type=int, default=None, help="Stop after N files")
        parser.add_argument("--batch-size", type=int, default=200)

    def handle(self, *args, **opts):
        reset_cache()
        root = Path(opts["root"]).expanduser() / "BAMA ADS"
        if not root.exists():
            self.stderr.write(self.style.ERROR(f"Data root not found: {root}"))
            return

        files = sorted(root.rglob("ads.json"))
        if opts["limit"]:
            files = files[: opts["limit"]]
        self.stdout.write(f"Found {len(files)} ads.json files under {root}")

        run = FetchRun.objects.create(
            source=FetchRun.Source.BULK_IMPORT,
            status=FetchRun.Status.RUNNING,
            started_at=djtz.now(),
        )

        def record_unknown(phrase: str) -> None:
            obj, created = UnknownTimePhrase.objects.get_or_create(phrase=phrase)
            if not created:
                obj.seen_count = (obj.seen_count or 0) + 1
                obj.save(update_fields=["seen_count", "last_seen_at"])
            obj.last_fetch_run = run
            obj.save(update_fields=["last_fetch_run"])

        processed = 0
        try:
            for path in files:
                observed_at = datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                )
                try:
                    with open(path, encoding="utf-8-sig") as fh:
                        ads = json.load(fh)
                except (json.JSONDecodeError, OSError) as exc:
                    self.stderr.write(self.style.WARNING(f"skip unreadable {path}: {exc}"))
                    continue

                if not isinstance(ads, list):
                    continue

                with transaction.atomic():
                    for raw in ads:
                        extracted = extract_ad(raw, observed_at)
                        if not extracted:
                            run.skipped_count += 1
                            continue
                        publish_at = parse_publish_time(
                            extracted.get("publish_phrase"), observed_at, on_unknown=record_unknown
                        )
                        price = extracted.get("current_price")
                        if publish_at is None or not price or price <= 0:
                            run.skipped_count += 1
                            continue
                        _, created, price_changed = ingest_ad(
                            extracted,
                            run=run,
                            observed_at=observed_at,
                            publish_at=publish_at,
                            dealer=raw.get("dealer"),
                        )
                        run.fetched_count += 1
                        if created:
                            run.created_count += 1
                        else:
                            run.updated_count += 1
                        if price_changed:
                            run.price_change_count += 1
                        processed += 1
                        if processed % 1000 == 0:
                            run.save()
                            self.stdout.write(f"  {processed} ads processed...")

                run.save()
                if processed and processed % 5000 == 0:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  Ad={run.created_count + run.updated_count - 0} "
                            f"created={run.created_count} skipped={run.skipped_count}"
                        )
                    )

            run.status = FetchRun.Status.SUCCEEDED
        except Exception as exc:  # noqa: BLE001
            run.status = FetchRun.Status.FAILED
            run.error = str(exc)[:4000]
            self.stderr.write(self.style.ERROR(f"Import failed: {exc}"))
            raise
        finally:
            run.finished_at = djtz.now()
            run.save()
            reset_cache()

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. fetched={run.fetched_count} created={run.created_count} "
                f"updated={run.updated_count} skipped={run.skipped_count} "
                f"price_changes={run.price_change_count}"
            )
        )
