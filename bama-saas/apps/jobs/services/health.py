"""Crawl health checks over data the pipeline already records.

Every failure mode below was already *detectable* — ``FetchRun`` stores status,
``stop_reason`` and ``reached_end``, ``PageCoverage`` stores which rank ranges
were read, ``IngestReject`` stores every quarantined payload with its rule. What
was missing was anything that reads them. The documented answer to "did Bama
change their schema?" was a SQL query an operator was expected to remember to
run, which means in practice nobody would, and the crawler could rot for days
behind a green-looking API.

So this module adds no schema and no crawl load. It is a set of pure queries
returning a list of :class:`Check`, consumed by the ``crawl_health`` command
(non-zero exit on failure, so cron/CI can gate on it) and by an operator
endpoint.

Severity is binary on purpose. A check is either OK or it is not, and anything
not OK needs a human. Graded severities invite "warning" states that are quietly
tolerated forever, which is the failure this module exists to end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Count, Sum
from django.utils import timezone

from apps.core.models import FetchRun, IngestReject
from apps.jobs.services.coverage import find_gaps, known_feed_depth

# A sweep runs every 6 h (see deploy/worker). Two missed cycles is a problem,
# not a blip — one late sweep can be a slow feed or a restart.
SWEEP_MAX_AGE_HOURS = 13.0

# Window for "recent" run failures and reject-rate comparison.
RECENT_HOURS = 24.0

# A single rule firing this many times its 7-day baseline means Bama changed
# something. Below this, quarantine noise is normal.
REJECT_SPIKE_FACTOR = 3.0

# Ignore spikes on rules that barely fire; 3x of two rejects is not a signal.
REJECT_SPIKE_MIN_COUNT = 20


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    # Numbers behind the verdict, so the endpoint is useful without a re-query.
    data: dict = field(default_factory=dict)


def check_sweep_freshness(now=None) -> Check:
    """A completed sweep is the only proof of full-feed coverage."""
    now = now or timezone.now()
    last = (
        FetchRun.objects.filter(reached_end=True, status=FetchRun.Status.SUCCEEDED)
        .order_by("-started_at")
        .values_list("started_at", "deepest_rank")
        .first()
    )
    if last is None:
        return Check(
            "sweep_freshness", False,
            "No sweep has ever reached the end of the feed. Nothing can be "
            "proven about coverage, and ad removal detection stays disabled.",
        )
    started_at, deepest = last
    age_h = (now - started_at).total_seconds() / 3600.0
    ok = age_h <= SWEEP_MAX_AGE_HOURS
    return Check(
        "sweep_freshness", ok,
        f"Last completed sweep {age_h:.1f}h ago (limit {SWEEP_MAX_AGE_HOURS}h), "
        f"deepest rank {deepest}.",
        {"age_hours": round(age_h, 2), "deepest_rank": deepest},
    )


def check_failed_runs(now=None) -> Check:
    """Any FAILED fetch in the window is worth a look."""
    now = now or timezone.now()
    since = now - timedelta(hours=RECENT_HOURS)
    rows = list(
        FetchRun.objects.filter(started_at__gte=since, status=FetchRun.Status.FAILED)
        .order_by("-started_at")
        .values_list("mode", "error")[:5]
    )
    total = FetchRun.objects.filter(
        started_at__gte=since, status=FetchRun.Status.FAILED
    ).count()
    if not total:
        return Check("failed_runs", True, f"No failed runs in {RECENT_HOURS:.0f}h.")
    sample = "; ".join(f"{mode}: {(err or '')[:120]}" for mode, err in rows)
    return Check(
        "failed_runs", False,
        f"{total} failed run(s) in {RECENT_HOURS:.0f}h. {sample}",
        {"count": total},
    )


def check_reject_spike(now=None) -> Check:
    """A jump in one rule id is how a Bama schema change announces itself."""
    now = now or timezone.now()
    since = now - timedelta(hours=RECENT_HOURS)
    baseline_since = now - timedelta(days=7)

    recent = dict(
        IngestReject.objects.filter(observed_at__gte=since)
        .values_list("rule")
        .annotate(n=Count("id"))
    )
    if not recent:
        return Check("reject_spike", True, f"No rejects in {RECENT_HOURS:.0f}h.")

    baseline = dict(
        IngestReject.objects.filter(
            observed_at__gte=baseline_since, observed_at__lt=since
        )
        .values_list("rule")
        .annotate(n=Count("id"))
    )
    # Baseline is 6 days of history vs a 1-day window; compare like with like.
    baseline_days = max(
        1.0, (since - baseline_since).total_seconds() / 86400.0
    )
    window_days = RECENT_HOURS / 24.0

    spikes = []
    for rule, count in recent.items():
        if count < REJECT_SPIKE_MIN_COUNT:
            continue
        expected = (baseline.get(rule, 0) / baseline_days) * window_days
        # An unseen rule firing at volume is the strongest possible signal.
        if expected == 0 or count >= expected * REJECT_SPIKE_FACTOR:
            spikes.append((rule, count, round(expected, 1)))

    if not spikes:
        return Check(
            "reject_spike", True,
            f"{sum(recent.values())} reject(s) in {RECENT_HOURS:.0f}h, all within "
            f"{REJECT_SPIKE_FACTOR:g}x baseline.",
        )
    detail = "; ".join(
        f"{rule}: {count} vs {exp} expected" for rule, count, exp in spikes
    )
    return Check(
        "reject_spike", False,
        f"Ingest reject spike — Bama likely changed their payload. {detail}",
        {"spikes": [{"rule": r, "count": c, "expected": e} for r, c, e in spikes]},
    )


def check_coverage_gaps(now=None) -> Check:
    """Rank ranges nobody read recently — the deletion-driven loss case."""
    now = now or timezone.now()
    since = now - timedelta(hours=RECENT_HOURS)
    depth = known_feed_depth()
    gaps = find_gaps(since=since, max_rank=depth)
    if not gaps:
        return Check(
            "coverage_gaps", True,
            f"No uncovered rank ranges in {RECENT_HOURS:.0f}h "
            f"(ceiling {depth or 'unknown'}).",
        )
    missing = sum(hi - lo + 1 for lo, hi in gaps)
    preview = ", ".join(f"{lo}-{hi}" for lo, hi in gaps[:5])
    return Check(
        "coverage_gaps", False,
        f"{len(gaps)} uncovered rank range(s), ~{missing} ad slots: {preview}. "
        f"Run crawl_gaps.",
        {"gap_count": len(gaps), "missing_ranks": missing},
    )


def check_ingest_progress(now=None) -> Check:
    """A crawler that runs but stores nothing is worse than one that crashes.

    A silent ban or a payload-shape change shows up here first: runs keep
    succeeding, pages keep being fetched, and ``created + updated`` goes to zero.
    """
    now = now or timezone.now()
    since = now - timedelta(hours=RECENT_HOURS)
    agg = FetchRun.objects.filter(
        started_at__gte=since, status=FetchRun.Status.SUCCEEDED
    ).aggregate(
        runs=Count("id"),
        fetched=Sum("fetched_count"),
        pages=Sum("pages_fetched"),
    )
    runs = agg["runs"] or 0
    fetched = agg["fetched"] or 0
    pages = agg["pages"] or 0
    if runs == 0:
        return Check(
            "ingest_progress", False,
            f"No successful run at all in {RECENT_HOURS:.0f}h — the worker is "
            f"not running.",
            {"runs": 0},
        )
    if pages > 0 and fetched == 0:
        return Check(
            "ingest_progress", False,
            f"{runs} run(s) fetched {pages} page(s) but ingested 0 ads — likely "
            f"a block or a payload-shape change.",
            {"runs": runs, "pages": pages, "fetched": 0},
        )
    return Check(
        "ingest_progress", True,
        f"{runs} successful run(s), {pages} page(s), {fetched} ad(s) ingested "
        f"in {RECENT_HOURS:.0f}h.",
        {"runs": runs, "pages": pages, "fetched": fetched},
    )


CHECKS = (
    check_sweep_freshness,
    check_failed_runs,
    check_reject_spike,
    check_coverage_gaps,
    check_ingest_progress,
)


def run_checks(now=None) -> list[Check]:
    """Run every check. A broken check reports itself rather than exploding."""
    results = []
    for check in CHECKS:
        try:
            results.append(check(now))
        except Exception as exc:  # noqa: BLE001 — a monitor must not crash
            results.append(
                Check(check.__name__.removeprefix("check_"), False,
                      f"check raised {exc!r}")
            )
    return results
