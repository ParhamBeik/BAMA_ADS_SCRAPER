"""Coverage-gap arithmetic over PageCoverage.

Integration tests: find_gaps is a queryset over real rows, so the DB is the
component boundary being exercised. plan_backfill is pure and rides along.
"""

from datetime import timedelta

import pytest
from django.utils import timezone as djtz

from apps.core.models import FetchRun, PageCoverage
from apps.jobs.fetcher import find_gaps, plan_backfill


def _run():
    return FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH,
        status=FetchRun.Status.SUCCEEDED,
        mode=FetchRun.Mode.DELTA,
    )


def _cover(run, pages, *, page_size=30, fetched_at=None):
    """Write one PageCoverage row per page index in ``pages``."""
    at = fetched_at or djtz.now()
    for page in pages:
        PageCoverage.objects.create(
            fetch_run=run,
            page_index=page,
            rank_lo=page_size * page + 1,
            rank_hi=page_size * (page + 1),
            ad_count=page_size,
            fetched_at=at,
        )


@pytest.mark.django_db
def test_no_gap_when_pages_are_contiguous():
    _cover(_run(), range(0, 5))
    assert find_gaps() == []


@pytest.mark.django_db
def test_single_hole_is_found_and_planned():
    # Pages 0,1 and 4,5 read; pages 2 and 3 (ranks 61..120) never were.
    _cover(_run(), [0, 1, 4, 5])
    assert find_gaps() == [(61, 120)]
    assert plan_backfill(find_gaps()) == [(2, 3)]


@pytest.mark.django_db
def test_leading_hole_when_page_zero_was_skipped():
    """The 0-based-pageIndex bug in raw form: page 0 never read."""
    _cover(_run(), [1, 2, 3])
    assert find_gaps() == [(1, 30)]
    assert plan_backfill(find_gaps()) == [(0, 0)]


@pytest.mark.django_db
def test_two_holes_stay_separate():
    _cover(_run(), [0, 2, 4])
    assert find_gaps() == [(31, 60), (91, 120)]
    assert plan_backfill(find_gaps()) == [(1, 1), (3, 3)]


@pytest.mark.django_db
def test_adjacent_gaps_merge_into_one_page_range():
    """Two rank holes landing on neighbouring pages collapse to one refetch."""
    run = _run()
    _cover(run, [0])
    # Hand-rolled partial coverage: ranks 31..40 and 51..60 seen, 41..50 and
    # 61..90 missing -> two rank gaps that are adjacent once mapped to pages.
    PageCoverage.objects.create(
        fetch_run=run, page_index=1, rank_lo=31, rank_hi=40, ad_count=10,
        fetched_at=djtz.now(),
    )
    PageCoverage.objects.create(
        fetch_run=run, page_index=3, rank_lo=91, rank_hi=120, ad_count=30,
        fetched_at=djtz.now(),
    )
    assert find_gaps() == [(41, 90)]
    # ranks 41..90 span pages 1 and 2 -> a single contiguous refetch range.
    assert plan_backfill(find_gaps()) == [(1, 2)]

    # Separate gaps mapping onto adjacent pages merge too.
    assert plan_backfill([(31, 40), (61, 70)]) == [(1, 2)]


@pytest.mark.django_db
def test_since_window_excludes_stale_coverage():
    old = _run()
    _cover(old, [0, 1, 2], fetched_at=djtz.now() - timedelta(hours=48))
    fresh = _run()
    _cover(fresh, [0, 2])

    since = djtz.now() - timedelta(hours=24)
    assert find_gaps(since=since) == [(31, 60)]
    # Without the window everything is covered.
    assert find_gaps() == []


@pytest.mark.django_db
def test_max_rank_demands_tail_coverage():
    _cover(_run(), [0, 1])
    assert find_gaps() == []
    assert find_gaps(max_rank=150) == [(61, 150)]


@pytest.mark.django_db
def test_empty_coverage():
    assert find_gaps() == []
    assert find_gaps(max_rank=60) == [(1, 60)]
    assert plan_backfill([]) == []
