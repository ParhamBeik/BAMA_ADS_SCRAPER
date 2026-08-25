"""What a car is worth, and how far below that it is listed.

One cohort median, one optional mileage adjustment, and the sample size behind
both. The listing page and the deal board read the *same* ``Baseline``, so the
two can no longer disagree about what a car is worth.

Three things this deliberately does not do, each having produced a wrong number
in a previous generation:

* quote a median of three cars — below ``MIN_PEERS`` it refuses to speak;
* adjust by an OLS fit of price against mileage (median r² 0.185, fitted on as
  few as six points) — that produced adjusted prices below zero and "discounts"
  of 148%. The adjustment is a bucket median or nothing;
* multiply the score by ``exp(-age_days / 90)`` — an uncalibrated half-life made
  an unchanged listing a worse deal purely by existing. Age is reported as its
  own field for the reader to weigh.

The word "sold" appears nowhere: the feed cannot tell a sale from an expiry or a
withdrawal.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from statistics import median

from django.core.cache import cache
from django.utils import timezone

from apps.core.models import Ad, DealScoreCache
from apps.core.quality import (
    COHORT_FLAGS,
    FLAG_OUTLIER_HIGH,
    exclude_unclear_price,
    verified,
    verified_by_ad,
    without_cohort_outliers,
)
from apps.jobs.verify import MIN_PLAUSIBLE_PRICE

# A cohort needs this many priced peers before it has an opinion worth quoting.
MIN_PEERS = 8

# Confidence tiers. Sample size dominates how much a cohort median can be
# trusted, so it drives the label the user sees.
TIERS = ((40, "high"), (15, "medium"), (MIN_PEERS, "low"))

# Mileage buckets in km. Per-bucket rather than one straight line because
# depreciation is steep early and flattens later.
MILEAGE_BUCKETS = (0, 20_000, 50_000, 100_000, 150_000, 200_000, 300_000)


def tier(n: int) -> str:
    for threshold, label in TIERS:
        if n >= threshold:
            return label
    return "insufficient"


def bucket(mileage: int | None) -> int | None:
    if mileage is None:
        return None
    for edge in reversed(MILEAGE_BUCKETS):
        if mileage >= edge:
            return edge
    return 0


@dataclass
class Adjusted:
    """One car's fair value, and the adjustment that produced it."""

    fair_value: int
    adjustment: int | None = None
    bucket: int | None = None
    bucket_peers: int = 0


@dataclass
class Baseline:
    """What one cohort's peers say, computed once per cohort and reused per car."""

    base: float
    peer_count: int
    bucket_medians: dict[int, tuple[float, int]] = field(default_factory=dict)

    @property
    def confidence(self) -> str:
        return tier(self.peer_count)

    def adjusted(self, mileage: int | None) -> Adjusted:
        """Fair value at this odometer. No usable bucket means no adjustment —
        the cohort median stands on its own rather than being nudged by a number
        nobody can defend."""
        key = bucket(mileage)
        if key is not None:
            med, n = self.bucket_medians.get(key, (None, 0))
            if med is not None and n >= MIN_PEERS:
                delta = med - self.base
                return Adjusted(int(self.base + delta), int(delta), key, n)
        return Adjusted(int(self.base), None, key, 0)


def cohort_baseline(peers: Iterable[tuple[int, int | None]]) -> Baseline | None:
    """Median price of a cohort plus its per-bucket medians, or None if too thin."""
    pairs = [(p, m) for p, m in peers if p]
    if len(pairs) < MIN_PEERS:
        return None
    by_bucket: dict[int, list[int]] = {}
    for price, mileage in pairs:
        key = bucket(mileage)
        if key is not None:
            by_bucket.setdefault(key, []).append(price)
    return Baseline(
        base=statistics.median([p for p, _ in pairs]),
        peer_count=len(pairs),
        bucket_medians={k: (statistics.median(v), len(v)) for k, v in by_bucket.items()},
    )


def dispersion(prices: list[int], base: float) -> float | None:
    """Median absolute deviation over the median — scale-free, outlier-resistant."""
    if not base:
        return None
    return round(statistics.median([abs(p - base) for p in prices]) / base, 4)


