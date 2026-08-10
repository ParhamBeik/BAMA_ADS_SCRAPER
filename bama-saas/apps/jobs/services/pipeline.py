"""Worker pipeline: the scheduled data cycle, with retry + structured logging.

Each step reuses an existing primitive rather than reimplementing it:

    fetch_live        (network) — pulls new/changed Bama ads; writes Ad/AdVersion/
                                 AdObservation/PriceObservation/PriceDropEvent
                                 via ingest_ad.
    mark_inactive     (local)  — flips stale ACTIVE ads to REMOVED.
    daily_snapshot    (local)  — idempotently refreshes today's per-slice inventory.
    market_snapshot   (local)  — idempotently refreshes today's whole-market rollup.
    market_index      (local)  — chains cohort medians into the price index;
                                 must follow daily_snapshot, whose rows it reads.
    deal_scores       (local)  — rebuilds per-ad DealScoreCache (best-deal board).

``refresh_analytics`` used to be a step here. It has been deleted outright: the
only table it wrote, ``PriceStatistics``, was read by no view, serializer or
service, so every one of its runs was pure cost.

Steps are largely independent — a failure in one is logged and the rest still
run, so a flaky live fetch does not stop the cheap local steps from keeping
analytics fresh. The exception is a declared prerequisite (``_DEPENDS_ON``): a
step whose input never refreshed is *skipped* rather than allowed to publish a
plausible number computed from yesterday. Every step records a JobRun either way.
The network fetch is retried with exponential backoff; the local steps are not
(they only touch the DB and either succeed or fail loudly).

User-facing alert evaluation and digests are NOT in this 5-minute pipeline —
they run on their own slower crons (see deploy/worker/) so a flaky email/Telegram
send can never stall the data tick.

Invoked every ~5 minutes by the cron-guarded runner (see deploy/worker/). The
fetch is incremental (small ``--max-ads``), so a tick finishes well under the
cadence; a full 50k sweep is a separate, less-frequent concern. The runner holds
a ``flock`` so overlapping ticks never race.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from io import StringIO
from typing import Callable, Optional

from django.core.management import call_command
from django.utils import timezone

logger = logging.getLogger("bama.worker")

# Ordered step list — the canonical pipeline. ``run_pipeline`` runs exactly
# these, in this order, unless the caller restricts via ``steps``.
STEP_ORDER = (
    "fetch",
    "mark_inactive",
    "episodes",
    "daily_snapshot",
    "market_index",
    "market_snapshot",
    "deal_scores",
)

# Local step name → management command. The network step (fetch) is special-cased.
_LOCAL_COMMANDS = {
    "mark_inactive": "mark_inactive_ads",
    # After removal marking: an episode ends when an ad stops being seen.
    "episodes": "sync_episodes",
    "daily_snapshot": "daily_snapshot",
    # Strictly after daily_snapshot: the index is chained arithmetic over the
    # rows that command writes, so running it first would index yesterday.
    "market_index": "build_market_index",
    "market_snapshot": "market_snapshot",
    "deal_scores": "compute_deal_scores",
    # No refresh_analytics: PriceStatistics, the only table it wrote, was read by
    # nothing and has been removed along with the command.
}


# Steps that must not run when a prerequisite failed, because they would produce
# a plausible-looking number from data that was never refreshed.
#
# Only the real chain is listed. A failed *fetch* deliberately does NOT cascade:
# the local steps are idempotent maintenance over what is already stored, and
# stopping them on a transient network blip would mean one flaky minute costs a
# day of snapshots. mark_inactive_ads is separately safe — it needs two completed
# sweeps on record, which a failed fetch does not produce.
_DEPENDS_ON = {
    # The index is chained arithmetic over the rows daily_snapshot writes, so on a
    # failed snapshot it would extend the series using yesterday's inventory and
    # report the gap as a real market move.
    "market_index": ("daily_snapshot",),
}


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""
    duration_s: float = 0.0


@dataclass
class PipelineReport:
    started_at: object = None
    finished_at: object = None
    steps: list[StepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps)

    @property
    def duration_s(self) -> float:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return 0.0

    def summary(self) -> str:
        flags = " ".join(f"{s.name}={'ok' if s.ok else 'FAIL'}" for s in self.steps)
        return f"pipeline ok={self.ok} duration={self.duration_s:.1f}s {flags}"


def _retry(fn: Callable, *, attempts: int, base_delay: float, label: str):
    """Run ``fn`` with exponential backoff. Re-raises the last error."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — surface any failure to caller
            if attempt >= attempts:
                logger.warning("%s attempt %d/%d exhausted: %s", label, attempt, attempts, exc)
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "%s attempt %d/%d failed (%s); retrying in %.1fs",
                label, attempt, attempts, exc, delay,
            )
            time.sleep(delay)


def record_job(name: str, *, triggered_by: str = "scheduler"):
    """Context manager that persists one JobRun row around a unit of work.

    Every scheduled step routes through here, so "did it run?" stops being a
    question you answer by reading container logs. Yields the row so a caller can
    annotate it; marks it failed and re-raises on exception.
    """
    from apps.core.models import JobRun

    return _JobRecorder(JobRun, name, triggered_by)


