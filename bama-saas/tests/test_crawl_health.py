"""Crawl-health checks and the empty-page truncation guard.

The health checks are unit-level (aggregate queries over seeded rows). The
empty-page guard is tested at the ``session.get`` boundary, matching the
convention ``test_fetcher_pagination.py`` established deliberately: mocking
``fetch_page`` instead once let a 0-based-``pageIndex`` bug survive, so the
page-index arithmetic must stay inside the system under test.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.utils import timezone

from apps.core.models import FetchRun, IngestReject, PageCoverage
from apps.jobs.services import fetcher
from apps.jobs.services.coverage import known_feed_depth
from apps.jobs.services.health import (
    REJECT_SPIKE_MIN_COUNT,
    check_failed_runs,
    check_ingest_progress,
    check_reject_spike,
    check_sweep_freshness,
    run_checks,
)

NOW = timezone.now()


def _run(**kw):
    kw.setdefault("source", FetchRun.Source.LIVE_FETCH)
    kw.setdefault("status", FetchRun.Status.SUCCEEDED)
    kw.setdefault("started_at", NOW)
    return FetchRun.objects.create(**kw)


def _cover(lo, hi, at=None, run=None):
    """Record that ranks ``lo..hi`` were fetched at ``at``."""
    at = at or NOW
    run = run or _run(started_at=at)
    PageCoverage.objects.create(
        fetch_run=run, page_index=(lo - 1) // 30, rank_lo=lo, rank_hi=hi,
        ad_count=hi - lo + 1, new_count=0, changed_count=0, fetched_at=at,
    )
    return run


# ---------------------------------------------------------------------------
# Empty page must be confirmed before it is believed to be the feed's end
# ---------------------------------------------------------------------------

def _response(ads):
    r = Mock()
    r.json.return_value = {"data": {"ads": ads}}
    r.raise_for_status.return_value = None
    return r


def _ad(i):
    return {"detail": {"code": f"code{i:04d}", "rank": i}}


def test_transient_empty_page_does_not_end_the_feed(monkeypatch):
    """One blank response mid-sweep must not truncate the crawl.

    A throttled API returns 200 with an empty ad list, byte-identical to the
    real end of the feed. Believing it both stops the sweep early and stamps
    reached_end, so the tail is never revisited.
    """
    monkeypatch.setattr(fetcher.time, "sleep", lambda *_: None)
    pages = [
        [_ad(1), _ad(2)],   # page 0
        [],                 # page 1 — transient blank
        [_ad(3), _ad(4)],   # page 1 again on the confirming retry
        [],                 # page 2 — genuinely empty
        [],                 # page 2 confirmed
    ]
    session = Mock()
    session.get.side_effect = [_response(p) for p in pages]

    seen = list(fetcher.iter_pages(
        session, max_ads=100, page_pause=0, request_timeout=5,
    ))

    # page 0 (2 ads), page 1 (2 ads after retry), page 2 (empty => end)
    assert [len(rows) for _, rows in seen] == [2, 2, 0]
    assert seen[-1][1] == []


def test_genuine_end_of_feed_still_terminates(monkeypatch):
    """The confirming retry must not stop a real end from being detected."""
    monkeypatch.setattr(fetcher.time, "sleep", lambda *_: None)
    session = Mock()
    session.get.side_effect = [
        _response([_ad(1)]),
        _response([]),   # page 1 empty
        _response([]),   # confirmed empty
    ]

    seen = list(fetcher.iter_pages(
        session, max_ads=100, page_pause=0, request_timeout=5,
    ))
    assert [len(rows) for _, rows in seen] == [1, 0]


# ---------------------------------------------------------------------------
# Gap ceiling
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_known_feed_depth_is_the_deepest_rank_ever_covered():
    """The ratchet takes the deepest rank *any* run reached in the window.

    No run has to have walked the feed end to end: two partial runs that each
    stopped early still establish the ceiling between them.
    """
    _cover(1, 500, at=NOW - timedelta(hours=6))
    _cover(501, 28000, at=NOW - timedelta(hours=2))
    assert known_feed_depth() == 28000


@pytest.mark.django_db
def test_known_feed_depth_is_not_lowered_by_a_truncated_run():
    """A shallow recent run must not drag the ceiling down and hide the tail."""
    _cover(1, 28000, at=NOW - timedelta(days=2))
    _cover(1, 60, at=NOW)
    assert known_feed_depth() == 28000


@pytest.mark.django_db
def test_a_shrinking_feed_lowers_the_ceiling():
    """The ratchet must not outlive the feed it measured.

    The feed reached rank 34,107 and a day later ended at ~33,112. A one-way
    ratchet keeps demanding coverage of ranks that no longer exist, so no window
    is ever complete and removal detection stays switched off — the exact stall
    the coverage rewrite exists to remove, arriving from the other side. A run
    that walks off the end of the feed is what proves where it now ends.
    """
    _cover(1, 34107, at=NOW - timedelta(days=2))
    _run(
        stop_reason=FetchRun.StopReason.END_OF_FEED,
        deepest_rank=33112,
        started_at=NOW - timedelta(hours=1),
    )

    assert known_feed_depth() == 33112


@pytest.mark.django_db
def test_a_growing_feed_is_not_capped_by_a_stale_end_of_feed():
    """A page fetched *after* the end-of-feed run overrules it.

    Otherwise a stale "the feed stops at 500" would hide every rank past it, and
    the tail would go unread while coverage still reported itself complete —
    exactly the truncation blindness the ceiling exists to prevent.
    """
    _run(
        stop_reason=FetchRun.StopReason.END_OF_FEED,
        deepest_rank=500,
        started_at=NOW - timedelta(days=3),
    )
    _cover(1, 500, at=NOW - timedelta(days=3))
    _cover(501, 900, at=NOW - timedelta(hours=1))

    assert known_feed_depth() == 900


@pytest.mark.django_db
def test_known_feed_depth_none_without_coverage():
    _run(deepest_rank=500)   # a run with no PageCoverage proves nothing
    assert known_feed_depth() is None


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_sweep_freshness_fails_without_any_coverage():
    assert check_sweep_freshness(NOW).ok is False


@pytest.mark.django_db
def test_sweep_freshness_fails_when_the_tail_went_unread():
    """The case the old deepest-rank-seen ceiling hid.

    The feed is known to be 300 deep, but only the first 60 ranks were read in
    the window — so 61-300 is a hole and coverage is not complete.
    """
    _cover(1, 300, at=NOW - timedelta(days=2))
    _cover(1, 60, at=NOW - timedelta(hours=1))
    check = check_sweep_freshness(NOW)
    assert check.ok is False
    assert check.data["gap_count"] == 1


@pytest.mark.django_db
def test_sweep_freshness_passes_when_the_window_covers_the_feed():
    _cover(1, 100, at=NOW - timedelta(hours=1))
    assert check_sweep_freshness(NOW).ok is True


@pytest.mark.django_db
def test_sweep_freshness_passes_on_coverage_assembled_from_partial_runs():
    """Four interrupted runs prove exactly what one clean sweep proved."""
    for lo in (1, 26, 51, 76):
        _cover(lo, lo + 24, at=NOW - timedelta(hours=1))
    assert check_sweep_freshness(NOW).ok is True


@pytest.mark.django_db
def test_failed_runs_detected():
    assert check_failed_runs(NOW).ok is True
    _run(status=FetchRun.Status.FAILED, error="boom", started_at=NOW - timedelta(hours=1))
    check = check_failed_runs(NOW)
    assert check.ok is False
    assert check.data["count"] == 1


@pytest.mark.django_db
def test_reject_spike_flags_a_new_rule_firing_at_volume():
    """A rule with no history suddenly firing is the schema-change signal."""
    run = _run()
    IngestReject.objects.bulk_create([
        IngestReject(code=f"c{i:04d}", rule="price_missing_for_lumpsum",
                     detail="", raw_payload={}, fetch_run=run,
                     observed_at=NOW - timedelta(hours=1))
        for i in range(REJECT_SPIKE_MIN_COUNT + 5)
    ])
    check = check_reject_spike(NOW)
    assert check.ok is False
    assert check.data["spikes"][0]["rule"] == "price_missing_for_lumpsum"


@pytest.mark.django_db
def test_reject_spike_ignores_low_volume_noise():
    """A handful of rejects is normal and must not page anyone."""
    run = _run()
    IngestReject.objects.bulk_create([
        IngestReject(code=f"d{i:04d}", rule="code_missing", detail="",
                     raw_payload={}, fetch_run=run,
                     observed_at=NOW - timedelta(hours=1))
        for i in range(REJECT_SPIKE_MIN_COUNT - 1)
    ])
    assert check_reject_spike(NOW).ok is True


@pytest.mark.django_db
def test_ingest_progress_fails_when_pages_fetched_but_nothing_stored():
    """The silent-ban signature: runs succeed, pages load, zero ads ingested."""
    _run(started_at=NOW - timedelta(hours=1), pages_fetched=30, fetched_count=0)
    check = check_ingest_progress(NOW)
    assert check.ok is False
    assert check.data["fetched"] == 0


@pytest.mark.django_db
def test_ingest_progress_fails_when_worker_is_not_running():
    assert check_ingest_progress(NOW).ok is False


@pytest.mark.django_db
def test_ingest_progress_passes_on_a_normal_run():
    _run(started_at=NOW - timedelta(hours=1), pages_fetched=30, fetched_count=900)
    assert check_ingest_progress(NOW).ok is True


@pytest.mark.django_db
def test_run_checks_returns_every_check():
    results = run_checks(NOW)
    assert {c.name for c in results} == {
        "source_block", "sweep_freshness", "failed_runs", "reject_spike",
        "ingest_progress",
    }
