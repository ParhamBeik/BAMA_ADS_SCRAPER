"""What the recorded crawl says about itself: coverage, depth, and gaps.

Pure arithmetic over ``PageCoverage`` and ``FetchRun`` — no network, no writes.
It lives in ``apps.core`` because those are core's own tables and because both
sides read it: ``apps.jobs`` to decide whether removal detection may run, and
``apps.core.views`` to badge every research answer with the coverage that
qualifies it. Holding it in the crawler meant the read API imported the crawler.

Every fetched page is an inclusive rank interval. Union those intervals and the
holes are exactly the ads nobody looked at in the window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Max, Q
from django.utils import timezone as djtz

from apps.common.parsing import PAGE_SIZE
from apps.core.models import FetchRun, PageCoverage

# How far back the depth ratchet looks. Long enough that a truncated run cannot
# lower the ceiling, short enough that one absurd rank_hi from a bug ages out
# instead of poisoning it forever — a permanently inflated ceiling means
# coverage is never "complete", which silently disables removal detection.
FEED_DEPTH_WINDOW_DAYS = 30

# One coverage pass. Coverage is judged over windows of this length rather than
# per-run, so it does not matter which run covered which page.
COVERAGE_WINDOW_HOURS = 24

# How stale a rank may get before the coverage job goes and re-reads it.
#
# Deliberately shorter than the window it has to satisfy, and that gap is the
# point. Planning refreshes against the same 24h it is judged on made the deep
# sweep a sawtooth with no slack: a rank became "due" at the exact moment it
# stopped counting as covered, so ~700 pages of tail fell due at once and the
# window was already broken before the first page of repair was fetched.
# Production ran that way for 13 hours on 2026-09-05 — one burst of deep pages
# at 15:00-18:00, then nothing below rank 1,500 until the next burst.
#
# Six hours of headroom against a job that walks the whole tail in about one
# hour (120 pages a tick, a tick every 10 minutes) is a 6x margin, so an outage
# the length of the one on 2026-09-06 no longer costs a window. It does not
# de-burst the work — the tail still comes due together — but it stops the burst
# from racing a deadline it has already missed.
COVERAGE_REFRESH_HOURS = 18.0


def known_feed_depth(as_of: datetime | None = None) -> int | None:
    """Deepest rank any page covered recently, capped by the last real end-of-feed.

    ``as_of`` answers the question as it stood at a past instant, ignoring
    everything learned since. That is what lets a *closed* coverage window be
    judged against the feed it was actually walking — see ``coverage_is_complete``,
    where using today's answer for yesterday's window silently disabled removal
    detection every single time the feed grew.

    A max over accumulated ``PageCoverage`` needs no run to survive start to
    finish: three interrupted sweeps that jointly walk the feed give the same
    ceiling as one clean one. The old rule ("deepest rank of the last sweep that
    set reached_end") made the ceiling hostage to one uninterrupted ~936-page
    walk against a host that answers 503 — 11 of 28 sweeps completed, so for
    long stretches there was no ceiling and removal detection stalled.

    The cap matters because a ratchet is one-way and the feed shrinks: it
    reached rank 34,107 and a day later ended at ~33,112. Uncapped, coverage
    would be demanded for ~1,000 ranks that no longer exist. The most recent
    authoritative statement wins — a plain min would let a stale end-of-feed
    hide a tail that has since grown.

    The cap deliberately accepts a credible end-of-feed from ANY mode, not only
    a full sweep. Requiring ``mode=FULL`` looked safe and was the bug: rolling
    coverage replaced the full sweep entirely, so no run could ever lower the
    ceiling again. Over one week production logged 624 backfills that reached the
    real end of the feed and 0 full sweeps, so the ratchet stayed pinned at a
    high-water mark from days earlier, ~50 ranks of it referring to ads that no
    longer existed. Coverage could never be complete, and removal detection was
    silently disabled for over 24 hours. The credibility check in
    ``end_of_feed_is_credible`` is what keeps this honest — a shallow delta that
    hits an empty page never sets ``reached_end``.
    """
    now = as_of or djtz.now()
    since = now - timedelta(days=FEED_DEPTH_WINDOW_DAYS)
    covered = PageCoverage.objects.filter(fetched_at__gte=since)
    ends = FetchRun.objects.filter(
        stop_reason=FetchRun.StopReason.END_OF_FEED,
        reached_end=True, status=FetchRun.Status.SUCCEEDED,
        started_at__gte=since,
    )
    if as_of is not None:
        covered = covered.filter(fetched_at__lt=as_of)
        ends = ends.filter(started_at__lt=as_of)

    ratchet = covered.aggregate(depth=Max("rank_hi"))["depth"]
    if not ratchet:
        return None

    last_end = (
        # feed_end_rank is the honest bound; deepest_rank is the fallback for
        # runs recorded before that column existed.
        ends.filter(Q(feed_end_rank__isnull=False) | Q(deepest_rank__isnull=False))
        .order_by("-started_at")
        .values("started_at", "feed_end_rank", "deepest_rank").first()
    )
    if not last_end:
        return ratchet
    ceiling = last_end["feed_end_rank"]
    if ceiling is None:
        ceiling = last_end["deepest_rank"]
    deeper_since = covered.filter(
        fetched_at__gte=last_end["started_at"]
    ).aggregate(depth=Max("rank_hi"))["depth"]
    return max(ceiling, deeper_since or 0)


def _merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping *and* adjacent inclusive integer intervals."""
    merged: list[tuple[int, int]] = []
    for lo, hi in sorted(intervals):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def find_gaps(since: datetime | None = None, max_rank: int | None = None,
              until: datetime | None = None) -> list[tuple[int, int]]:
    """Rank ranges no PageCoverage row covers in the window, sorted and merged.

    ``max_rank`` defaults to the deepest rank seen in the window, so the result
    holds interior holes only; pass it to also demand tail coverage. ``until``
    bounds the window on the right, which is what lets a caller ask "was the
    feed fully covered in the window *before* this one".
    """
    qs = PageCoverage.objects.all()
    if since is not None:
        qs = qs.filter(fetched_at__gte=since)
    if until is not None:
        qs = qs.filter(fetched_at__lt=until)
    covered = _merge(list(qs.values_list("rank_lo", "rank_hi")))
    if not covered:
        # Nothing observed: everything up to max_rank is a gap, but with no
        # ceiling there is nothing meaningful to claim.
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


# How many uncovered ranks a window may still hold and count as swept.
#
# Demanding literally zero was too strict to ever hold. The feed is strictly
# recency-ordered and shifts under the crawler continuously, so two pages read
# seconds apart can leave a handful of ranks that neither one claimed — and one
# such hole, six ranks wide against a ~28,700-rank feed, switched removal
# detection off entirely. Production flip-flopped between "986 ads marked
# REMOVED" and "cannot prove an ad is gone" inside sixteen minutes, and the
# active-ad count drifted upward for as long as it was off.
#
# One page. An ad can only hide from a sweep by sitting in an uncovered range
# for the *whole* window; a range this small is one the constant reshuffling
# fills in on the next pass, which is why the tolerance is a page and not a
# percentage — a ratio of a 1,000-page feed would be dozens of pages, and a real
# multi-page hole is exactly the evidence this check exists to respect.
COVERAGE_GAP_TOLERANCE_RANKS = PAGE_SIZE


def uncovered_ranks(gaps: list[tuple[int, int]]) -> int:
    """How many ranks the gaps add up to. One definition for every reader."""
    return sum(hi - lo + 1 for lo, hi in gaps)


def coverage_is_complete(since: datetime, until: datetime | None = None) -> bool:
    """True when the window covered the feed to within ``COVERAGE_GAP_TOLERANCE_RANKS``.

    With no known depth there is nothing to prove against, so this returns False
    — callers must fail closed. So does a window with no coverage at all: the
    whole feed is then one gap, which is far past the tolerance.

    The ceiling is the depth known *at the end of the window*, not the depth
    known now, and that distinction is the whole reason removal detection kept
    dying. The feed grows continuously. Judged against today's ceiling, a window
    that closed yesterday is retroactively guilty of not covering ads that did
    not exist while it was open: measured in production 2026-09-06, the window
    24-48h back had walked the feed to rank 20,846 — every rank there was — and
    was failed for leaving (20847, 20910) uncovered, 64 ranks the feed grew
    afterwards. Two pages over a 30-rank tolerance, so ``mark_inactive`` refused
    to mark anything for 14 hours and 918 ads sat in UNVERIFIED. Every time the
    feed grows, one window fails; windows are needed in consecutive pairs, so a
    growing feed switched the whole mechanism off permanently.
    """
    depth = known_feed_depth(as_of=until)
    if not depth:
        return False
    return uncovered_ranks(find_gaps(since=since, until=until, max_rank=depth)) \
        <= COVERAGE_GAP_TOLERANCE_RANKS


@dataclass(frozen=True)
class CoverageState:
    """How well the recent window covered the feed, as one answer.

    ``depth`` is whatever ``known_feed_depth`` returned, passed through
    unchanged so a caller that merely reports it says exactly what it used to.
    When it is falsy nothing can be proven, so ``complete`` is False and the
    caller fails closed — the same rule ``coverage_is_complete`` follows.
    """

    depth: int | None
    gaps: list[tuple[int, int]]
    missing: int
    complete: bool


def coverage_state(now: datetime | None = None) -> CoverageState:
    """Depth, gaps, uncovered ranks and the completeness verdict, computed once.

    This chain — ``known_feed_depth`` then ``find_gaps`` over
    ``COVERAGE_WINDOW_HOURS`` then ``uncovered_ranks`` then compare to
    ``COVERAGE_GAP_TOLERANCE_RANKS`` — was written out in three places: the
    provenance envelope on every research answer, the ``sweep_freshness`` health
    check, and the operator health screen. The first two carried comments saying
    they must agree with what ``mark_inactive`` acts on, and that agreement was
    being maintained by hand across an app boundary.

    The verdict is deliberately the same slack ``mark_inactive`` uses and not a
    stricter one: reporting "no complete sweep" off a hole the worker itself
    tolerates put a permanent warning strip on every screen.
    """
    now = now or djtz.now()
    depth = known_feed_depth()
    if not depth:
        return CoverageState(depth=depth, gaps=[], missing=0, complete=False)
    gaps = find_gaps(since=now - timedelta(hours=COVERAGE_WINDOW_HOURS), max_rank=depth)
    missing = uncovered_ranks(gaps)
    return CoverageState(depth=depth, gaps=gaps, missing=missing,
                         complete=missing <= COVERAGE_GAP_TOLERANCE_RANKS)


def plan_backfill(gaps: list[tuple[int, int]],
                  page_size: int = PAGE_SIZE) -> list[tuple[int, int]]:
    """Turn rank gaps into inclusive ``(start_page, end_page)`` ranges.

    Rank ``r`` lives on page ``(r - 1) // page_size`` (pageIndex is 0-based).
    Adjacent ranges collapse, so two neighbouring holes inside one page yield a
    single one-page refetch.
    """
    return _merge([
        ((max(lo, 1) - 1) // page_size, (max(hi, 1) - 1) // page_size) for lo, hi in gaps
    ])


# ---------------------------------------------------------------------------
# Whether bama.ir is refusing us
# ---------------------------------------------------------------------------
#
# Here rather than with the crawl gate that acts on it, for the same reason as
# the coverage arithmetic above: this reads FetchRun, which is a core table, and
# both sides ask the question. apps/jobs uses it to decide whether to fetch at
# all; apps/core reports it on the provenance envelope, because a source that is
# refusing us is the *cause* of the staleness a reader is looking at, and it
# implies removal detection is paused — so a listing shown as active may be sold.

# Longer than the max cooldown, so a streak survives its own quiet period.
STREAK_LOOKBACK = timedelta(hours=48)


def recent_runs(limit: int = 40):
    return list(
        FetchRun.objects.filter(
            source__in=(FetchRun.Source.LIVE_FETCH, FetchRun.Source.SOLD_PROBE),
            created_at__gte=djtz.now() - STREAK_LOOKBACK,
        )
        .order_by("-created_at")
        .values("status", "stop_reason", "finished_at", "started_at", "created_at")[:limit]
    )


def consecutive_blocks() -> int:
    """How many runs in a row ended blocked, counting back from the newest.

    Counts across modes: a blocked delta and a blocked backfill are the same ban,
    and separating them would let two schedules each probe at full rate.
    """
    streak = 0
    for run in recent_runs():
        if run["stop_reason"] == FetchRun.StopReason.BLOCKED:
            streak += 1
        elif run["status"] == FetchRun.Status.RUNNING:
            # The in-flight run asking this question. Ignore it rather than
            # letting it break its own streak.
            continue
        else:
            break
    return streak