class _JobRecorder:
    def __init__(self, model, name: str, triggered_by: str):
        self.model, self.name, self.triggered_by = model, name, triggered_by
        self.row = None
        self._start = 0.0

    def __enter__(self):
        self._start = time.monotonic()
        self.row = self.model.objects.create(
            name=self.name,
            status=self.model.Status.RUNNING,
            triggered_by=self.triggered_by,
            started_at=timezone.now(),
        )
        return self.row

    def __exit__(self, exc_type, exc, tb):
        self.row.duration_s = time.monotonic() - self._start
        self.row.finished_at = timezone.now()
        if exc is not None:
            self.row.status = self.model.Status.FAILED
            self.row.error = str(exc)[:4000]
        elif self.row.status == self.model.Status.RUNNING:
            self.row.status = self.model.Status.OK
        self.row.save()
        return False  # never swallow


def record_skipped(name: str, reason: str) -> None:
    """Persist the fact that a step did not run because a prerequisite failed.

    A step that was skipped and a step that succeeded look identical when only
    failures are recorded, which is exactly how stale analytics get mistaken for
    fresh ones.
    """
    from apps.core.models import JobRun

    JobRun.objects.create(
        name=name,
        status=JobRun.Status.SKIPPED,
        started_at=timezone.now(),
        finished_at=timezone.now(),
        duration_s=0.0,
        detail=reason,
    )


def _exec_cmd_step(
    name: str, command: str, *, retry: Optional[dict] = None,
    triggered_by: str = "scheduler", **opts
) -> StepResult:
    """Run one management command as a step.

    The command must raise on failure (call_command does) so an optional retry
    policy can fire. Captures stdout into ``detail`` for the report, and persists
    a JobRun row either way — this is the one place every scheduled step passes
    through, so it is the only place that has to remember.
    """
    start = time.monotonic()
    buf = StringIO()
    call_opts = {**opts, "stdout": buf}

    def run_once():
        call_command(command, **call_opts)

    try:
        with record_job(name, triggered_by=triggered_by) as job:
            runner = run_once if retry is None else lambda: _retry(run_once, label=name, **retry)
            runner()
            dur = time.monotonic() - start
            detail = buf.getvalue().strip().replace("\n", " | ")
            job.detail = detail[:4000]
            logger.info("step=%s OK duration=%.1fs %s", name, dur, detail)
            return StepResult(name, True, detail, dur)
    except Exception as exc:  # noqa: BLE001
        dur = time.monotonic() - start
        logger.exception("step=%s FAILED duration=%.1fs err=%s", name, dur, exc)
        return StepResult(name, False, str(exc)[:500], dur)


def run_pipeline(
    *,
    fetch: bool = True,
    fetch_max_ads: Optional[int] = None,
    mode: str = "delta",
    steps: Optional[set[str]] = None,
    fetch_attempts: int = 3,
    fetch_retry_delay: float = 5.0,
) -> PipelineReport:
    """Run the scheduled pipeline. See module docstring.

    Parameters
    ----------
    fetch : bool
        If False, skip the network fetch entirely (local-maintenance-only tick).
        Maps to the ``run_pipeline --skip-fetch`` flag for testability.
    fetch_max_ads : int | None
        Per-tick fetch size. None defers to the ``fetch_live`` command default
        (which reads BAMA_WORKER_FETCH_ADS / BAMA_MAX_ADS from settings).
    mode : str
        Ingestion mode: "delta" (fast-delta with early-stopping) or "full" (full scan).
    steps : set[str] | None
        Explicit subset of STEP_ORDER to run. None means every enabled step.
    fetch_attempts / fetch_retry_delay :
        Exponential-backoff retry only for the network fetch (1, base_delay, 2*base_delay, …).
    """
    enabled = set(steps) if steps is not None else set(STEP_ORDER)
    if not fetch:
        enabled.discard("fetch")

    report = PipelineReport(started_at=timezone.now())
    logger.info("pipeline start mode=%s steps=[%s]", mode, ",".join(s for s in STEP_ORDER if s in enabled))

    failed: set[str] = set()
    for name in STEP_ORDER:
        if name not in enabled:
            continue
        blocker = next((p for p in _DEPENDS_ON.get(name, ()) if p in failed), None)
        if blocker:
            reason = f"prerequisite {blocker!r} failed"
            logger.warning("step=%s SKIPPED (%s)", name, reason)
            record_skipped(name, reason)
            report.steps.append(StepResult(name, False, f"skipped: {reason}", 0.0))
            failed.add(name)
            continue
        if name == "fetch":
            opts = {"mode": mode}
            if fetch_max_ads is not None:
                opts["max_ads"] = fetch_max_ads
            result = _exec_cmd_step(
                "fetch", "fetch_live",
                retry={"attempts": fetch_attempts, "base_delay": fetch_retry_delay},
                **opts,
            )
        else:
            result = _exec_cmd_step(name, _LOCAL_COMMANDS[name])
        report.steps.append(result)
        if not result.ok:
            failed.add(name)

    report.finished_at = timezone.now()
    logger.info("%s", report.summary())
    return report

