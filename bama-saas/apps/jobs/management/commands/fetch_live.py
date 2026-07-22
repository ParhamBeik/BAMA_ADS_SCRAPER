"""Fetch live Bama ads straight into Postgres via the ingest pipeline.

Thin wrapper around :func:`apps.jobs.services.fetcher.fetch_live`. Mirrors
``import_scraped``'s summary style; defaults come from the ``BAMA_*`` settings.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.jobs.services.fetcher import fetch_live


class Command(BaseCommand):
    help = "Stream live Bama ads from bama.ir into Postgres"

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            choices=["delta", "full"],
            default="delta",
            help="Ingestion mode: 'delta' (fast-delta with early stopping) or 'full' (full scan)",
        )
        parser.add_argument(
            "--max-stale-pages",
            type=int,
            default=None,
            help="Max consecutive pages with no new/updated ads before stopping in delta mode",
        )
        parser.add_argument(
            "--max-ads",
            type=int,
            default=settings.BAMA_MAX_ADS,
            help="Cap on total ads to ingest (default: BAMA_MAX_ADS)",
        )
        parser.add_argument(
            "--page-pause",
            type=float,
            default=settings.BAMA_PAGE_PAUSE,
            help="Seconds to sleep between pages (default: BAMA_PAGE_PAUSE)",
        )
        parser.add_argument(
            "--request-timeout",
            type=int,
            default=settings.BAMA_REQUEST_TIMEOUT,
            help="Per-request HTTP timeout in seconds (default: BAMA_REQUEST_TIMEOUT)",
        )


    def handle(self, *args, **opts):
        self.stdout.write(
            f"Starting live fetch [mode={opts['mode']}] (max_ads={opts['max_ads']}, "
            f"page_pause={opts['page_pause']}, "
            f"request_timeout={opts['request_timeout']})"
        )
        run = fetch_live(
            mode=opts["mode"],
            max_ads=opts["max_ads"],
            page_pause=opts["page_pause"],
            request_timeout=opts["request_timeout"],
            max_stale_pages=opts["max_stale_pages"],
        )

        affected = getattr(run, "affected_model_ids", set())
        if affected:
            from apps.core.services.deal_score import refresh_cohort_deal_scores
            res = refresh_cohort_deal_scores(affected)
            self.stdout.write(
                f"Refreshed deal scores for {res['refreshed_models']} model cohorts "
                f"({res['total_scored']} ads scored)."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. status={run.status} fetched={run.fetched_count} "
                f"created={run.created_count} updated={run.updated_count} "
                f"skipped={run.skipped_count} price_changes={run.price_change_count}"
            )
        )

