"""Mark FetchRun / JobRun rows left RUNNING after a process death."""

from __future__ import annotations

from django.utils import timezone

from apps.core.models import FetchRun, JobRun

_FETCH_ERROR = "orphaned: process exited while this run was still RUNNING"
_JOB_ERROR = "orphaned: process exited while this job was still RUNNING"


def reap_orphan_runs(*, now=None) -> dict[str, int]:
    """Fail every RUNNING fetch/job. Safe at worker boot: nothing is live yet."""
    now = now or timezone.now()
    fetch_n = FetchRun.objects.filter(status=FetchRun.Status.RUNNING).update(
        status=FetchRun.Status.FAILED,
        stop_reason=FetchRun.StopReason.INTERRUPTED,
        finished_at=now,
        error=_FETCH_ERROR,
    )
    job_n = JobRun.objects.filter(status=JobRun.Status.RUNNING).update(
        status=JobRun.Status.FAILED,
        finished_at=now,
        error=_JOB_ERROR,
    )
    return {"fetch_runs": fetch_n, "job_runs": job_n}
