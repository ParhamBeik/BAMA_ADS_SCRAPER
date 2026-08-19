"""Drop aged provenance rows that are no longer needed for gap repair or proofs.

Keeps ``Ad`` / ``AdVersion`` (current snapshot + content history). Prunes
``AdObservation``, ``PageCoverage``, and ``JobRun`` older than ``days``.

``PageCoverage`` has a hard retention floor of ``FEED_DEPTH_WINDOW_DAYS``,
because coverage rows are no longer just a repair aid — they *are* the proof of
feed coverage. ``known_feed_depth`` derives the ceiling from them and
``mark_inactive_ads`` derives removal from them, so pruning inside that window
would lower the ceiling and silently stall removal detection.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from apps.core.models import AdObservation, JobRun, PageCoverage
from apps.jobs.services.coverage import FEED_DEPTH_WINDOW_DAYS

DEFAULT_DAYS = 30
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
    now = timezone.now()
    cutoff = now - timedelta(days=days)
    # Never prune coverage inside the depth-ratchet window, however small
    # ``days`` is: those rows are the proof the ceiling and removal rule stand on.
    coverage_cutoff = min(cutoff, now - timedelta(days=FEED_DEPTH_WINDOW_DAYS))

    obs = AdObservation.objects.filter(observed_at__lt=cutoff)
    cov = PageCoverage.objects.filter(fetched_at__lt=coverage_cutoff)
    jobs = JobRun.objects.filter(started_at__lt=cutoff)

    counts = {
        "cutoff": cutoff.isoformat(),
        "coverage_cutoff": coverage_cutoff.isoformat(),
        "days": days,
        "observations": obs.count(),
        "page_coverage": cov.count(),
        "job_runs": jobs.count(),
        "dry_run": dry_run,
    }
    if dry_run:
        return counts

    counts["observations"] = _batched_delete(obs)
    counts["page_coverage"] = _batched_delete(cov)
    counts["job_runs"] = _batched_delete(jobs)
    return counts
