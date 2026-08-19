"""Refetch the feed ranges nobody covered in the recent window.

Deletions pull ads to lower ranks, behind pages a forward sweep already read,
so a page walk alone silently loses them. ``PageCoverage`` records which rank
ranges were actually read; this command turns the holes back into pages and
refetches them via ``fetch_live(mode="backfill", ...)``.

This is also how full coverage is now achieved at all. There is no separate
all-or-nothing sweep: the deep tail simply shows up as a gap once it ages out
of the coverage window, and each tick walks a bounded chunk of it. Coverage
therefore accumulates across many short runs, none of which has to survive
start to finish — the property that one ~936-page sweep never had (it completed
11 times in 28 attempts, which is why removal detection stalled for days).
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone as djtz

from apps.jobs.services.coverage import (
    PAGE_SIZE,
    find_gaps,
    known_feed_depth,
    plan_backfill,
)
from apps.jobs.services.crawl_gate import CrawlBlocked
from apps.jobs.services.fetcher import fetch_live


def _budgeted(
    ranges: list[tuple[int, int]], budget: int
) -> list[tuple[int, int]]:
    """Trim page ranges to at most ``budget`` pages, truncating the last one.

    Capping by *range count* (the old ``--max-ranges``) bounded nothing: one
    range can be the entire 900-page tail, so a "capped" repair was still an
    unbounded run. Whatever is left over is simply still a gap next tick.
    """
    if budget <= 0:
        return []
    out: list[tuple[int, int]] = []
    remaining = budget
    for lo, hi in ranges:
        if remaining <= 0:
            break
        span = hi - lo + 1
        if span <= remaining:
            out.append((lo, hi))
            remaining -= span
        else:
            out.append((lo, lo + remaining - 1))
            remaining = 0
    return out


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
            "--max-pages",
            type=int,
            default=settings.BAMA_COVERAGE_CHUNK_PAGES,
            help=(
                "Page budget per invocation (default: BAMA_COVERAGE_CHUNK_PAGES). "
                "Bounds each tick so a run is short enough to finish."
            ),
        )
        parser.add_argument(
            "--max-rank",
            type=int,
            default=None,
            help=(
                "Demand coverage up to this rank. Default: the deepest rank any "
                "page reached in the depth window, which makes an un-refreshed "
                "tail visible as a gap."
            ),
        )
        parser.add_argument(
            "--probe-pages",
            type=int,
            default=5,
            help=(
                "When there are no gaps, walk this many pages past the known "
                "ceiling to notice the feed growing (default: 5)."
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
        budget = opts["max_pages"]
        max_rank = opts["max_rank"]
        if max_rank is None:
            max_rank = known_feed_depth()
            if max_rank:
                self.stdout.write(
                    f"Demanding coverage to rank {max_rank} "
                    f"(deepest rank reached in the depth window)."
                )
        gaps = find_gaps(since=since, max_rank=max_rank)

        if gaps:
            ranges = _budgeted(plan_backfill(gaps), budget)
            self.stdout.write(
                f"{len(gaps)} rank gap(s) in the last {opts['since_hours']:g}h "
                f"-> fetching {sum(hi - lo + 1 for lo, hi in ranges)} page(s) "
                f"(budget {budget}): "
                + ", ".join(f"{lo}-{hi}" for lo, hi in ranges)
            )
        else:
            # Coverage is complete, so the only thing left worth learning is
            # whether the feed got deeper. Walking a few pages past the ceiling
            # is how the ratchet grows; without it a growing feed would stay
            # permanently invisible below a stale ceiling.
            probe = opts["probe_pages"]
            if not probe or max_rank is None:
                self.stdout.write(self.style.SUCCESS(
                    f"No coverage gaps in the last {opts['since_hours']:g}h."
                ))
                return
            # Pages are 0-based (fetcher.py) and rank r lives on page
            # (r-1)//PAGE_SIZE (coverage.plan_backfill), so the next unread
            # page after max_rank is max_rank // PAGE_SIZE, not +1 — the old
            # +1 skipped a full page on every probe.
            next_page = max_rank // PAGE_SIZE
            ranges = [(next_page, next_page + probe - 1)]
            self.stdout.write(
                f"No gaps in the last {opts['since_hours']:g}h; probing pages "
                f"{ranges[0][0]}-{ranges[0][1]} past the ceiling."
            )

        if opts["dry_run"]:
            self.stdout.write("Dry run: nothing fetched.")
            return

        total_pages = 0
        affected: set[int] = set()
        for start, end in ranges:
            try:
                run = fetch_live(
                    mode="backfill",
                    start_page=start,
                    end_page=end,
                    page_pause=opts["page_pause"],
                    request_timeout=opts["request_timeout"],
                )
            except CrawlBlocked as exc:
                # Stop the whole loop, not just this range: the gate is global,
                # so every remaining range would raise the same thing. The gaps
                # stay uncovered and are re-derived on the next tick.
                self.stdout.write(self.style.WARNING(f"Crawl gated: {exc}"))
                break
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
                f"Done. {len(ranges)} range(s), {total_pages} page(s) fetched."
            )
        )
