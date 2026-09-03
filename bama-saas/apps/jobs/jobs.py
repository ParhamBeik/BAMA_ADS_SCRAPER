"""Everything the worker does on a schedule, as plain functions.

Each job takes keyword options and returns a summary dict. The runner in
``pipeline.py`` wraps them in a ``JobRun`` row; nothing here knows about
management commands, stdout capture or cadences.
"""

from __future__ import annotations

import os
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from apps.core import research
from apps.core.models import (
    Ad,
    AdObservation,
    DailyInventorySnapshot,
    DealScoreCache,
    FetchRun,
    IngestReject,
    JobRun,
    ListingEpisode,
    MarketIndex,
    PageCoverage,
)
from apps.core.notify import deliver_alerts, notify_deals
from apps.core.pricing import compute_deal_scores, deal_window, refresh_cohort_deal_scores
from apps.core.quality import verified
from apps.core.research import build_index
from apps.jobs.fetcher import (
    COVERAGE_WINDOW_HOURS,
    FIRST_PAGE,
    PAGE_SIZE,
    CrawlBlocked,
    _fetch_lease,
    check_gate,
    coverage_is_complete,
    create_session,
    detail_says_sold,
    fetch_ad_page_with_backoff,
    fetch_live,
    fetch_page_with_backoff,
    find_gaps,
    is_waf_block,
    known_feed_depth,
    plan_backfill,
    warmup,
)
from apps.jobs.parsing import absolute_ad_url

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

# Sample bounds for the measured expiry threshold. Below the minimum the P90 is
# noise; the maximum keeps the scan bounded on a table that only grows.
MIN_TENURE_SAMPLE = 200
MAX_TENURE_SAMPLE = 20_000


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


def _expiry_threshold_days() -> float | None:
    """P90 of closed-episode tenure — the age past which leaving looks like expiry.

    Measured, not assumed. Bama does not publish its listing lifetime, and
    hardcoding a guess would put a number the product asserts as "likely
    expired" entirely at the mercy of whoever typed it. ``None`` when there is
    not enough closed history to say, in which case nothing is called expired.
    """
    tenures = [
        (ended - started).total_seconds() / 86400.0
        for started, ended in ListingEpisode.objects
        .filter(ended_at__isnull=False, started_at__isnull=False)
        .values_list("started_at", "ended_at")[:MAX_TENURE_SAMPLE]
    ]
    if len(tenures) < MIN_TENURE_SAMPLE:
        return None
    tenures.sort()
    return tenures[min(len(tenures) - 1, int(len(tenures) * 0.9))]


def _infer_reason(ad, expiry_days: float | None, reposted: set[str]) -> tuple[str, str]:
    """``(reason, confidence)`` for one ad that has left the feed.

    Ordered by how much evidence there is. A repost is close to observation — we
    have the replacement listing in hand. Everything below it is a guess, and the
    confidence column says so.
    """
    if ad.code in reposted:
        return Ad.Reason.REPOSTED, Ad.Confidence.HIGH
    if not (ad.first_seen_at and ad.last_seen_at):
        return Ad.Reason.UNKNOWN, Ad.Confidence.LOW
    tenure = (ad.last_seen_at - ad.first_seen_at).total_seconds() / 86400.0
    if expiry_days is not None and tenure >= expiry_days:
        # Sat around as long as the slowest tenth of all listings, then vanished.
        return Ad.Reason.EXPIRED, Ad.Confidence.MEDIUM
    if expiry_days is None:
        # No measured baseline to place this tenure against.
        return Ad.Reason.SOLD, Ad.Confidence.LOW
    return Ad.Reason.SOLD, Ad.Confidence.MEDIUM


