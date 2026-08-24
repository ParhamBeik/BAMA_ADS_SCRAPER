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

import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from statistics import median

from django.utils import timezone

from apps.core.models import Ad, DealScoreCache
from apps.core.quality import (
    COHORT_FLAGS,
    FLAG_OUTLIER_HIGH,
    exclude_unclear_price,
    verified,
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