def percentile(values: list[float], p: int) -> float:
    """The p-th percentile by nearest rank. Empty input is 0.0.

    Nearest-rank rather than interpolated: the result is used as an inclusion
    threshold compared against the same values it was drawn from, so it should
    be one of them.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def cohort_peers(*, model_id: int, variant_id, year_jalali) -> list[tuple[int, int]]:
    """Priced, active, verified ``(price, mileage)`` peers of one cohort.

    Cohort outliers excluded: a price that is not believable must not help define
    the baseline that judges believability.
    """
    return list(
        without_cohort_outliers(verified(Ad.objects))
        .filter(
            model_id=model_id, variant_id=variant_id, year_jalali=year_jalali,
            current_price__gt=0, status=Ad.Status.ACTIVE,
        )
        .values_list("current_price", "mileage")
    )


def fair_price(code: str) -> dict:
    """An explainable asking-price estimate for one listing."""
    ad = (
        verified(Ad.objects).filter(code=code)
        .select_related("model", "variant").first()
    )
    if ad is None:
        return {"code": code, "available": False, "reason": "unknown_or_unverified_ad"}
    if not ad.model_id or not ad.current_price:
        return {"code": code, "available": False, "reason": "missing_model_or_price"}

    peers = cohort_peers(
        model_id=ad.model_id, variant_id=ad.variant_id, year_jalali=ad.year_jalali,
    )
    baseline = cohort_baseline(peers)
    if baseline is None:
        return {
            "code": code, "available": False, "reason": "insufficient_peers",
            "asking": ad.current_price, "peer_count": len(peers),
            "min_peers": MIN_PEERS,
        }

    adjusted = baseline.adjusted(ad.mileage)
    components = [{
        "name": "cohort_median",
        "amount": int(baseline.base),
        "detail": (
            f"median of {baseline.peer_count} "
            f"{ad.model.name_fa if ad.model else ''} peers"
        ),
    }]
    if adjusted.adjustment is not None:
        components.append({
            "name": "mileage",
            "amount": adjusted.adjustment,
            "detail": f"{adjusted.bucket_peers} peers in the {adjusted.bucket:,}km+ band",
        })

    return {
        "code": code,
        "available": True,
        "reason": "",
        "asking": ad.current_price,
        "fair_value": adjusted.fair_value,
        "gap_pct": (
            round((ad.current_price - adjusted.fair_value) / adjusted.fair_value * 100, 1)
            if adjusted.fair_value else None
        ),
        "components": components,
        "peer_count": baseline.peer_count,
        "dispersion": dispersion([p for p, _ in peers], baseline.base),
        "confidence": baseline.confidence,
        # Where this car sits among its peers, as a shape rather than a verdict.
        # A components table answers "how was the number built"; this answers
        # "is this cheap", which is the question people actually arrive with.
        # Free: it is the same peer list the baseline was computed from.
        "distribution": peer_distribution([p for p, _ in peers]),
    }


def peer_distribution(prices: list[int]) -> dict:
    """The cohort's asking-price shape: the band, the middle, and the edges.

    p10/p90 rather than min/max define the drawn band — one typo listing at
    5.8 trillion toman would otherwise squash every real car into the left
    pixel. min/max ride along for labelling the tails honestly.
    """
    if not prices:
        return {}
    return {
        "min": min(prices),
        "p10": int(percentile(prices, 10)),
        "p25": int(percentile(prices, 25)),
        "median": int(statistics.median(prices)),
        "p75": int(percentile(prices, 75)),
        "p90": int(percentile(prices, 90)),
        "max": max(prices),
        "count": len(prices),
    }


# ---------------------------------------------------------------------------
# High-side outlier flagging
# ---------------------------------------------------------------------------
#
# Only the high side. A price far below its cohort is the underpriced car this
# product exists to find; a price far above it is a typo or a dealer fishing.
#
# Median and MAD, never mean and standard deviation: one extreme value drags the
# mean toward itself AND inflates sigma, so the outlier widens the very band
# meant to catch it.

# How many MADs above the cohort median counts as implausible. Deliberately
# loose — this is a visibility filter, not a verification rule, and a genuinely
# expensive car in a cheap cohort must survive. At a typical MAD/median of ~0.1
# this only flags above roughly 1.6x the cohort's own median.
MAD_THRESHOLD = 6.0

# A MAD of zero means more than half the cohort shares one price (small or
# heavily-rounded cohorts). Scale off the median so the band stays finite.
FALLBACK_SPREAD_RATIO = 0.1


def flag_high_outliers(*, model_id: int | None = None) -> dict:
    """Recompute ``price_outlier_high`` across active priced ads.

    Idempotent, and it *clears* the flag from rows that no longer deserve it: a
    cohort's median moves, and a flag that could only be added would leave
    listings permanently hidden by a threshold that has since passed them.
    """
    base = verified(Ad.objects).filter(status=Ad.Status.ACTIVE, current_price__gt=0)
    if model_id is not None:
        base = base.filter(model_id=model_id)

    cohorts: dict = defaultdict(list)
    for r in base.values("code", "model_id", "variant_id", "year_jalali",
                         "current_price", "cohort_flags"):
        if r["model_id"] is not None and r["year_jalali"] is not None:
            cohorts[(r["model_id"], r["variant_id"], r["year_jalali"])].append(r)

    def without_high(row):
        return Ad(code=row["code"],
                  cohort_flags=[f for f in (row["cohort_flags"] or []) if f != FLAG_OUTLIER_HIGH])

    flagged: list[Ad] = []
    cleared: list[Ad] = []
    for peers in cohorts.values():
        if len(peers) < MIN_PEERS:
            # A thinly listed model cannot be judged from price alone. Keep it
            # visible — but still clear any flag set while the cohort was large
            # enough, or a shrunk cohort would hide a listing forever.
            cleared.extend(
                without_high(r) for r in peers
                if FLAG_OUTLIER_HIGH in (r["cohort_flags"] or [])
            )
            continue
        prices = [r["current_price"] for r in peers]
        base_price = median(prices)
        mad = median([abs(p - base_price) for p in prices]) or (
            base_price * FALLBACK_SPREAD_RATIO
        )
        ceiling = base_price + MAD_THRESHOLD * mad

        for r in peers:
            has_flag = FLAG_OUTLIER_HIGH in (r["cohort_flags"] or [])
            is_high = r["current_price"] > ceiling
            if is_high and not has_flag:
                row = without_high(r)
                row.cohort_flags.append(FLAG_OUTLIER_HIGH)
                flagged.append(row)
            elif has_flag and not is_high:
                cleared.append(without_high(r))

    for batch in (flagged, cleared):
        if batch:
            Ad.objects.bulk_update(batch, ["cohort_flags"], batch_size=500)

    return {
        "cohorts": len(cohorts),
        "flagged": len(flagged),
        "cleared": len(cleared),
        "model_id": model_id,
    }


# ---------------------------------------------------------------------------
# The deal board
# ---------------------------------------------------------------------------
#
#     score = discount_pct = (fair_value - asking) / fair_value * 100

# Asking below half the peer median is a deposit or a missing-zero typo, not a
# deal. Written as a ratio rather than in MADs so a noisy cohort cannot admit a
# 50M-vs-2B row.
MIN_ASK_VS_MEDIAN = 0.5

# Above this, the gap is an attribute the cohort key cannot see far more often
# than it is a bargain: (model, variant, year) knows nothing about accident
# damage, free-zone plates or pre-sales. Those listings are not hidden — they go
# to the review band, labelled, instead of onto the page that calls them the
# best deals available.
#
# Lives here, not in the frontend where it started, because the API filters on
# it and the UI narrates it; two copies drift on the first retune. Lowered from
# 30 to 25 on 2026-08-25: the 25-50% band is populated systematically rather
# than occasionally, which is a symptom of something unresolved (a peer median
# that is not recency-weighted, or damaged/fake listings) and not a supply of
# quarter-price cars.
TRUSTED_MAX_DISCOUNT = 25.0

# --- the dynamic top-suggestions window ------------------------------------
#
# A fixed "top N by discount" board ranks a three-week-old asking price above a
# fresh one, and a fixed discount floor is either empty on a quiet day or
# thousands of rows deep on a busy one. Both thresholds are therefore measured
# from the batch actually on the board right now.

# The window grows a day at a time until it holds this many candidates. Several
# pages' worth, so the board is worth paging through, but small enough that a
# normal day resolves in a handful of days rather than falling back to a month.
MIN_CANDIDATES = 200
MAX_WINDOW_DAYS = 30
# "Top suggestions" means the best quarter of what the window holds.
CANDIDATE_PERCENTILE = 75
# Recomputed on the worker's tick anyway; five minutes keeps the page honest
# without re-running the percentile scan for every reader.
WINDOW_CACHE_SECONDS = 300
_WINDOW_CACHE_KEY = "deal_window:v1"

# Freshness bands, in days since the ad was published or last bumped. The board
# sorts by band first and discount second, so a fresh 9% deal outranks a
# three-week-old 20% one without recency having to be blended into the score.
FRESHNESS_BANDS = ((1, "today"), (3, "d1_3"), (7, "d4_7"), (14, "d8_14"))
LAST_BAND = "d15_plus"


def deal_window(*, now=None) -> dict:
    """How far back the board looks today, and how good a deal has to be.

    Walks the window out one day at a time and stops at the first width that
    holds ``MIN_CANDIDATES`` listings at or above that width's own
    ``CANDIDATE_PERCENTILE`` discount. A thin day therefore widens the window
    instead of showing three cars, and a busy day tightens it instead of
    burying today's arrivals under last month's.

    Recency is measured on ``publish_at``, never ``first_seen_at``:
    ``first_seen_at`` is when *our crawler* got there, so an old listing found
    by a deep backfill yesterday would rank as brand new. ``publish_at`` comes
    from Bama's own phrase and moves when a seller bumps the ad — which is the
    "this price was reasserted recently" signal the ordering is actually for.
    """
    cached = cache.get(_WINDOW_CACHE_KEY)
    if cached is not None:
        return cached

    now = now or timezone.now()
    rows = list(
        verified_by_ad(DealScoreCache.objects.filter(discount_pct__gt=0,
                                                     discount_pct__lte=TRUSTED_MAX_DISCOUNT))
        .filter(ad__publish_at__isnull=False)
        .values_list("ad__publish_at", "discount_pct")
    )

    window = {
        "window_days": MAX_WINDOW_DAYS,
        "min_discount_pct": 0.0,
        "ceiling_pct": TRUSTED_MAX_DISCOUNT,
        "candidates": 0,
        "scored": len(rows),
        "computed_at": now,
    }
    for days in range(1, MAX_WINDOW_DAYS + 1):
        cutoff = now - timedelta(days=days)
        inside = [d for published, d in rows if published >= cutoff]
        if not inside:
            continue
        floor = percentile(inside, CANDIDATE_PERCENTILE)
        candidates = sum(1 for d in inside if d >= floor)
        window.update(window_days=days, min_discount_pct=round(floor, 2),
                      candidates=candidates)
        if candidates >= MIN_CANDIDATES:
            break

    cache.set(_WINDOW_CACHE_KEY, window, WINDOW_CACHE_SECONDS)
    return window


def compute_deal_scores(*, model_id: int | None = None) -> dict:
    """Rebuild deal scores for every eligible ad, or one model's.

    Eligible = ACTIVE, priced, publish-complete, price is one car's cash price,
    cohort of at least ``MIN_PEERS``. Rows with a non-positive fair value, an
    asking price at or above it, or one below half the peer median are not
    written at all: a board of typos and non-deals is worse than a short board.

    Idempotent — a full refresh drops every row and rebuilds, a per-model
    refresh drops only that model's.
    """
    outliers = flag_high_outliers(model_id=model_id)
    base = exclude_unclear_price(
        verified(Ad.objects).filter(
            status=Ad.Status.ACTIVE,
            # The 10M floor is the unit-switch sentinel, not a car.
            current_price__gt=MIN_PLAUSIBLE_PRICE,
            publish_at__isnull=False,
        )
    )
    if model_id is not None:
        base = base.filter(model_id=model_id)

    rows = list(base.values(
        "code", "model_id", "variant_id", "year_jalali",
        "current_price", "first_seen_at", "mileage", "cohort_flags",
    ))

    peers_by_cohort: dict = defaultdict(list)
    for r in rows:
        if None not in (r["model_id"], r["variant_id"], r["year_jalali"]):
            peers_by_cohort[(r["model_id"], r["variant_id"], r["year_jalali"])].append(r)

    now = timezone.now()
    objs: list[DealScoreCache] = []
    for (mid, vid, yj), peers in peers_by_cohort.items():
        # The baseline is built from unflagged peers, but every peer is still
        # scored against it. Both halves matter: an outlier that helps set the
        # baseline shrinks its own apparent discount, while an outlier dropped
        # from the results is a genuinely underpriced car hidden from the one
        # board a buyer reads.
        clean = [r for r in peers if not set(r["cohort_flags"] or []) & COHORT_FLAGS]
        baseline_rows = clean if len(clean) >= MIN_PEERS else peers
        baseline = cohort_baseline(
            [(r["current_price"], r["mileage"]) for r in baseline_rows]
        )
        if baseline is None:
            continue
        spread = dispersion(
            [r["current_price"] for r in baseline_rows if r["current_price"]], baseline.base
        )
        floor = MIN_ASK_VS_MEDIAN * baseline.base

        for r in peers:
            adjusted = baseline.adjusted(r["mileage"])
            fair_value = adjusted.fair_value
            price = r["current_price"]
            # fair_value <= 0 means the adjustment ate the car. Bucket medians
            # cannot produce one, but the last generation shipped 123% discounts
            # because nothing ever checked.
            if fair_value <= 0 or price < floor:
                continue
            discount_pct = (fair_value - price) / fair_value * 100
            if discount_pct <= 0:
                continue  # priced at or above fair value: not a deal
            first_seen = r["first_seen_at"]
            objs.append(DealScoreCache(
                ad_id=r["code"],
                score=round(min(100.0, discount_pct), 1),
                discount_pct=round(discount_pct, 2),
                peer_median=int(baseline.base),
                components={
                    "discount_pct": round(discount_pct, 2),
                    "peer_median": int(baseline.base),
                    "fair_value": fair_value,
                    "price": price,
                    "age_days": (now - first_seen).days if first_seen else 0,
                    "peer_count": baseline.peer_count,
                    "confidence": baseline.confidence,
                    "dispersion": spread,
                    "model_id": mid,
                    "variant_id": vid,
                    "year_jalali": yj,
                    "mileage": r["mileage"],
                    "mileage_adjustment": adjusted.adjustment,
                    "mileage_bucket": adjusted.bucket,
                    "mileage_bucket_peers": adjusted.bucket_peers,
                },
            ))

    if model_id is not None:
        DealScoreCache.objects.filter(ad__model_id=model_id).delete()
    else:
        DealScoreCache.objects.all().delete()
    if objs:
        DealScoreCache.objects.bulk_create(objs, batch_size=500)

    # The window is measured from these rows, so it is wrong the instant they
    # are replaced. Dropped rather than recomputed here: the next reader pays
    # for it, and a rebuild that crashes afterwards leaves no stale answer.
    cache.delete(_WINDOW_CACHE_KEY)

    return {
        "scored": len(objs),
        "min_peers": MIN_PEERS,
        "model_id": model_id,
        "outliers_flagged": outliers["flagged"],
        "outliers_cleared": outliers["cleared"],
    }


def refresh_cohort_deal_scores(model_ids) -> dict:
    """Rescore just the models a fetch touched."""
    totals = {"refreshed_models": 0, "total_scored": 0,
              "total_outliers_flagged": 0, "total_outliers_cleared": 0}
    for mid in {m for m in model_ids if m is not None}:
        result = compute_deal_scores(model_id=mid)
        totals["refreshed_models"] += 1
        totals["total_scored"] += result["scored"]
        totals["total_outliers_flagged"] += result["outliers_flagged"]
        totals["total_outliers_cleared"] += result["outliers_cleared"]
    return totals