def mark_inactive(*, days: int | None = None,
                  window_hours: int = COVERAGE_WINDOW_HOURS) -> dict:
    """Resolve ads that have stopped appearing into REMOVED or UNVERIFIED.

    Two outcomes, because there are two different facts:

    * Coverage over both windows was complete, so the feed was walked end to end
      twice without this ad -> REMOVED, with an inferred ``likely_reason``.
    * The ad is equally absent but coverage could not be proven -> UNVERIFIED.
      Leaving it ACTIVE is the bug this exists to fix: for over a day the app
      showed 546 cars as for sale that nothing had seen in 48 hours, because
      "we cannot prove it is gone" was being rendered as "it is still here".

    Idempotent, and stamps ``removed_at`` with the ad's own ``last_seen_at`` (the
    best estimate of when it left). Re-seeing an ad in either state flips it back
    to ACTIVE (see ``ingest._ad_defaults``). ``days`` is an escape hatch for the
    legacy wall-clock rule.
    """
    now = timezone.now()
    unproven_cutoff = now - REQUIRED_MISSED_WINDOWS * timedelta(hours=window_hours)

    if days is not None:
        cutoff = now - timedelta(days=days)
        basis = f"wall-clock override, last_seen < {cutoff:%Y-%m-%d %H:%M} UTC"
        n_windows = REQUIRED_MISSED_WINDOWS
    else:
        cutoff, n_windows = sweep_cutoff(window_hours)
        basis = (f"absent from {REQUIRED_MISSED_WINDOWS} consecutive {window_hours}h "
                 f"windows (last_seen < {cutoff:%Y-%m-%d %H:%M} UTC)" if cutoff else "")

    if cutoff is None:
        # Cannot prove absence. Say so on the rows themselves rather than
        # leaving them looking live.
        flagged = Ad.objects.filter(
            status=Ad.Status.ACTIVE, last_seen_at__lt=unproven_cutoff
        ).update(status=Ad.Status.UNVERIFIED)
        return {
            "marked": 0, "unverified": flagged, "windows_complete": n_windows,
            "detail": (
                f"only {n_windows} of {REQUIRED_MISSED_WINDOWS} consecutive "
                f"{window_hours}h windows are fully covered; cannot prove an ad is "
                f"gone. Flagged {flagged} unseen ad(s) UNVERIFIED. Run the coverage "
                f"job to close the uncovered ranges."
            ),
        }

    # Both windows are complete, so absence is proven — including for anything
    # parked in UNVERIFIED while coverage was patchy.
    stale = Ad.objects.filter(
        status__in=(Ad.Status.ACTIVE, Ad.Status.UNVERIFIED), last_seen_at__lt=cutoff
    )
    expiry_days = _expiry_threshold_days()
    reposted = set(
        Ad.objects.filter(reposted_from__in=stale.values("code"))
        .values_list("reposted_from_id", flat=True)
    )

    updates, reasons = [], defaultdict(int)
    for ad in stale.only("code", "first_seen_at", "last_seen_at").iterator(chunk_size=1000):
        ad.status = Ad.Status.REMOVED
        ad.removed_at = ad.last_seen_at
        ad.likely_reason, ad.reason_confidence = _infer_reason(ad, expiry_days, reposted)
        # `str()`, not the enum member. This dict is rendered straight into the
        # operator log, where a TextChoices key prints as `Ad.Reason.SOLD` —
        # a Python identifier on a Persian status page.
        reasons[str(ad.likely_reason)] += 1
        updates.append(ad)
    Ad.objects.bulk_update(
        updates, ["status", "removed_at", "likely_reason", "reason_confidence"],
        batch_size=500,
    )

    breakdown = ", ".join(f"{n} {reason}" for reason, n in sorted(reasons.items()))
    return {
        "marked": len(updates), "unverified": 0, "windows_complete": n_windows,
        "reasons": dict(reasons),
        "detail": (f"marked {len(updates)} ad(s) REMOVED — {basis}"
                   + (f" [{breakdown}]" if breakdown else "")),
    }


# ---------------------------------------------------------------------------
# Repost linking
# ---------------------------------------------------------------------------
#
# Bama issues a fresh ad code when a seller delists and relists, so the same car
# reads as one removal plus one arrival. That restarts the tenure clock and
# double-counts a delisting, biasing every survival curve toward "sells fast".
#
# Conservative by construction: this writes a LINK and never merges or deletes,
# so a wrong match costs one UPDATE to undo and no history is lost. The match is
# an exact content fingerprint (see parsing.listing_fingerprint), not a
# similarity score — two identically-specced cars in one city do exist, and a
# fuzzy threshold would quietly fuse them.

