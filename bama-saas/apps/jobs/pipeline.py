"""The scheduled runner: which jobs run together, in what order, and recorded how.

Cadences (see ``CADENCES``):

    hot         ~15 min   fetch + removal marking + incremental deals + notify
    coverage    ~10 min   one bounded chunk of whatever the feed has not shown lately
    warm        ~30 min   episodes + daily snapshot + market index
    maintenance   ~6 h    full deal rebuild + prune + health report
    full                  every hot/warm step, with a full deal-score rebuild

Steps are independent by design: a flaky live fetch must not stop the cheap
local steps from keeping analytics fresh. The exception is a declared
prerequisite (``DEPENDS_ON``) — a step whose input never refreshed is *skipped*
rather than allowed to publish a plausible number computed from yesterday.

Every step records a ``JobRun`` either way, which is what makes "did last
night's snapshot run?" a query instead of a container-log excavation. Only the
network fetch is retried; the local steps touch the DB and either work or fail
loudly.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field

from django.utils import timezone

from apps.core.models import JobRun
from apps.jobs import jobs
from apps.jobs.fetcher import CrawlBlocked, _retryable

logger = logging.getLogger("bama.worker")

# name -> the callable that does the work. This is the whole job vocabulary.
JOBS: dict[str, Callable[..., dict]] = {
    "fetch": jobs.fetch,
    "mark_inactive": jobs.mark_inactive,
    "link_reposts": jobs.link_reposts,
    "episodes": jobs.sync_episodes,
    "snapshot": jobs.daily_snapshot,
    "market_index": jobs.market_index,
    "deal_scores": jobs.deal_scores,
    "probe_sold": jobs.probe_sold,
    "notify": jobs.notify,
    "coverage": jobs.coverage,
    "backfill_images": jobs.backfill_images,
    "prune": jobs.prune,
    "health": jobs.health,
    "probe_depth": jobs.probe_depth,
    "reap_orphans": jobs.reap_orphan_runs,
}

# Canonical ordering. `market_index` must follow `snapshot` (it is arithmetic
# over those rows) and `notify` must follow `deal_scores` (it reads that board),
# so a run in this order can never announce the previous tick's answers.
# link_reposts sits between removal marking and episodes: it needs the
# delisted set to be current, and episodes reads the links it writes.
STEP_ORDER = ("fetch", "mark_inactive", "link_reposts", "episodes", "snapshot",
              "market_index", "deal_scores", "probe_sold", "notify", "coverage",
              "backfill_images", "prune", "health")

CADENCES = {
    "hot": ("fetch", "mark_inactive", "deal_scores", "probe_sold", "notify"),
    "warm": ("link_reposts", "episodes", "snapshot", "market_index"),
    "coverage": ("coverage",),
    # backfill_images is local and idempotent: it sweeps up rows whose photos
    # were never extracted, so a listing does not have to be re-observed before
    # the board can show it.
    "maintenance": ("deal_scores", "backfill_images", "prune", "health"),
    "full": ("fetch", "mark_inactive", "link_reposts", "episodes", "snapshot",
             "market_index", "deal_scores", "probe_sold", "notify"),
}

# A failed *fetch* deliberately does not cascade: the local steps are idempotent
# maintenance over what is already stored, and stopping them on a transient blip
# would mean one flaky minute costs a day of snapshots. Removal marking is
# separately safe — it needs two completed coverage windows on record, which a
# failed fetch does not produce.
#
# The one exception is an *incremental* deal-score pass, which reads the models
# the latest fetch touched; that is handled in ``run`` rather than declared here,
# because a full rebuild has no such dependency.
DEPENDS_ON = {
    # On a failed snapshot the index would extend a chained series using
    # yesterday's inventory and report the gap as a real market move.
    "market_index": ("snapshot",),
}

# ``health`` is a report, not a step: a red crawler must not make the
# maintenance tick look as though it failed to run.
ADVISORY = frozenset({"health"})

FETCH_ATTEMPTS = 3
FETCH_RETRY_DELAY = 5.0


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""
    duration_s: float = 0.0
    skipped: bool = False


@dataclass
class Report:
    steps: list[StepResult] = field(default_factory=list)
    started_at: object = None
    finished_at: object = None

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


@contextmanager
def record_job(name: str, triggered_by: str = JobRun.Trigger.SCHEDULER):
    """Persist one JobRun row around a unit of work. Yields the row.

    ``CrawlBlocked`` is recorded as SKIPPED, not FAILED: it is a back-off this
    system chose, and lumping it in with faults is what made the health page read
    "245 failed run(s)" during a block with exactly one cause. Nothing is
    swallowed — the caller still sees the exception.
    """
    start = time.monotonic()
    row = JobRun.objects.create(name=name, status=JobRun.Status.RUNNING,
                                triggered_by=triggered_by, started_at=timezone.now())
    try:
        yield row
    except CrawlBlocked as exc:
        row.status, row.detail = JobRun.Status.SKIPPED, str(exc)[:4000]
        raise
    except Exception as exc:  # noqa: BLE001 — re-raised immediately
        row.status, row.error = JobRun.Status.FAILED, str(exc)[:4000]
        raise
    finally:
        if row.status == JobRun.Status.RUNNING:
            row.status = JobRun.Status.OK
        row.duration_s = time.monotonic() - start
        row.finished_at = timezone.now()
        row.save()


def _retry(fn, *, attempts: int, base_delay: float):
    """Run ``fn`` with exponential backoff. Re-raises the last error."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — surfaced to the caller
            if attempt >= attempts or not _retryable(exc):
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning("event=pipeline_fetch_retry attempt=%d/%d delay_s=%.1f error=%s",
                           attempt, attempts, delay, exc)
            time.sleep(delay)


