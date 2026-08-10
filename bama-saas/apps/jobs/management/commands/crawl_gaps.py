"""Refetch the feed ranges nobody covered in the recent window.

Deletions pull ads to lower ranks, behind pages a forward sweep already read,
so a page walk alone silently loses them. ``PageCoverage`` records which rank
ranges were actually read; this command turns the holes back into pages and
refetches them via ``fetch_live(mode="backfill", ...)``.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone as djtz

from apps.jobs.services.coverage import find_gaps, known_feed_depth, plan_backfill
from apps.jobs.services.fetcher import fetch_live


class Command(BaseCommand):
    help = "Find and refetch rank ranges the crawl missed"

    def add_arguments(self, parser):
        parser.add_argument(
            "--since-hours",
            type=float,
            default=24.0,
            help="Only count coverage newer than this many hours (default: 24)",
        )
        parser.add_argument(
            "--max-ranges",
            type=int,
            default=5,
            help="Cap page ranges repaired per invocation (default: 5)",
        )
        parser.add_argument(
            "--max-rank",
            type=int,
            default=None,
            help=(
                "Demand coverage up to this rank. Default: the deepest rank of "
                "the last completed sweep, which is what makes a truncated "
                "sweep visible as a gap."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the plan without fetching anything",
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
        since = djtz.now() - timedelta(hours=opts["since_hours"])
        max_rank = opts["max_rank"]
        if max_rank is None:
            max_rank = known_feed_depth()
            if max_rank:
                self.stdout.write(
                    f"Demanding coverage to rank {max_rank} "
                    f"(deepest rank of the last completed sweep)."
                )
        gaps = find_gaps(since=since, max_rank=max_rank)
        if not gaps:
            self.stdout.write(
                self.style.SUCCESS(
                    f"No coverage gaps in the last {opts['since_hours']:g}h."
                )
            )
            return

        ranges = plan_backfill(gaps)
        self.stdout.write(
            f"{len(gaps)} rank gap(s) in the last {opts['since_hours']:g}h "
            f"-> {len(ranges)} page range(s): "
            + ", ".join(f"{lo}-{hi}" for lo, hi in ranges)
        )

        limit = opts["max_ranges"]
        skipped = ranges[limit:]
        if skipped:
            self.stdout.write(
                self.style.WARNING(
                    f"--max-ranges={limit}: skipping {len(skipped)} range(s) "
                    + ", ".join(f"{lo}-{hi}" for lo, hi in skipped)
                    + " — rerun to pick them up."
                )
            )

        if opts["dry_run"]:
            self.stdout.write("Dry run: nothing fetched.")
            return

        total_pages = 0
        affected: set[int] = set()
        for start, end in ranges[:limit]:
            run = fetch_live(
                mode="backfill",
                start_page=start,
                end_page=end,
                page_pause=opts["page_pause"],
                request_timeout=opts["request_timeout"],
            )
            total_pages += run.pages_fetched
            affected |= getattr(run, "affected_model_ids", set())
            self.stdout.write(
                f"  pages {start}-{end}: status={run.status} "
                f"pages_fetched={run.pages_fetched} fetched={run.fetched_count} "
                f"created={run.created_count} stop_reason={run.stop_reason}"
            )

        if affected:
            from apps.core.services.deal_score import refresh_cohort_deal_scores

            res = refresh_cohort_deal_scores(affected)
            self.stdout.write(
                f"Refreshed deal scores for {res['refreshed_models']} model cohorts "
                f"({res['total_scored']} ads scored)."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. repaired={min(len(ranges), limit)} range(s), "
                f"{total_pages} page(s) refetched."
            )
        )
