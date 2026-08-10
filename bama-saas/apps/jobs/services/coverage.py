"""Crawl-coverage arithmetic over :class:`PageCoverage`.

The Bama feed numbers ads 1..N by recency, so every fetched page is an
inclusive rank interval. Union those intervals and the holes are exactly the
ads nobody looked at in the window — the deletion case, where an ad removed
from the feed pulls its successors down into a rank range an earlier page
already claimed to have read.

Pure interval arithmetic on top of one queryset. No network, no writes.
"""

from __future__ import annotations

from datetime import datetime

from apps.core.models import FetchRun, PageCoverage

PAGE_SIZE = 30


def known_feed_depth() -> int | None:
    """Deepest rank reached by the most recent *completed* sweep.

    The natural ceiling for a gap search. Defaulting instead to "deepest rank
    seen in the window" makes a truncated sweep invisible: the sweep stops
    early, the window's deepest rank stops with it, and the missing tail sits
    below the ceiling where no gap can be reported. Anchoring on a run that
    provably reached the end of the feed keeps the tail in scope.

    Returns None when no sweep has ever completed — there is no defensible
    claim about feed depth then, and demanding coverage of an invented ceiling
    would schedule an endless backfill.
    """
    return (
        FetchRun.objects.filter(
            reached_end=True, status=FetchRun.Status.SUCCEEDED
        )
        .order_by("-started_at")
        .values_list("deepest_rank", flat=True)
        .first()
    )


def _merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping *and* adjacent inclusive integer intervals."""
    merged: list[tuple[int, int]] = []
    for lo, hi in sorted(intervals):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def find_gaps(
    since: datetime | None = None, max_rank: int | None = None
) -> list[tuple[int, int]]:
    """Rank ranges not covered by any PageCoverage row since ``since``.

    Returns sorted, merged, non-overlapping ``(rank_lo, rank_hi)`` tuples.
    ``max_rank`` defaults to the deepest rank seen in the window, so the result
    holds interior holes only; pass it explicitly to also demand tail coverage.
    """
    qs = PageCoverage.objects.all()
    if since is not None:
        qs = qs.filter(fetched_at__gte=since)
    covered = _merge(list(qs.values_list("rank_lo", "rank_hi")))
    if not covered:
        # Nothing observed in the window: everything up to max_rank is a gap,
        # but with no ceiling there is nothing meaningful to claim.
        return [(1, max_rank)] if max_rank else []

    ceiling = max_rank if max_rank is not None else covered[-1][1]
    gaps: list[tuple[int, int]] = []
    cursor = 1
    for lo, hi in covered:
        if lo > cursor:
            gaps.append((cursor, min(lo - 1, ceiling)))
        cursor = max(cursor, hi + 1)
        if cursor > ceiling:
            break
    if cursor <= ceiling:
        gaps.append((cursor, ceiling))
    return [(lo, hi) for lo, hi in gaps if lo <= hi]


def plan_backfill(
    gaps: list[tuple[int, int]], page_size: int = PAGE_SIZE
) -> list[tuple[int, int]]:
    """Turn rank gaps into inclusive ``(start_page, end_page)`` ranges to refetch.

    Rank ``r`` lives on page ``(r - 1) // page_size`` (pageIndex is 0-based).
    Adjacent page ranges collapse, so two neighbouring rank holes inside one
    page yield a single one-page refetch.
    """
    return _merge(
        [
            ((max(lo, 1) - 1) // page_size, (max(hi, 1) - 1) // page_size)
            for lo, hi in gaps
        ]
    )
