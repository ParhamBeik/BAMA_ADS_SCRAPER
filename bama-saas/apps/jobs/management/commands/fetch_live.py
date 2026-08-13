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
            choices=["delta", "full", "backfill"],
            default="delta",
            help=(
                "Ingestion mode: 'delta' (early-stopping sweep from page 0), "
                "'full' (page 0 to end of feed), or 'backfill' "
                "(explicit --start-page/--end-page range for gap repair)"
            ),
        )
        parser.add_argument(
            "--start-page",
            type=int,
            default=None,
            help="0-based first pageIndex (required for backfill; else resume/0)",
        )
        parser.add_argument(
            "--end-page",
            type=int,
            default=None,
            help="0-based last pageIndex, inclusive (backfill only)",
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
            f"Starting live fetch [mode={opts['mode']}] "
            f"(start_page={opts['start_page']}, end_page={opts['end_page']}, "
            f"max_ads={opts['max_ads']}, page_pause={opts['page_pause']}, "
            f"request_timeout={opts['request_timeout']})"
        )
        run = fetch_live(
            mode=opts["mode"],
            max_ads=opts["max_ads"],
            page_pause=opts["page_pause"],
            request_timeout=opts["request_timeout"],
            max_stale_pages=opts["max_stale_pages"],
            start_page=opts["start_page"],
            end_page=opts["end_page"],
        )

        # Deal scores: the HOT pipeline tick refreshes models sighted in this
        # run; the sweep does a full rebuild. Do not rescore here.
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. status={run.status} stop_reason={run.stop_reason} "
                f"pages_fetched={run.pages_fetched} deepest_rank={run.deepest_rank} "
                f"reached_end={run.reached_end} fetched={run.fetched_count} "
                f"created={run.created_count} updated={run.updated_count} "
                f"skipped={run.skipped_count} price_changes={run.price_change_count}"
            )
        )

