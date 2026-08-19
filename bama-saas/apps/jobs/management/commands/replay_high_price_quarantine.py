"""Restore the latest payload for each listing blocked by the retired price cap."""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import FetchRun, IngestReject
from apps.core.services.deal_score import refresh_cohort_deal_scores
from apps.jobs.services.ingest import ingest_ad, reset_price_cache
from apps.parsing import extract_ad, parse_publish_time


class Command(BaseCommand):
    help = "Replay the latest payload for each listing quarantined only for high price"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the distinct listing count without writing data.",
        )
        parser.add_argument(
            "--since-hours",
            type=float,
            default=24.0,
            help="Replay listings seen in this recent window (default: 24).",
        )

    def handle(self, *args, **opts):
        since = timezone.now() - timedelta(hours=opts["since_hours"])
        rejects = (
            IngestReject.objects.filter(
                rule="price_too_high", observed_at__gte=since,
            )
            .exclude(code="")
            .order_by("code", "-observed_at")
            .distinct("code")
        )
        total = rejects.count()
        if opts["dry_run"]:
            self.stdout.write(
                f"Would replay {total} distinct listing(s) from the last "
                f"{opts['since_hours']:g} hour(s)."
            )
            return

        run = FetchRun.objects.create(
            source=FetchRun.Source.HISTORY_REPLAY,
            status=FetchRun.Status.RUNNING,
            mode=FetchRun.Mode.BACKFILL,
            max_ads=total,
            started_at=timezone.now(),
        )
        reset_price_cache()
        affected: set[int] = set()
        try:
            for reject in rejects.iterator(chunk_size=500):
                observed_at = reject.observed_at
                payload = reject.raw_payload or {}
                extracted = extract_ad(payload, observed_at)
                if not extracted:
                    run.skipped_count += 1
                    continue
                result = ingest_ad(
                    extracted,
                    run=run,
                    observed_at=observed_at,
                    publish_at=parse_publish_time(
                        extracted.get("publish_phrase"), observed_at,
                    ),
                    dealer=payload.get("dealer"),
                )
                if result.ad is None:
                    run.skipped_count += 1
                    continue
                run.fetched_count += 1
                run.created_count += int(result.created)
                run.updated_count += int(not result.created)
                run.price_change_count += int(result.price_changed)
                if result.cohort:
                    affected.add(result.cohort[0])

            run.status = FetchRun.Status.SUCCEEDED
            run.stop_reason = FetchRun.StopReason.MAX_ADS
        except Exception as exc:  # noqa: BLE001
            run.status = FetchRun.Status.FAILED
            run.stop_reason = FetchRun.StopReason.ERROR
            run.error = str(exc)[:4000]
            raise
        finally:
            run.finished_at = timezone.now()
            run.save()
            reset_price_cache()

        scores = refresh_cohort_deal_scores(affected)
        self.stdout.write(
            self.style.SUCCESS(
                f"Replayed {run.fetched_count}/{total} listing(s): "
                f"created={run.created_count} updated={run.updated_count} "
                f"skipped={run.skipped_count}; "
                f"outlier-aware cohorts={scores['refreshed_models']}."
            )
        )
