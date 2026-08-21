"""Everything the worker does on a schedule, as plain functions.

Each job takes keyword options and returns a summary dict. The runner in
``pipeline.py`` wraps them in a ``JobRun`` row; nothing here knows about
management commands, stdout capture or cadences.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, F, Sum
from django.utils import timezone

from apps.core.models import (
    Ad, AdObservation, DailyInventorySnapshot, FetchRun, IngestReject, JobRun,
    ListingEpisode, MarketIndex, PageCoverage,
)
from apps.core.notify import notify_deals
from apps.core.pricing import compute_deal_scores, refresh_cohort_deal_scores
from apps.core.quality import verified
from apps.core.research import build_index
from apps.jobs.fetcher import (
    COVERAGE_WINDOW_HOURS, PAGE_SIZE, CrawlBlocked, coverage_is_complete,
    fetch_live, find_gaps, known_feed_depth, plan_backfill,
)

# ---------------------------------------------------------------------------
# Removal detection
# ---------------------------------------------------------------------------
#
# Coverage-based, not wall-clock. Any ad still listed is seen by a pass that
# walks every rank, so "absent from two consecutive complete coverage windows"
# is a *proof* of absence where "not seen for 14 days" was only ever a guess.
#
# Two windows rather than one because a single pass can miss an ad legitimately:
# deletions pull later ads to lower ranks, behind pages the pass already read.
# Requiring two consecutive misses costs one window of latency and removes that
# whole class of false positive.
#
# Safety property: unless BOTH windows are provably complete this marks nothing
# and says so. A stalled crawler must never be read as "the entire inventory
# disappeared" — the failure mode a wall-clock rule has.

REQUIRED_MISSED_WINDOWS = 2


def sweep_cutoff(window_hours: int = COVERAGE_WINDOW_HOURS):
    """``(cutoff, n_complete_windows)``; cutoff is None unless both are complete."""
    now = timezone.now()
    window = timedelta(hours=window_hours)
    recent_start = now - window
    older_start = now - 2 * window

    if not coverage_is_complete(since=recent_start):
        return None, 0
    if not coverage_is_complete(since=older_start, until=recent_start):
        return None, 1
    return older_start, REQUIRED_MISSED_WINDOWS


def mark_inactive(*, days: int | None = None,
                  window_hours: int = COVERAGE_WINDOW_HOURS) -> dict:
    """Flip ACTIVE ads absent from two complete coverage windows to REMOVED.

    Idempotent, and stamps ``removed_at`` with the ad's own ``last_seen_at`` (the
    best estimate of when it left). Re-seeing a removed ad flips it back.
    ``days`` is an escape hatch for the legacy wall-clock rule.
    """
    if days is not None:
        cutoff = timezone.now() - timedelta(days=days)
        basis = f"wall-clock override, last_seen < {cutoff:%Y-%m-%d %H:%M} UTC"
    else:
        cutoff, n_windows = sweep_cutoff(window_hours)
        if cutoff is None:
            return {
                "marked": 0, "windows_complete": n_windows,
                "detail": (
                    f"only {n_windows} of {REQUIRED_MISSED_WINDOWS} consecutive "
                    f"{window_hours}h windows are fully covered; cannot prove an ad "
                    f"is gone. Run the coverage job to close the uncovered ranges."
                ),
            }
        basis = (f"absent from {REQUIRED_MISSED_WINDOWS} consecutive {window_hours}h "
                 f"windows (last_seen < {cutoff:%Y-%m-%d %H:%M} UTC)")

    marked = Ad.objects.filter(
        status=Ad.Status.ACTIVE, last_seen_at__lt=cutoff
    ).update(status=Ad.Status.REMOVED, removed_at=F("last_seen_at"))
    return {"marked": marked, "detail": f"marked {marked} ad(s) REMOVED — {basis}"}


# ---------------------------------------------------------------------------
# Listing episodes
# ---------------------------------------------------------------------------


@transaction.atomic
def sync_episodes(*, limit: int | None = None) -> dict:
    """Bring episodes in line with the current state of every ad.

    Derived entirely from ``Ad``, so it is idempotent, re-runnable at any time,
    and back-fills history on first run. Deliberately a separate pass rather than
    a hook inside ingestion: an episode ends when an ad stops being seen, which
    is a conclusion no single observation can reach. Runs *after* removal
    marking for the same reason.
    """
    report = {"opened": 0, "reopened": 0, "closed": 0}
    now = timezone.now()
    open_by_ad = {e.ad_id: e for e in ListingEpisode.objects.filter(ended_at__isnull=True)}
    known = set(ListingEpisode.objects.values_list("ad_id", flat=True))

    ads = Ad.objects.all().only(
        "code", "status", "first_seen_at", "last_seen_at", "removed_at", "current_price",
    )
    if limit:
        ads = ads[:limit]

    to_create, to_update = [], []
    for ad in ads.iterator(chunk_size=1000):
        episode = open_by_ad.get(ad.code)

        if ad.status == Ad.Status.ACTIVE:
            if episode is None:
                # Either a first sighting, or the ad came back after removal — a
                # reappearance opens a NEW episode rather than resurrecting the
                # old one, because the gap in between is the interesting part.
                seen_before = ad.code in known
                to_create.append(ListingEpisode(
                    ad=ad,
                    started_at=(ad.last_seen_at or now) if seen_before else ad.first_seen_at,
                    first_price=ad.current_price, last_price=ad.current_price,
                ))
                if seen_before:
                    report["reopened"] += 1
                else:
                    report["opened"] += 1
            elif episode.last_price != ad.current_price:
                episode.last_price = ad.current_price
                to_update.append(episode)
        elif episode is not None:
            episode.ended_at = ad.removed_at or ad.last_seen_at or now
            episode.last_price = ad.current_price
            to_update.append(episode)
            report["closed"] += 1
        elif ad.code not in known:
            # Already removed before episodes existed. Its whole life is still
            # derivable, and without this the first backfill would discard every
            # ad that had already left — most of the history, and precisely the
            # population survival analysis is about.
            to_create.append(ListingEpisode(
                ad=ad, started_at=ad.first_seen_at,
                ended_at=ad.removed_at or ad.last_seen_at or now,
                first_price=ad.current_price, last_price=ad.current_price,
            ))
            report["closed"] += 1

    if to_create:
        ListingEpisode.objects.bulk_create(to_create, batch_size=500)
    if to_update:
        ListingEpisode.objects.bulk_update(to_update, ["ended_at", "last_price"], batch_size=500)

    return report


# ---------------------------------------------------------------------------
# Daily cohort snapshot and the market index
# ---------------------------------------------------------------------------


@transaction.atomic
def daily_snapshot() -> dict:
    """Rebuild today's per-cohort inventory rows.

    Idempotent for today: drops today's rows and rebuilds. Aggregated in Python
    because median has no ORM aggregate and the pull is one narrow scan.

    The cohort key must be ``year_jalali``, never the raw mixed-calendar
    ``year``: grouping on `year` split every real cohort into a Jalali half and
    a Gregorian half, so both halves carried a wrong median and no cohort could
    be matched across days by the index that reads these rows.
    """
    today = timezone.now().date()
    DailyInventorySnapshot.objects.filter(date=today).delete()

    groups: dict = defaultdict(lambda: {"prices": [], "new": 0})
    rows = verified(Ad.objects).filter(
        status=Ad.Status.ACTIVE, current_price__gt=0,
        publish_at__isnull=False, year_jalali__isnull=False,
    ).values("model_id", "variant_id", "year_jalali", "current_price", "first_seen_at")
    for r in rows:
        group = groups[(r["model_id"], r["variant_id"], r["year_jalali"])]
        if r["current_price"]:
            group["prices"].append(r["current_price"])
        if r["first_seen_at"] and r["first_seen_at"].date() == today:
            group["new"] += 1

    objs = [
        DailyInventorySnapshot(
            model_id=model_id, variant_id=variant_id, year_jalali=year, date=today,
            ad_count=len(prices), new_count=g["new"],
            median_price=int(statistics.median(prices)),
            mean_price=int(statistics.mean(prices)),
            min_price=min(prices), max_price=max(prices),
        )
        for (model_id, variant_id, year), g in groups.items()
        if (prices := g["prices"])
    ]
    if objs:
        DailyInventorySnapshot.objects.bulk_create(objs, batch_size=500)

    return {
        "slices": len(objs), "date": str(today),
        "ads": sum(o.ad_count for o in objs),
        "new_today": sum(o.new_count for o in objs),
    }


MIN_SCOPE_COHORTS = 5


def market_index() -> dict:
    """Rebuild the matched-cohort index for the market and every eligible scope.

    Must run after ``daily_snapshot``: it is chained arithmetic over exactly the
    rows that job writes. Per-brand and per-model series are only built where
    there is something to measure — below ``MIN_SCOPE_COHORTS`` an "index" would be one
    or two cars pretending to be a market.
    """
    def eligible(field: str) -> list:
        # Counted on the most recent snapshot date only: each row there is
        # exactly one live cohort, so a plain row count is the cohort count and
        # it reflects the scope's *current* breadth.
        latest = (
            DailyInventorySnapshot.objects.order_by("-date")
            .values_list("date", flat=True).first()
        )
        if latest is None:
            return []
        return list(
            DailyInventorySnapshot.objects
            .filter(date=latest, median_price__isnull=False, model_id__isnull=False)
            .exclude(**{f"{field}__isnull": True})
            .values(field).annotate(n=Count("id")).filter(n__gte=MIN_SCOPE_COHORTS)
            .values_list(field, flat=True)
        )

    points = build_index(MarketIndex.Scope.MARKET, None)
    scopes = 1
    for slug in eligible("model__brand__slug"):
        points += build_index(MarketIndex.Scope.BRAND, slug)
        scopes += 1
    for model_id in eligible("model_id"):
        points += build_index(MarketIndex.Scope.MODEL, str(model_id))
        scopes += 1
    return {"scopes": scopes, "points": points}


# ---------------------------------------------------------------------------
# Fetch, deal scores, notifications
# ---------------------------------------------------------------------------


def fetch(**opts) -> dict:
    """One live fetch. See ``apps.jobs.fetcher.fetch_live``."""
    run = fetch_live(**opts)
    return {
        "run_id": str(run.pk), "status": run.status, "stop_reason": run.stop_reason,
        "pages": run.pages_fetched, "deepest_rank": run.deepest_rank,
        "reached_end": run.reached_end, "fetched": run.fetched_count,
        "created": run.created_count, "updated": run.updated_count,
        "skipped": run.skipped_count, "price_changes": run.price_change_count,
    }


def _models_from_latest_fetch() -> list[int]:
    run = (
        FetchRun.objects.filter(source=FetchRun.Source.LIVE_FETCH,
                                status=FetchRun.Status.SUCCEEDED)
        .order_by("-finished_at", "-started_at").first()
    )
    if run is None:
        return []
    return list(
        AdObservation.objects.filter(fetch_run=run).exclude(ad__model_id=None)
        .values_list("ad__model_id", flat=True).distinct()
    )


def deal_scores(*, incremental: bool = False, model: int | None = None) -> dict:
    """Rebuild the deal board: all of it, one model, or just what the fetch touched."""
    if not incremental:
        return compute_deal_scores(model_id=model)
    model_ids = _models_from_latest_fetch()
    if not model_ids:
        return {"skipped": True, "detail": "no affected models in latest fetch"}
    return refresh_cohort_deal_scores(model_ids)


def notify(*, dry_run: bool = False) -> dict:
    """Telegram for deals clearing the notifier bars. Must follow ``deal_scores``."""
    return notify_deals(dry_run=dry_run)


# ---------------------------------------------------------------------------
# Rolling coverage repair
# ---------------------------------------------------------------------------
#
# There is no separate all-or-nothing sweep. The deep tail shows up as a gap
# once it ages out of the coverage window, and each tick walks a bounded chunk
# of it, so coverage accumulates across many short runs — none of which has to
# survive start to finish. The old ~936-page sweep completed 11 times in 28
# attempts, which is why removal detection stalled for days.


# How far past the known ceiling to look when coverage is already complete.
PROBE_PAGES = 5


def _budgeted(ranges: list[tuple[int, int]], budget: int) -> list[tuple[int, int]]:
    """Trim page ranges to at most ``budget`` pages, truncating the last one.

    Capping by *range count* bounded nothing — one range can be the entire
    900-page tail. Whatever is left over is simply still a gap next tick.
    """
    out: list[tuple[int, int]] = []
    remaining = budget
    for lo, hi in ranges:
        if remaining <= 0:
            break
        span = hi - lo + 1
        if span <= remaining:
            out.append((lo, hi))
            remaining -= span
        else:
            out.append((lo, lo + remaining - 1))
            remaining = 0
    return out


def coverage(*, since_hours: float = 24.0, max_pages: int | None = None,
             dry_run: bool = False, **fetch_opts) -> dict:
    """Refetch the feed ranges nobody covered in the recent window."""
    from django.conf import settings

    if max_pages is None:
        max_pages = settings.BAMA_COVERAGE_CHUNK_PAGES
    since = timezone.now() - timedelta(hours=since_hours)
    max_rank = known_feed_depth()
    gaps = find_gaps(since=since, max_rank=max_rank)

    if gaps:
        ranges = _budgeted(plan_backfill(gaps), max_pages)
        plan = f"{len(gaps)} rank gap(s) -> {sum(hi - lo + 1 for lo, hi in ranges)} page(s)"
    elif max_rank is None:
        return {"gaps": 0, "pages": 0, "detail": f"no coverage gaps in the last {since_hours:g}h"}
    else:
        # Coverage is complete, so the only thing left worth learning is whether
        # the feed got deeper — that is how the ratchet grows. Rank r lives on
        # page (r-1)//PAGE_SIZE, so the next unread page is max_rank//PAGE_SIZE,
        # not +1 (an earlier +1 skipped a full page on every probe).
        next_page = max_rank // PAGE_SIZE
        ranges = [(next_page, next_page + PROBE_PAGES - 1)]
        plan = f"no gaps; probing pages {ranges[0][0]}-{ranges[0][1]} past the ceiling"

    if dry_run:
        return {"gaps": len(gaps), "pages": 0, "dry_run": True, "detail": plan}

    total_pages = 0
    affected: set[int] = set()
    for start, end in ranges:
        try:
            run = fetch_live(mode="backfill", start_page=start, end_page=end, **fetch_opts)
        except CrawlBlocked:
            # Stop the whole loop, not just this range: the gate is global, so
            # every remaining range would raise the same thing. The gaps stay
            # uncovered and are re-derived next tick.
            raise
        total_pages += run.pages_fetched
        affected |= getattr(run, "affected_model_ids", set())

    scored = refresh_cohort_deal_scores(affected) if affected else {"refreshed_models": 0}
    return {
        "gaps": len(gaps), "ranges": len(ranges), "pages": total_pages,
        "rescored_models": scored["refreshed_models"], "detail": plan,
    }


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

PRUNE_DEFAULT_DAYS = 30
_PRUNE_BATCH = 5000


def _batched_delete(qs) -> int:
    deleted = 0
    pk_name = qs.model._meta.pk.name
    while True:
        ids = list(qs.values_list(pk_name, flat=True)[:_PRUNE_BATCH])
        if not ids:
            break
        n, _ = qs.model.objects.filter(pk__in=ids).delete()
        deleted += n
    return deleted


def prune(*, days: int = PRUNE_DEFAULT_DAYS, dry_run: bool = False) -> dict:
    """Drop aged provenance rows. Keeps Ad and AdVersion (the content history).

    ``PageCoverage`` has a hard retention floor of the depth window, however
    small ``days`` is: those rows *are* the proof of feed coverage, so pruning
    inside that window would lower the ceiling and silently stall removal
    detection.
    """
    from apps.jobs.fetcher import FEED_DEPTH_WINDOW_DAYS

    now = timezone.now()
    cutoff = now - timedelta(days=days)
    coverage_cutoff = min(cutoff, now - timedelta(days=FEED_DEPTH_WINDOW_DAYS))

    targets = {
        "observations": AdObservation.objects.filter(observed_at__lt=cutoff),
        "page_coverage": PageCoverage.objects.filter(fetched_at__lt=coverage_cutoff),
        "job_runs": JobRun.objects.filter(started_at__lt=cutoff),
    }
    counts = {
        "days": days, "cutoff": cutoff.isoformat(),
        "coverage_cutoff": coverage_cutoff.isoformat(), "dry_run": dry_run,
    }
    for name, qs in targets.items():
        counts[name] = qs.count() if dry_run else _batched_delete(qs)
    return counts


def reap_orphan_runs(*, now=None) -> dict:
    """Fail every RUNNING fetch/job. Safe at worker boot: nothing is live yet."""
    now = now or timezone.now()
    return {
        "fetch_runs": FetchRun.objects.filter(status=FetchRun.Status.RUNNING).update(
            status=FetchRun.Status.FAILED, stop_reason=FetchRun.StopReason.INTERRUPTED,
            finished_at=now, error="orphaned: process exited while this run was still RUNNING",
        ),
        "job_runs": JobRun.objects.filter(status=JobRun.Status.RUNNING).update(
            status=JobRun.Status.FAILED, finished_at=now,
            error="orphaned: process exited while this job was still RUNNING",
        ),
    }


# ---------------------------------------------------------------------------
# Crawl health
# ---------------------------------------------------------------------------
#
# Every failure mode below was already *detectable* — FetchRun stores status and
# stop_reason, PageCoverage stores which ranks were read, IngestReject stores
# every quarantined payload with its rule. What was missing was anything that
# reads them: the documented answer to "did Bama change their schema?" was a SQL
# query an operator was expected to remember to run, which means in practice the
# crawler could rot for days behind a green-looking API.
#
# So this adds no schema and no crawl load — pure queries. Severity is binary on
# purpose: a check is either OK or it needs a human. Graded severities invite
# "warning" states that are quietly tolerated forever.

RECENT_HOURS = 24.0

# A single rule firing this many times its 7-day baseline means Bama changed
# something. Below REJECT_SPIKE_MIN_COUNT, 3x of two rejects is not a signal.
REJECT_SPIKE_FACTOR = 3.0
REJECT_SPIKE_MIN_COUNT = 20


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    # The numbers behind the verdict, so a caller needs no re-query.
    data: dict = field(default_factory=dict)


def check_source_block(now=None) -> Check:
    """Is bama.ir refusing us, and how long until the next attempt?

    Its own check because the operator response is completely different from a
    normal failure: nothing in this codebase can fix a 403 from the source's CDN.
    """
    from apps.jobs.fetcher import consecutive_blocks, cooldown_until

    now = now or timezone.now()
    streak = consecutive_blocks()
    until = cooldown_until()
    if not streak:
        return Check("source_block", True, "bama.ir is answering; no active block.")
    mins = max(0.0, ((until - now).total_seconds() / 60) if until else 0.0)
    return Check(
        "source_block", False,
        f"bama.ir returned 403 on {streak} consecutive run(s). Next attempt in "
        f"{mins:.0f} min. The catalog is frozen until it clears; removal "
        f"detection stays paused.",
        {"consecutive_blocks": streak,
         "next_attempt_at": until.isoformat() if until else None},
    )


def check_sweep_freshness(now=None) -> Check:
    """Was the whole feed covered in the last window?

    Coverage accumulates across runs, so a feed can be fully covered by several
    partial sweeps with no run setting ``reached_end`` at all — asking for that
    flag reported permanent failure while the crawler worked correctly.
    """
    now = now or timezone.now()
    depth = known_feed_depth()
    if not depth:
        return Check(
            "sweep_freshness", False,
            "No pages fetched in the depth window, so feed depth is unknown. "
            "Nothing can be proven about coverage and removal detection stays disabled.",
        )
    gaps = find_gaps(since=now - timedelta(hours=COVERAGE_WINDOW_HOURS), max_rank=depth)
    missing = sum(hi - lo + 1 for lo, hi in gaps)
    detail = (
        f"Feed fully covered in the last {COVERAGE_WINDOW_HOURS:.0f}h (ceiling {depth})."
        if not gaps else
        f"{len(gaps)} uncovered rank range(s) (~{missing} ad slots) in the last "
        f"{COVERAGE_WINDOW_HOURS:.0f}h; removal detection is paused until closed."
    )
    return Check("sweep_freshness", not gaps, detail,
                 {"feed_depth": depth, "gap_count": len(gaps), "missing_ranks": missing})


def check_failed_runs(now=None) -> Check:
    """Any FAILED fetch in the window — except a WAF block, which has its own check.

    Blocked runs used to land here, and during the 2026-08-16 block this read
    "245 failed run(s)" for a situation with exactly one cause, burying every
    other signal on the page.
    """
    now = now or timezone.now()
    failed = FetchRun.objects.filter(
        started_at__gte=now - timedelta(hours=RECENT_HOURS), status=FetchRun.Status.FAILED
    ).exclude(stop_reason=FetchRun.StopReason.BLOCKED)
    rows = list(failed.order_by("-started_at").values_list("mode", "error")[:5])
    total = failed.count()
    if not total:
        return Check("failed_runs", True, f"No failed runs in {RECENT_HOURS:.0f}h.")
    sample = "; ".join(f"{mode}: {(err or '')[:120]}" for mode, err in rows)
    return Check("failed_runs", False,
                 f"{total} failed run(s) in {RECENT_HOURS:.0f}h. {sample}", {"count": total})


def check_reject_spike(now=None) -> Check:
    """A jump in one rule id is how a Bama schema change announces itself."""
    now = now or timezone.now()
    since = now - timedelta(hours=RECENT_HOURS)
    baseline_since = now - timedelta(days=7)

    recent = dict(
        IngestReject.objects.filter(observed_at__gte=since)
        .values_list("rule").annotate(n=Count("id"))
    )
    if not recent:
        return Check("reject_spike", True, f"No rejects in {RECENT_HOURS:.0f}h.")

    baseline = dict(
        IngestReject.objects
        .filter(observed_at__gte=baseline_since, observed_at__lt=since)
        .values_list("rule").annotate(n=Count("id"))
    )
    # Baseline is ~6 days of history vs a 1-day window; compare like with like.
    baseline_days = max(1.0, (since - baseline_since).total_seconds() / 86400.0)
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
        return Check("reject_spike", True,
                     f"{sum(recent.values())} reject(s) in {RECENT_HOURS:.0f}h, all "
                     f"within {REJECT_SPIKE_FACTOR:g}x baseline.")
    detail = "; ".join(f"{rule}: {count} vs {exp} expected" for rule, count, exp in spikes)
    return Check("reject_spike", False,
                 f"Ingest reject spike — Bama likely changed their payload. {detail}",
                 {"spikes": [{"rule": r, "count": c, "expected": e} for r, c, e in spikes]})


def check_ingest_progress(now=None) -> Check:
    """A crawler that runs but stores nothing is worse than one that crashes.

    A silent ban or a payload-shape change shows up here first: runs keep
    succeeding, pages keep being fetched, and created+updated goes to zero.
    """
    now = now or timezone.now()
    agg = FetchRun.objects.filter(
        started_at__gte=now - timedelta(hours=RECENT_HOURS), status=FetchRun.Status.SUCCEEDED
    ).aggregate(runs=Count("id"), fetched=Sum("fetched_count"), pages=Sum("pages_fetched"))
    runs, fetched, pages = agg["runs"] or 0, agg["fetched"] or 0, agg["pages"] or 0
    if runs == 0:
        return Check("ingest_progress", False,
                     f"No successful run at all in {RECENT_HOURS:.0f}h — the worker "
                     f"is not running.", {"runs": 0})
    if pages > 0 and fetched == 0:
        return Check("ingest_progress", False,
                     f"{runs} run(s) fetched {pages} page(s) but ingested 0 ads — "
                     f"likely a block or a payload-shape change.",
                     {"runs": runs, "pages": pages, "fetched": 0})
    return Check("ingest_progress", True,
                 f"{runs} successful run(s), {pages} page(s), {fetched} ad(s) ingested "
                 f"in {RECENT_HOURS:.0f}h.",
                 {"runs": runs, "pages": pages, "fetched": fetched})


# Source block first: when it is active it is the cause of everything below, and
# reading the consequences before the cause wastes the operator's time.
CHECKS = (check_source_block, check_sweep_freshness, check_failed_runs,
          check_reject_spike, check_ingest_progress)


def run_checks(now=None) -> list[Check]:
    """Run every check. A broken check reports itself rather than exploding."""
    results = []
    for check in CHECKS:
        try:
            results.append(check(now))
        except Exception as exc:  # noqa: BLE001 — a monitor must not crash
            results.append(Check(check.__name__.removeprefix("check_"), False,
                                 f"check raised {exc!r}"))
    return results


def health() -> dict:
    """Crawl health as a job. ``ok`` is False if any check failed."""
    checks = run_checks()
    return {
        "ok": all(c.ok for c in checks),
        "checks": [asdict(c) for c in checks],
    }
