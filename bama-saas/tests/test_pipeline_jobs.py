"""Failure visibility: JobRun records, cadences, and prerequisite skipping.

Integration level: the subject is what survives in the database after a step
runs, which is precisely what was missing before. "Did last night's snapshot
run?" used to be answerable only by reading container logs, and a step skipped
because its prerequisite failed looked exactly like one that succeeded.
"""

from unittest.mock import Mock

import pytest
import requests

from apps.core.models import FetchRun, JobRun
from apps.jobs import pipeline as P


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
    assert [s.name for s in report.steps] == ["mark_inactive", "deal_scores", "notify"]


@pytest.mark.django_db
def test_warm_cadence_skips_fetch_and_deals(stub_jobs):
    seen = stub_jobs()
    report = P.run(cadence="warm")

    assert "fetch" not in seen and "deal_scores" not in seen
    assert [s.name for s in report.steps] == ["episodes", "snapshot", "market_index"]


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