REPOST_WINDOW_DAYS = 30


@transaction.atomic
def link_reposts(*, window_days: int = REPOST_WINDOW_DAYS) -> dict:
    """Point newly-seen ads at the delisted ad they appear to be a relist of."""
    since = timezone.now() - timedelta(days=window_days)
    candidates = list(
        Ad.objects.filter(first_seen_at__gte=since, reposted_from__isnull=True)
        .exclude(listing_fingerprint="")
        .values_list("code", "listing_fingerprint", "first_seen_at")
    )
    if not candidates:
        return {"linked": 0, "detail": "no unlinked ads first seen in the window"}

    # One query for every predecessor of every candidate, then matched in Python:
    # a per-candidate query would be thousands of round trips on a hot path.
    # Newest-departed first, so the first match in the scan below is the closest
    # predecessor and the loop can stop there.
    prior = defaultdict(list)
    for code, fp, last_seen in (
        Ad.objects.filter(listing_fingerprint__in={fp for _, fp, _ in candidates},
                          status=Ad.Status.REMOVED,
                          # Bounded on BOTH sides. Windowing only the candidates
                          # left the predecessor search running over all history,
                          # so a listing removed two years ago could be called
                          # the origin of an ad posted today purely because the
                          # spec matched — which is a different car, not a
                          # repost, and REPOST_WINDOW_DAYS says so.
                          last_seen_at__gte=since)
        .order_by("-last_seen_at")
        .values_list("code", "listing_fingerprint", "last_seen_at")
    ):
        prior[fp].append((code, last_seen))

    linked, predecessors = [], []
    for code, fp, first_seen in candidates:
        # `last_seen <= first_seen` is what stops two cars from being fused: a
        # predecessor still live alongside this ad is a different car with the
        # same spec, not the same one relisted.
        match = next(
            (c for c, seen in prior[fp] if c != code and seen <= first_seen), None
        )
        if match is None:
            continue
        linked.append(Ad(code=code, reposted_from_id=match))
        predecessors.append(match)

    if linked:
        Ad.objects.bulk_update(linked, ["reposted_from"], batch_size=500)
        Ad.objects.filter(code__in=predecessors).update(
            likely_reason=Ad.Reason.REPOSTED, reason_confidence=Ad.Confidence.HIGH
        )
    return {"linked": len(linked),
            "detail": f"linked {len(linked)} repost(s) over {window_days}d"}


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
        elif ad.status == Ad.Status.UNVERIFIED:
            # Deliberately nothing. An episode ends when a listing is *proven*
            # gone, and UNVERIFIED means the opposite — we lost coverage and
            # cannot say. Closing it here would stamp an ended_at we never
            # observed, and those closed episodes are exactly what
            # _expiry_threshold_days measures, so unproven closures would feed
            # back into the "likely expired" threshold as if they were evidence.
            # The episode stays open until mark_inactive resolves the ad either
            # way.
            pass
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
    # UNVERIFIED counts as inventory here, unlike on the browse and deal
    # surfaces. Those answer "can I buy this?", where showing an unconfirmed
    # listing is the bug. This answers "how big is the market, at what price",
    # and dropping every ad the crawler temporarily lost sight of would report
    # our own downtime as an inventory collapse — then market_index, which is
    # chained arithmetic over these rows, would publish that collapse as a
    # price move. Same failure DEPENDS_ON exists to prevent.
    rows = verified(Ad.objects).filter(
        status__in=(Ad.Status.ACTIVE, Ad.Status.UNVERIFIED), current_price__gt=0,
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

    # Segment axes. Membership is resolved once here and handed to every
    # build_index call: it is one pass over the latest snapshot plus one over
    # the ad table, and re-deriving it per segment would repeat both for each of
    # ~15 segments. See research.cohort_segments for why membership is fixed
    # rather than recomputed per day.
    segments = research.cohort_segments()
    if segments:
        by_axis: dict[str, Counter] = defaultdict(Counter)
        for memberships in segments.values():
            for axis, key in memberships.items():
                by_axis[axis][key] += 1
        for axis in research.SEGMENT_SCOPES:
            for key, cohorts in by_axis.get(axis, {}).items():
                # Same bar the brand and model loops use: below it an "index" is
                # one or two cars pretending to be a segment.
                if cohorts < MIN_SCOPE_COHORTS:
                    continue
                points += build_index(axis, key, segments=segments)
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
        "rejected": run.skipped_count, "price_changes": run.price_change_count,
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


def alerts(*, dry_run: bool = False) -> dict:
    """Fill every user's alert feed. Must follow ``deal_scores``.

    Separate from ``notify`` rather than folded into it: that one is the
    operator's single Telegram chat and this one is every user's in-app feed.
    They read the same board through the same matcher, but a Telegram outage
    must not stop a feed row from being written, and neither should be able to
    fail the other.
    """
    return deliver_alerts(dry_run=dry_run)


def ml_train(*, only: str | None = None) -> dict:
    """Refit the learned models and let the promotion gate decide what goes live.

    A job like every other one, so "did last night's retrain run, and what did
    it score?" is a ``JobRun`` query rather than a container-log excavation —
    the provenance mechanism already exists and this costs nothing to reuse.

    Imported inside the function on purpose: ``apps.ml.train`` reaches for
    LightGBM and scikit-learn, which live in their own optional extra, and the
    worker must still start on a host that has not installed them. It refuses
    with a reason instead.
    """
    from apps.ml.train import train_all

    return train_all(only=only)


def ml_score(*, limit: int | None = None) -> dict:
    """Rescore the live catalogue with whatever models are ACTIVE.

    Must follow ``deal_scores`` for the same reason ``alerts`` does — the two
    tables are read side by side on one card, and a prediction written against
    a board the rebuild has since replaced is a disagreement the reader gets
    blamed for noticing.
    """
    from apps.ml.inference import score_all

    return score_all(limit=limit)


SOLD_PROBE_BATCH = int(os.environ.get("BAMA_SOLD_PROBE_ADS", "20"))
SOLD_PROBE_TTL = 6 * 60 * 60
SOLD_PROBE_KEY = "sold_probe:{code}"


def probe_sold() -> dict:
    """Visit bargain-board detail pages and mark ones Bama already sold.

    Feed-absence proof takes two 24h windows, so a just-sold car can sit on
    the suggestions grid until then. This checks the served board against the
    detail page (HTTP 410 / "این آگهی فروخته شد") and removes a hit immediately
    with high confidence. Capped and gated so a 403 is a FetchRun block, not a
    hammer on the shared VPS IP.
    """
    check_gate()
    now = timezone.now()
    window = deal_window(now=now)
    qs = (
        DealScoreCache.objects.filter(
            discount_pct__gt=0,
            discount_pct__lte=window["ceiling_pct"],
            discount_pct__gte=window["min_discount_pct"],
            ad__status=Ad.Status.ACTIVE,
            ad__publish_at__gte=now - timedelta(days=window["window_days"]),
        )
        .select_related("ad")
        .order_by("-score")
    )

    run = FetchRun.objects.create(
        source=FetchRun.Source.SOLD_PROBE,
        mode=FetchRun.Mode.DELTA,
        status=FetchRun.Status.RUNNING,
        started_at=now,
    )
    sold: list[Ad] = []
    probed = 0
    skipped_recent = 0
    try:
        with _fetch_lease():
            session = create_session(settings.BAMA_COOKIE or None)
            timeout = settings.BAMA_REQUEST_TIMEOUT
            warmup(session, timeout)
            for row in qs.iterator(chunk_size=50):
                if probed >= SOLD_PROBE_BATCH:
                    break
                key = SOLD_PROBE_KEY.format(code=row.ad_id)
                if cache.get(key):
                    skipped_recent += 1
                    continue
                url = absolute_ad_url(row.ad.url or row.ad.canonical_path)
                if not url:
                    continue
                try:
                    status, body = fetch_ad_page_with_backoff(
                        session, url, timeout,
                    )
                except Exception as exc:
                    if is_waf_block(exc):
                        run.status = FetchRun.Status.FAILED
                        run.stop_reason = FetchRun.StopReason.BLOCKED
                        run.error = str(exc)[:4000]
                        run.finished_at = timezone.now()
                        run.save(update_fields=[
                            "status", "stop_reason", "error", "finished_at",
                        ])
                        raise CrawlBlocked(str(exc)) from exc
                    raise
                probed += 1
                cache.set(key, 1, SOLD_PROBE_TTL)
                if not detail_says_sold(status, body):
                    continue
                ad = row.ad
                ad.status = Ad.Status.REMOVED
                ad.removed_at = now
                ad.likely_reason = Ad.Reason.SOLD
                ad.reason_confidence = Ad.Confidence.HIGH
                sold.append(ad)

        if sold:
            Ad.objects.bulk_update(
                sold, ["status", "removed_at", "likely_reason", "reason_confidence"],
                batch_size=100,
            )
            DealScoreCache.objects.filter(ad_id__in=[a.code for a in sold]).delete()

        run.status = FetchRun.Status.SUCCEEDED
        run.fetched_count = probed
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "fetched_count", "finished_at"])
    except CrawlBlocked:
        raise
    except Exception:
        run.status = FetchRun.Status.FAILED
        run.stop_reason = FetchRun.StopReason.ERROR
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "stop_reason", "finished_at"])
        raise

    return {
        "probed": probed,
        "sold": len(sold),
        "skipped_recent": skipped_recent,
        "run_id": str(run.pk),
    }


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
    # Set only on the past-the-ceiling branch below. A refusal there is the
    # feed's answer about its own depth, not a broken crawler — see fetch_live.
    probing = False

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
        probing = True

    if dry_run:
        return {"gaps": len(gaps), "pages": 0, "dry_run": True, "detail": plan}

    total_pages = 0
    affected: set[int] = set()
    for start, end in ranges:
        try:
            run = fetch_live(mode="backfill", start_page=start, end_page=end,
                             probe=probing, **fetch_opts)
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
# Feed-depth probe
# ---------------------------------------------------------------------------
#
# "What stops the fetch?" used to be answerable only by reading container logs.
# Over one week every stop was a limit *we* chose — max_ads on delta, the
# coverage chunk budget on backfill, the stale-page rule — except the genuine end
# of the feed. Nothing bama.ir does caps us. This makes that checkable on demand
# instead of inferred, and it is also the fastest way to see the depth ratchet
# disagreeing with reality, which is what froze removal detection.

