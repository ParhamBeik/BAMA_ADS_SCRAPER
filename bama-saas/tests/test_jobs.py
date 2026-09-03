"""The scheduled worker: JobRun visibility, pruning, and crawl health.

Integration level throughout: the subject is what survives in the database after
a step runs, and what a pure-query health check reports about it — neither is
observable from a unit test of the function alone.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import Mock

import pytest
import requests
from django.utils import timezone

from apps.core.models import (
    Ad,
    AdObservation,
    AdVersion,
    Brand,
    FetchRun,
    IngestReject,
    JobRun,
    Model,
    PageCoverage,
)
from apps.jobs import fetcher
from apps.jobs import pipeline as P
from apps.jobs.fetcher import known_feed_depth
from apps.jobs.jobs import (
    REJECT_SPIKE_MIN_COUNT,
    check_failed_runs,
    check_ingest_progress,
    check_reject_spike,
    check_sweep_freshness,
    prune,
    run_checks,
)

NOW = timezone.now()


@pytest.fixture
def stub_jobs(monkeypatch):
    """Replace every job with a recorder. Returns the list of names invoked.

    ``fail`` names jobs that should raise instead. Patching ``P.JOBS`` rather
    than each module keeps the tests about the *runner*, which is the unit here.
    """
    seen: list[str] = []

    def make(name, fail):
        def job(**opts):
            seen.append(name)
            if name in fail:
                raise RuntimeError(f"{name} failed")
            return {"stub": name, **opts}
        return job

    def install(fail=()):
        monkeypatch.setattr(P, "JOBS", {n: make(n, fail) for n in P.JOBS})
        return seen

    return install


@pytest.mark.django_db
def test_a_successful_step_is_recorded():
    result = P.run_step("snapshot")

    job = JobRun.objects.get(name="snapshot")
    assert job.status == JobRun.Status.OK
    assert job.finished_at is not None
    assert job.duration_s is not None
    assert result.ok


@pytest.mark.django_db
def test_rejected_fetch_ads_do_not_mark_the_step_skipped(monkeypatch):
    """Rejected input is crawler output, not a crawler skip."""
    monkeypatch.setitem(P.JOBS, "fetch", lambda **_: {"rejected": 1})

    result = P.run_step("fetch")

    assert result.ok
    assert JobRun.objects.get(name="fetch").status == JobRun.Status.OK


@pytest.mark.django_db
def test_a_failing_step_is_recorded_with_its_error(stub_jobs):
    stub_jobs(fail={"snapshot"})
    result = P.run_step("snapshot")

    job = JobRun.objects.get(name="snapshot")
    assert job.status == JobRun.Status.FAILED
    assert "snapshot failed" in job.error
    assert not result.ok


@pytest.mark.django_db
def test_a_dependent_step_is_skipped_and_says_so(stub_jobs):
    """The market index is chained arithmetic over the rows the snapshot writes.
    Run after a failed snapshot it extends the series using yesterday's
    inventory and publishes the gap as a real market move."""
    stub_jobs(fail={"snapshot"})
    report = P.run(steps={"snapshot", "market_index"})

    index_job = JobRun.objects.get(name="market_index")
    assert index_job.status == JobRun.Status.SKIPPED
    assert "snapshot" in index_job.detail
    assert not report.ok


@pytest.mark.django_db
def test_skipped_is_distinguishable_from_succeeded(stub_jobs):
    """Why SKIPPED is a status rather than an absent row: silence would be
    indistinguishable from success, which is how stale analytics read as fresh."""
    stub_jobs(fail={"snapshot"})
    P.run(steps={"snapshot", "market_index"})

    assert dict(JobRun.objects.values_list("name", "status")) == {
        "snapshot": JobRun.Status.FAILED,
        "market_index": JobRun.Status.SKIPPED,
    }


@pytest.mark.django_db
def test_an_unrelated_step_still_runs_after_a_failure(stub_jobs):
    """Skipping follows the declared dependency and does not cascade — otherwise
    one flaky step stops all maintenance."""
    stub_jobs(fail={"snapshot"})
    P.run(steps={"snapshot", "episodes"})

    assert JobRun.objects.get(name="episodes").status == JobRun.Status.OK


@pytest.mark.django_db
def test_a_failed_fetch_does_not_cascade(stub_jobs, monkeypatch):
    """Deliberate: local steps are idempotent maintenance over stored data, so a
    transient network blip must not cost a day of snapshots."""
    monkeypatch.setattr(P, "FETCH_ATTEMPTS", 1)
    stub_jobs(fail={"fetch"})
    P.run(steps={"fetch", "snapshot"})

    assert JobRun.objects.get(name="fetch").status == JobRun.Status.FAILED
    assert JobRun.objects.get(name="snapshot").status == JobRun.Status.OK


def test_a_403_fetch_is_not_retried():
    calls = 0

    def reject():
        nonlocal calls
        calls += 1
        raise requests.HTTPError("HTTP 403", response=Mock(status_code=403))

    with pytest.raises(requests.HTTPError):
        P._retry(reject, attempts=3, base_delay=0)

    assert calls == 1


@pytest.mark.django_db
def test_hot_cadence_skips_warm_steps(stub_jobs):
    seen = stub_jobs()
    report = P.run(cadence="hot", skip_fetch=True)

    assert "episodes" not in seen and "snapshot" not in seen
    # `alerts` follows `notify` and precedes nothing: the operator's single
    # Telegram chat and every user's in-app feed read the same board, and
    # neither may be able to fail the other.
    #
    # `ml_score` sits directly after `deal_scores` and before everything that
    # reads the board. The prediction and the peer median are printed on one
    # card, so scoring against a board the rebuild has since replaced makes the
    # two disagree with no way for a reader to tell which half is stale.
    # `ml_train` is deliberately absent: it is minutes of CPU in its own
    # container, on its own `train` cadence.
    assert [s.name for s in report.steps] == [
        "mark_inactive", "deal_scores", "ml_score", "probe_sold", "notify", "alerts",
    ]
    assert "ml_train" not in seen


@pytest.mark.django_db
def test_train_cadence_fits_before_it_scores(stub_jobs):
    """Order, not membership, and it shipped backwards once.

    A cadence's steps are sorted by ``STEP_ORDER``, not run in the order the
    cadence lists them — so with `ml_train` at the end of that tuple, `train`
    ran `ml_score` first: it scored the board with the outgoing models, then
    fitted new ones and never scored with them. A promotion took a full day to
    reach a reader.
    """
    stub_jobs()
    report = P.run(cadence="train")
    assert [s.name for s in report.steps] == ["ml_train", "ml_score"]


@pytest.mark.django_db
def test_warm_cadence_skips_fetch_and_deals(stub_jobs):
    seen = stub_jobs()
    report = P.run(cadence="warm")

    assert "fetch" not in seen and "deal_scores" not in seen
    assert [s.name for s in report.steps] == [
        "link_reposts", "episodes", "snapshot", "market_index",
    ]


@pytest.mark.django_db
def test_hot_deal_scores_are_incremental_and_maintenance_is_not(stub_jobs):
    """The hot tick rescores only what the fetch touched; every other cadence
    rebuilds, which is what catches cohorts nothing fetched."""
    modes = {}

    def record(**opts):
        modes[len(modes)] = opts.get("incremental")
        return opts

    stub_jobs()
    P.JOBS["deal_scores"] = record
    P.run(cadence="hot", skip_fetch=True)
    P.run(cadence="maintenance")

    assert modes == {0: True, 1: False}


@pytest.mark.django_db
def test_reap_orphan_runs_fails_stuck_running_rows():
    from apps.jobs.jobs import reap_orphan_runs

    run = FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH, status=FetchRun.Status.RUNNING,
        mode=FetchRun.Mode.FULL,
    )
    job = JobRun.objects.create(name="fetch", status=JobRun.Status.RUNNING)

    counts = reap_orphan_runs()
    run.refresh_from_db()
    job.refresh_from_db()

    assert counts == {"fetch_runs": 1, "job_runs": 1}
    assert run.status == FetchRun.Status.FAILED
    assert run.stop_reason == FetchRun.StopReason.INTERRUPTED
    assert job.status == JobRun.Status.FAILED


@pytest.mark.django_db
def test_record_job_marks_failure_and_reraises():
    with pytest.raises(ValueError):
        with P.record_job("custom"):
            raise ValueError("boom")

    job = JobRun.objects.get(name="custom")
    assert job.status == JobRun.Status.FAILED
    assert "boom" in job.error


@pytest.mark.django_db
def test_a_gated_crawl_is_skipped_not_failed(stub_jobs):
    """A cooldown this system chose is not a fault. Recording it as one lit up
    the health page with 245 "failures" for a block with exactly one cause."""
    from apps.jobs.fetcher import CrawlBlocked

    def blocked(**opts):
        raise CrawlBlocked("cooling down")

    stub_jobs()
    P.JOBS["fetch"] = blocked
    report = P.run(steps={"fetch"})

    assert JobRun.objects.get(name="fetch").status == JobRun.Status.SKIPPED
    # ok=True so the tick continues to the steps that need no network.
    assert report.ok


@pytest.mark.django_db
@pytest.mark.django_db
def test_prune_history_deletes_old_rows_and_keeps_recent_and_sweep_coverage():
    now = timezone.now()
    old = now - timedelta(days=120)
    brand = Brand.objects.create(slug="x", name_fa="x")
    model = Model.objects.create(brand=brand, name_fa="m")
    ad = Ad.objects.create(code="p1", brand=brand, model=model, year_jalali=1400)

    old_run = FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH,
        status=FetchRun.Status.SUCCEEDED,
        started_at=old,
        finished_at=old,
        reached_end=True,
        mode=FetchRun.Mode.FULL,
    )
    keep_run = FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH,
        status=FetchRun.Status.SUCCEEDED,
        started_at=now,
        finished_at=now,
        reached_end=True,
        mode=FetchRun.Mode.FULL,
    )
    version = AdVersion.objects.create(
        ad=ad, semantic_hash="a" * 8, raw_hash="b" * 8,
        payload={}, origin=AdVersion.Origin.LIVE_FETCH, first_observed_at=old,
    )
    AdObservation.objects.create(
        ad=ad, fetch_run=old_run, version=version, observed_at=old, raw_hash="b" * 8,
    )
    AdObservation.objects.create(
        ad=ad, fetch_run=keep_run, version=version, observed_at=now, raw_hash="b" * 8,
    )
    PageCoverage.objects.create(
        fetch_run=old_run, page_index=0, rank_lo=1, rank_hi=30,
        ad_count=30, fetched_at=old,
    )
    PageCoverage.objects.create(
        fetch_run=keep_run, page_index=0, rank_lo=1, rank_hi=30,
        ad_count=30, fetched_at=now,
    )
    JobRun.objects.create(name="fetch", status=JobRun.Status.OK, started_at=old, finished_at=old)
    JobRun.objects.create(name="fetch", status=JobRun.Status.OK, started_at=now, finished_at=now)

    result = prune(days=90)

    assert result["observations"] == 1
    assert AdObservation.objects.count() == 1
    assert AdObservation.objects.get().fetch_run_id == keep_run.id
    # 120-day-old coverage is past the retention window and proves nothing: the
    # depth ratchet only looks back FEED_DEPTH_WINDOW_DAYS. Coverage used to be
    # kept because its run had reached_end, a rule that no longer exists.
    assert PageCoverage.objects.count() == 1
    assert PageCoverage.objects.get().fetch_run_id == keep_run.id
    assert JobRun.objects.filter(started_at__lt=now - timedelta(days=1)).count() == 0
    assert Ad.objects.filter(code="p1").exists()
    assert AdVersion.objects.filter(ad=ad).exists()


@pytest.mark.django_db
def test_prune_never_deletes_coverage_inside_the_depth_window():
    """Coverage is the proof the ceiling and removal rule stand on.

    Pruning inside the ratchet window would lower the known feed depth, hiding
    the tail below the ceiling and silently stalling removal detection — so a
    short ``--days`` must not reach it.
    """
    now = timezone.now()
    recent = now - timedelta(days=10)
    run = FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH,
        status=FetchRun.Status.SUCCEEDED,
        started_at=recent, finished_at=recent, mode=FetchRun.Mode.FULL,
    )
    PageCoverage.objects.create(
        fetch_run=run, page_index=0, rank_lo=1, rank_hi=30,
        ad_count=30, fetched_at=recent,
    )

    prune(days=1)

    assert PageCoverage.objects.count() == 1


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
def test_delta_end_does_not_change_global_feed_depth():
    _run(
        mode=FetchRun.Mode.DELTA,
        status=FetchRun.Status.SUCCEEDED,
        stop_reason=FetchRun.StopReason.END_OF_FEED,
        reached_end=False,
        deepest_rank=30,
    )
    PageCoverage.objects.create(
        fetch_run=FetchRun.objects.filter(mode=FetchRun.Mode.DELTA).first(),
        page_index=0,
        rank_lo=1,
        rank_hi=30,
        fetched_at=NOW,
    )
    assert known_feed_depth() == 30


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
        mode=FetchRun.Mode.FULL,
        reached_end=True,
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
        mode=FetchRun.Mode.FULL,
        reached_end=True,
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
