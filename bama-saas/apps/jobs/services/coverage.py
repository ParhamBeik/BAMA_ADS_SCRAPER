"""Crawl-coverage arithmetic over :class:`PageCoverage`.

The Bama feed numbers ads 1..N by recency, so every fetched page is an
inclusive rank interval. Union those intervals and the holes are exactly the
ads nobody looked at in the window — the deletion case, where an ad removed
from the feed pulls its successors down into a rank range an earlier page
already claimed to have read.

Pure interval arithmetic on top of one queryset. No network, no writes.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db.models import Max
from django.utils import timezone

from apps.core.models import FetchRun, PageCoverage

PAGE_SIZE = 30

# How far back the depth ratchet looks. Long enough that a truncated run cannot
# lower the ceiling (older, deeper coverage is still in scope), short enough
# that one absurd rank_hi from a bug ages out instead of poisoning the ceiling
# forever — a permanently inflated ceiling means coverage is never "complete",
# which silently disables removal detection.
FEED_DEPTH_WINDOW_DAYS = 30

# One coverage pass. Coverage is judged over windows of this length rather than
# per-run, so it does not matter which run covered which page.
COVERAGE_WINDOW_HOURS = 24


def known_feed_depth() -> int | None:
    """Deepest rank any page has covered in the recent window.

    This used to be "deepest rank of the last sweep that set ``reached_end``",
    which made the ceiling hostage to one uninterrupted 20-minute walk of ~936
    pages against a host that answers 503. Measured over 39 days: 11 of 28 full
    sweeps completed, so for long stretches there was no ceiling at all, no gap
    could be reported, and removal detection stalled.

    A max over accumulated ``PageCoverage`` needs no run to survive start to
    finish: three interrupted sweeps that jointly walk the feed give the same
    ceiling as one clean one. It cannot be lowered by a truncated run, because
    deeper coverage from earlier in the window is still counted.

    Returns None when nothing has been fetched in the window.

    The ratchet is capped by the most recent run that walked off the end of the
    feed, because a ratchet alone is one-way and the feed shrinks: it reached
    rank 34,107 and a day later ended at ~33,112. Left uncapped, coverage would
    be demanded for ~1,000 ranks that no longer exist, no window could ever be
    complete, and removal detection would stay switched off indefinitely — the
    same stall this whole redesign exists to remove, reintroduced from the other
    side. ``end_of_feed`` is only recorded when ``end_of_feed_is_credible``
    agrees, so a blocked crawl returning early empties cannot shrink the ceiling.
    """
    since = timezone.now() - timedelta(days=FEED_DEPTH_WINDOW_DAYS)
    covered = PageCoverage.objects.filter(fetched_at__gte=since)
    ratchet = covered.aggregate(depth=Max("rank_hi"))["depth"]
    if not ratchet:
        return None

    last_end = (
        FetchRun.objects.filter(
            stop_reason=FetchRun.StopReason.END_OF_FEED,
            mode=FetchRun.Mode.FULL,
            reached_end=True,
            status=FetchRun.Status.SUCCEEDED,
            started_at__gte=since,
            deepest_rank__isnull=False,
        )
        .order_by("-started_at")
        .values("started_at", "deepest_rank")
        .first()
    )
    if not last_end:
        return ratchet

    # The most recent authoritative statement about the feed's end wins. The
    # end-of-feed run says "it stops here"; any page fetched *after* it that
    # reached deeper says "no, it goes at least this far". Taking a plain
    # minimum would let a stale end-of-feed hide a tail that has since grown,
    # and taking a plain maximum leaves the ratchet demanding ranks that no
    # longer exist.
    deeper_since = covered.filter(
        fetched_at__gte=last_end["started_at"]
    ).aggregate(depth=Max("rank_hi"))["depth"]
    return max(last_end["deepest_rank"], deeper_since or 0)


def coverage_is_complete(
    since: datetime, until: datetime | None = None
) -> bool:
    """True when every rank up to the known feed depth was fetched in the window.

    The union of page ranges is the proof, so any mix of delta, backfill and
    partial sweeps counts. With no known depth there is nothing to prove
    against, and this returns False — callers must fail closed.
    """
    depth = known_feed_depth()
    if not depth:
        return False
    return not find_gaps(since=since, until=until, max_rank=depth)


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
    since: datetime | None = None,
    max_rank: int | None = None,
    until: datetime | None = None,
) -> list[tuple[int, int]]:
    """Rank ranges not covered by any PageCoverage row in the window.

    Returns sorted, merged, non-overlapping ``(rank_lo, rank_hi)`` tuples.
    ``max_rank`` defaults to the deepest rank seen in the window, so the result
    holds interior holes only; pass it explicitly to also demand tail coverage.
    ``until`` bounds the window on the right, which is what lets a caller ask
    "was the feed fully covered in the window *before* this one".
    """
    qs = PageCoverage.objects.all()
    if since is not None:
        qs = qs.filter(fetched_at__gte=since)
    if until is not None:
        qs = qs.filter(fetched_at__lt=until)
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