PROBE_MAX_STEPS = 24


def probe_depth(*, request_timeout: int | None = None) -> dict:
    """Binary-search for the last page that still holds ads. Read-only.

    Doubling out then halving in costs ~log2(pages) requests rather than the
    ~1,160 a linear walk would, and touches no rows: this asks bama.ir where the
    feed ends and compares that with what the ratchet believes.
    """
    from django.conf import settings

    check_gate()
    timeout = int(request_timeout if request_timeout is not None
                  else settings.BAMA_REQUEST_TIMEOUT)
    session = create_session(settings.BAMA_COOKIE or None)
    warmup(session, timeout)
    requests_made = 0

    def has_ads(page: int) -> bool:
        nonlocal requests_made
        requests_made += 1
        return bool(fetch_page_with_backoff(session, page, timeout))

    if not has_ads(FIRST_PAGE):
        return {"ok": False, "detail": "page 0 is empty — the feed itself is unreadable"}

    # Double until an empty page is found, then binary-search the boundary.
    lo, hi = FIRST_PAGE, 1
    for _ in range(PROBE_MAX_STEPS):
        if not has_ads(hi):
            break
        lo, hi = hi, hi * 2
    else:
        return {"ok": False, "detail": f"no empty page within {PROBE_MAX_STEPS} doublings"}

    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if has_ads(mid):
            lo = mid
        else:
            hi = mid

    # `lo` holds ads, `lo + 1` does not, so the feed ends at rank PAGE_SIZE*(lo+1)
    # at the very most.
    observed = PAGE_SIZE * (lo + 1)
    ratchet = known_feed_depth()
    drift = None if ratchet is None else ratchet - observed
    return {
        "ok": True,
        "last_page_with_ads": lo, "first_empty_page": lo + 1,
        "feed_end_rank": observed, "ratchet": ratchet, "ratchet_drift": drift,
        "requests": requests_made,
        "detail": (
            f"feed ends after page {lo} (<= rank {observed}); "
            + ("ratchet unknown" if ratchet is None else
               f"ratchet says {ratchet} ({drift:+d})")
            + f"; {requests_made} request(s). Every other stop reason (max_ads, "
              f"max_pages, stale_pages) is a limit this app sets, not bama.ir."
        ),
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


_IMAGE_BACKFILL_BATCH = 500


def backfill_images(*, limit: int | None = None, prune: bool = True) -> dict:
    """Refill the image columns from payloads already on disk, then drop the rest.

    The image columns were added after most of the catalog was ingested, and
    they only refill when an ad is next *observed* — so 36,914 of 81,490
    production rows render "No photo" while ~78% of them carry a perfectly good
    CDN URL inside their own ``raw_payload``. Nothing needs re-crawling.

    What cannot be filled is **deleted**, not kept: the feed is crawled with
    ``image=1&priced=1``, so an ad with no photo is outside the population this
    app collects rather than a listing with one field missing. ``_photo_missing``
    is the hard verify rule that stops new ones arriving; this is the same
    decision applied to rows that predate it. Fill first and delete second —
    reversing that order would destroy the ~28.5k rows whose photos were merely
    unread.

    Runs through the same ``_image_urls`` the live path uses, so a row filled
    here and a row filled by a fetch cannot disagree. Idempotent: it only reads
    rows that have no primary image, and a second pass over a filled row is a
    no-op.

    ``prune=False`` fills only, for checking what a run would remove first.
    """
    from apps.jobs.ingest import _image_urls

    qs = (
        Ad.objects.filter(primary_image_url="", raw_payload__isnull=False)
        .order_by("code")
    )
    scanned = filled = 0
    batch: list[Ad] = []
    for ad in qs.only("code", "raw_payload", "image_count").iterator(
        chunk_size=_IMAGE_BACKFILL_BATCH
    ):
        if limit is not None and scanned >= limit:
            break
        scanned += 1
        primary, gallery = _image_urls(ad.raw_payload or {})
        if not primary:
            continue
        ad.primary_image_url = primary[:500]
        ad.image_urls = gallery
        # Only when the payload never carried a count of its own: Bama's
        # image_count is what the *ad* has, which can exceed the capped gallery.
        if not ad.image_count:
            ad.image_count = len(gallery) or None
        batch.append(ad)
        filled += 1
        if len(batch) >= _IMAGE_BACKFILL_BATCH:
            Ad.objects.bulk_update(
                batch, ["primary_image_url", "image_urls", "image_count"]
            )
            batch = []
    if batch:
        Ad.objects.bulk_update(batch, ["primary_image_url", "image_urls", "image_count"])

    # Everything still photoless after the fill genuinely has no image in its
    # payload. Batched, because CASCADE reaches observations, versions, episodes
    # and price rows, and one 8k-row DELETE takes locks for the whole statement.
    # Counted before the delete, not from its return value: `.delete()` reports
    # every CASCADEd row (observations, versions, episodes, price rows), so the
    # first production run said it pruned 155,240 when it removed 8,889 ads.
    pruned = 0
    if prune and limit is None:
        photoless = Ad.objects.filter(primary_image_url="")
        pruned = photoless.count()
        _batched_delete(photoless)

    return {"scanned": scanned, "filled": filled, "pruned": pruned,
            "remaining": Ad.objects.filter(primary_image_url="").count()}


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