def run_step(name: str, *, triggered_by: str = JobRun.Trigger.SCHEDULER, **opts) -> StepResult:
    """Run one job, recorded as a JobRun. Never raises."""
    start = time.monotonic()
    job = JOBS[name]
    try:
        with record_job(name, triggered_by=triggered_by) as row:
            if name == "fetch":
                result = _retry(lambda: job(**opts), attempts=FETCH_ATTEMPTS,
                                base_delay=FETCH_RETRY_DELAY)
            else:
                result = job(**opts)
            detail = " ".join(f"{k}={v}" for k, v in result.items() if k != "checks")
            row.detail = detail[:4000]
            if result.get("skipped"):
                row.status = JobRun.Status.SKIPPED
            duration = time.monotonic() - start
            ok = result.get("ok", True)
            logger.info("step=%s %s duration=%.1fs %s",
                        name, "OK" if ok else "FAIL", duration, detail)
            return StepResult(name, ok or name in ADVISORY, detail, duration)
    except CrawlBlocked as exc:
        # record_job already stored this as SKIPPED. Reported ok=True so the tick
        # continues to the steps that need no network.
        duration = time.monotonic() - start
        logger.info("step=%s SKIPPED duration=%.1fs %s", name, duration, exc)
        return StepResult(name, True, f"skipped: {exc}"[:500], duration, skipped=True)
    except Exception as exc:  # noqa: BLE001
        duration = time.monotonic() - start
        logger.exception("event=pipeline_step_failed step=%s duration_s=%.1f error=%s",
                         name, duration, exc)
        return StepResult(name, name in ADVISORY, str(exc)[:500], duration)


def record_skipped(name: str, reason: str) -> StepResult:
    """Persist the fact that a step did not run because a prerequisite failed.

    A skipped step and a successful one look identical when only failures are
    recorded, which is exactly how stale analytics get mistaken for fresh ones.
    """
    now = timezone.now()
    JobRun.objects.create(name=name, status=JobRun.Status.SKIPPED, started_at=now,
                          finished_at=now, duration_s=0.0, detail=reason)
    logger.warning("step=%s SKIPPED (%s)", name, reason)
    return StepResult(name, False, f"skipped: {reason}", 0.0, skipped=True)


def run(*, cadence: str | None = None, steps=None, skip_fetch: bool = False,
        job_opts: dict | None = None,
        triggered_by: str = JobRun.Trigger.SCHEDULER) -> Report:
    """Run a cadence (or an explicit step list) in canonical order.

    ``job_opts`` maps a job name to the keyword options it should receive.
    """
    job_opts = job_opts or {}
    if steps is not None:
        enabled = set(steps)
        incremental_deals = False
    else:
        cadence = cadence or "full"
        if cadence not in CADENCES:
            raise ValueError(f"unknown cadence {cadence!r}; choices: {', '.join(CADENCES)}")
        enabled = set(CADENCES[cadence])
        # Only the hot tick rescores incrementally; every other cadence that
        # touches the board wants the full rebuild that catches cohorts nothing
        # fetched (an ad going stale, a peer being delisted).
        incremental_deals = cadence == "hot"
    if skip_fetch:
        enabled.discard("fetch")

    report = Report(started_at=timezone.now())
    logger.info("pipeline start cadence=%s steps=[%s]", cadence or "explicit",
                ",".join(s for s in STEP_ORDER if s in enabled))

    failed: set[str] = set()
    for name in STEP_ORDER:
        if name not in enabled:
            continue
        step_opts = dict(job_opts.get(name, {}))
        if name == "deal_scores":
            step_opts.setdefault("incremental", incremental_deals)
        # A prerequisite only blocks when it was actually part of this run:
        # `warm` has no fetch, and its steps must not be skipped for one that
        # never ran.
        blocker = next(
            (p for p in DEPENDS_ON.get(name, ()) if p in failed and p in enabled), None
        )
        if blocker is None and name == "deal_scores" and step_opts["incremental"] \
                and "fetch" in failed:
            blocker = "fetch"
        if blocker:
            result = record_skipped(name, f"prerequisite {blocker!r} failed")
        else:
            result = run_step(name, triggered_by=triggered_by, **step_opts)
        report.steps.append(result)
        if not result.ok:
            failed.add(name)

    report.finished_at = timezone.now()
    logger.info("%s", report.summary())
    return report
