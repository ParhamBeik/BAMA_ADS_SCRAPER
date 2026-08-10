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
    SWEEP_MAX_AGE_HOURS,
    check_coverage_gaps,
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
def test_known_feed_depth_ignores_incomplete_sweeps():
    _run(reached_end=False, deepest_rank=99999, started_at=NOW)
    _run(reached_end=True, deepest_rank=28000, started_at=NOW - timedelta(hours=6))
    assert known_feed_depth() == 28000


@pytest.mark.django_db
def test_known_feed_depth_none_without_completed_sweep():
    _run(reached_end=False, deepest_rank=500)
    assert known_feed_depth() is None


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_sweep_freshness_fails_without_any_sweep():
    assert check_sweep_freshness(NOW).ok is False


@pytest.mark.django_db
def test_sweep_freshness_fails_when_stale():
    _run(reached_end=True, deepest_rank=100,
         started_at=NOW - timedelta(hours=SWEEP_MAX_AGE_HOURS + 1))
    assert check_sweep_freshness(NOW).ok is False


@pytest.mark.django_db
def test_sweep_freshness_passes_when_recent():
    _run(reached_end=True, deepest_rank=100, started_at=NOW - timedelta(hours=1))
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
def test_coverage_gaps_reports_uncovered_tail():
    """A truncated sweep leaves a hole the old deepest-rank-seen ceiling hid."""
    sweep = _run(reached_end=True, deepest_rank=300,
                 started_at=NOW - timedelta(hours=2))
    # Only the first 60 ranks were read since.
    PageCoverage.objects.create(
        fetch_run=sweep, page_index=0, rank_lo=1, rank_hi=60,
        ad_count=60, new_count=0, changed_count=0, fetched_at=NOW,
    )
    check = check_coverage_gaps(NOW)
    assert check.ok is False
    assert check.data["gap_count"] == 1


@pytest.mark.django_db
def test_run_checks_returns_every_check():
    results = run_checks(NOW)
    assert {c.name for c in results} == {
        "sweep_freshness", "failed_runs", "reject_spike",
        "coverage_gaps", "ingest_progress",
    }
