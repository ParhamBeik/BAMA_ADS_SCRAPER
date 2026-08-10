"""Failure visibility: JobRun records and prerequisite skipping.

Test type: integration — the subject is what survives in the database after a
step runs, which is precisely what was missing before.

The question these answer is "did last night's snapshot actually run?". Before
JobRun that was answerable only by reading container logs, and a step that was
skipped because its prerequisite failed looked exactly like one that succeeded.
"""

import pytest

from apps.core.models import JobRun
from apps.jobs.services import pipeline as P


@pytest.mark.django_db
def test_a_successful_step_is_recorded():
    result = P._exec_cmd_step("daily_snapshot", "daily_snapshot")

    job = JobRun.objects.get(name="daily_snapshot")
    assert job.status == JobRun.Status.OK
    assert job.finished_at is not None
    assert job.duration_s is not None
    assert result.ok


@pytest.mark.django_db
def test_a_failing_step_is_recorded_with_its_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("database on fire")

    monkeypatch.setattr(P, "call_command", boom)
    result = P._exec_cmd_step("daily_snapshot", "daily_snapshot")

    job = JobRun.objects.get(name="daily_snapshot")
    assert job.status == JobRun.Status.FAILED
    assert "database on fire" in job.error
    assert not result.ok


@pytest.mark.django_db
def test_a_dependent_step_is_skipped_and_says_so(monkeypatch):
    """The market index is chained arithmetic over the rows daily_snapshot writes.
    Run after a failed snapshot it extends the series using yesterday's inventory
    and publishes the gap as a real market move."""
    def selective(command, *a, **k):
        if command == "daily_snapshot":
            raise RuntimeError("snapshot failed")

    monkeypatch.setattr(P, "call_command", selective)
    report = P.run_pipeline(fetch=False, steps={"daily_snapshot", "market_index"})

    index_job = JobRun.objects.get(name="market_index")
    assert index_job.status == JobRun.Status.SKIPPED
    assert "daily_snapshot" in index_job.detail
    assert not report.ok


@pytest.mark.django_db
def test_skipped_is_distinguishable_from_succeeded(monkeypatch):
    """The whole reason SKIPPED is a status rather than an absent row: silence
    would be indistinguishable from success, which is how stale analytics get
    read as fresh."""
    def selective(command, *a, **k):
        if command == "daily_snapshot":
            raise RuntimeError("nope")

    monkeypatch.setattr(P, "call_command", selective)
    P.run_pipeline(fetch=False, steps={"daily_snapshot", "market_index"})

    statuses = dict(JobRun.objects.values_list("name", "status"))
    assert statuses == {
        "daily_snapshot": JobRun.Status.FAILED,
        "market_index": JobRun.Status.SKIPPED,
    }


@pytest.mark.django_db
def test_an_unrelated_step_still_runs_after_a_failure(monkeypatch):
    """Skipping must follow the declared dependency, not cascade to everything —
    otherwise one flaky step stops all maintenance."""
    def selective(command, *a, **k):
        if command == "daily_snapshot":
            raise RuntimeError("nope")

    monkeypatch.setattr(P, "call_command", selective)
    P.run_pipeline(fetch=False, steps={"daily_snapshot", "market_snapshot"})

    assert JobRun.objects.get(name="market_snapshot").status == JobRun.Status.OK


@pytest.mark.django_db
def test_a_failed_fetch_does_not_cascade(monkeypatch):
    """Deliberate: local steps are idempotent maintenance over stored data, so a
    transient network blip must not cost a day of snapshots."""
    def selective(command, *a, **k):
        if command == "fetch_live":
            raise RuntimeError("network")

    monkeypatch.setattr(P, "call_command", selective)
    P.run_pipeline(
        fetch=True, steps={"fetch", "daily_snapshot"}, fetch_attempts=1,
    )

    assert JobRun.objects.get(name="fetch").status == JobRun.Status.FAILED
    assert JobRun.objects.get(name="daily_snapshot").status == JobRun.Status.OK


@pytest.mark.django_db
def test_record_job_marks_failure_and_reraises():
    with pytest.raises(ValueError):
        with P.record_job("custom"):
            raise ValueError("boom")

    job = JobRun.objects.get(name="custom")
    assert job.status == JobRun.Status.FAILED
    assert "boom" in job.error
