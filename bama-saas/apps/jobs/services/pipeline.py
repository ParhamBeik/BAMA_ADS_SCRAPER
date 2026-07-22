"""Worker pipeline: the scheduled data cycle, with retry + structured logging.

Each step reuses an existing primitive rather than reimplementing it:

    fetch_live        (network) — pulls new/changed Bama ads; writes Ad/AdVersion/
                                 AdObservation/PriceObservation/PriceDropEvent
                                 via ingest_ad.
    mark_inactive     (local)  — flips stale ACTIVE ads to REMOVED.
    daily_snapshot    (local)  — idempotently refreshes today's per-slice inventory.
    market_snapshot   (local)  — idempotently refreshes today's whole-market rollup.
    deal_scores       (local)  — rebuilds per-ad DealScoreCache (best-deal board).
    refresh_analytics (local)  — rebuilds PriceStatistics aggregates.

Steps are independent: a failure in one is logged but does not abort the others,
so a flaky live fetch still lets the cheap local steps keep analytics fresh. The
network fetch is retried with exponential backoff; the local steps are not (they
only touch the DB and either succeed or fail loudly).

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
    "daily_snapshot",
    "market_snapshot",
    "deal_scores",
    "refresh_analytics",
)

# Local step name → management command. The network step (fetch) is special-cased.
_LOCAL_COMMANDS = {
    "mark_inactive": "mark_inactive_ads",
    "daily_snapshot": "daily_snapshot",
    "market_snapshot": "market_snapshot",
    "deal_scores": "compute_deal_scores",
    "refresh_analytics": "refresh_analytics",
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


def _exec_cmd_step(
    name: str, command: str, *, retry: Optional[dict] = None, **opts
) -> StepResult:
    """Run one management command as a step.

    The command must raise on failure (call_command does) so an optional retry
    policy can fire. Captures stdout into ``detail`` for the report.
    """
    start = time.monotonic()
    buf = StringIO()
    call_opts = {**opts, "stdout": buf}

    def run_once():
        call_command(command, **call_opts)

    try:
        runner = run_once if retry is None else lambda: _retry(run_once, label=name, **retry)
        runner()
        dur = time.monotonic() - start
        detail = buf.getvalue().strip().replace("\n", " | ")
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

    for name in STEP_ORDER:
        if name not in enabled:
            continue
        if name == "fetch":
            opts = {"mode": mode}
            if fetch_max_ads is not None:
                opts["max_ads"] = fetch_max_ads
            report.steps.append(_exec_cmd_step(
                "fetch", "fetch_live",
                retry={"attempts": fetch_attempts, "base_delay": fetch_retry_delay},
                **opts,
            ))
        else:
            report.steps.append(_exec_cmd_step(name, _LOCAL_COMMANDS[name]))

    report.finished_at = timezone.now()
    logger.info("%s", report.summary())
    return report

