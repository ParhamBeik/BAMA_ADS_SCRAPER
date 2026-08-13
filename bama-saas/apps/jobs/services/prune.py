"""Drop aged provenance rows that are no longer needed for gap repair or proofs.

Keeps ``Ad`` / ``AdVersion`` (current snapshot + content history). Prunes
``AdObservation``, ``PageCoverage``, and ``JobRun`` older than ``days``.
Coverage rows belonging to the last two completed (``reached_end``) sweeps are
always kept — ``mark_inactive_ads`` needs those two sweeps.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from apps.core.models import AdObservation, FetchRun, JobRun, PageCoverage

DEFAULT_DAYS = 90
_BATCH = 5000


def _batched_delete(qs, *, batch: int = _BATCH) -> int:
    deleted = 0
    pk_name = qs.model._meta.pk.name
    while True:
        ids = list(qs.values_list(pk_name, flat=True)[:batch])
        if not ids:
            break
        n, _ = qs.model.objects.filter(pk__in=ids).delete()
        deleted += n
    return deleted


def prune_history(*, days: int = DEFAULT_DAYS, dry_run: bool = False) -> dict:
    cutoff = timezone.now() - timedelta(days=days)
    keep_runs = list(
        FetchRun.objects.filter(
            reached_end=True,
            status=FetchRun.Status.SUCCEEDED,
        )
        .order_by("-started_at")
        .values_list("pk", flat=True)[:2]
    )

    obs = AdObservation.objects.filter(observed_at__lt=cutoff)
    cov = PageCoverage.objects.filter(fetched_at__lt=cutoff)
    if keep_runs:
        cov = cov.exclude(fetch_run_id__in=keep_runs)
    jobs = JobRun.objects.filter(started_at__lt=cutoff)

    counts = {
        "cutoff": cutoff.isoformat(),
        "days": days,
        "observations": obs.count(),
        "page_coverage": cov.count(),
        "job_runs": jobs.count(),
        "kept_sweep_runs": len(keep_runs),
        "dry_run": dry_run,
    }
    if dry_run:
        return counts

    counts["observations"] = _batched_delete(obs)
    counts["page_coverage"] = _batched_delete(cov)
    counts["job_runs"] = _batched_delete(jobs)
    return counts
