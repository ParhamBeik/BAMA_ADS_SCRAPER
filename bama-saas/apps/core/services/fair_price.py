"""What a car is worth, and why.

One cohort median, one optional mileage adjustment, and the sample size behind
both. Everything price-related in the product now goes through here: the
per-listing verdict on ``/listing/:code`` and the deal board on ``/`` read the
same ``Baseline``, so the two can no longer disagree about what a car is worth.

The estimate refuses to speak below ``MIN_PEERS`` peers rather than quoting a
median of three, and the mileage adjustment is a *bucket* median — a delta
measured against peers with comparable odometers. The old fallback, an OLS fit
of price against mileage, is gone: its median r² across models was 0.185 and it
was fitted on as few as six points, and applying a line that explains 15% of the
variance to an individual car produced adjusted prices below zero and "discounts"
of 148%. An unadjusted honest number beats an adjusted invented one.

The word "sold" appears nowhere. The feed cannot tell a sale from an expiry or a
withdrawal.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Iterable

from apps.core.models import Ad
from apps.core.services.quality import verified, without_cohort_outliers

# A cohort needs this many priced peers before it has an opinion worth quoting.
MIN_PEERS = 8

# Confidence tiers. Sample size is the dominant driver of how much a cohort median
# can be trusted, so it drives the label the user sees.
_TIERS = ((40, "high"), (15, "medium"), (MIN_PEERS, "low"))

# Mileage buckets, in km. Adjustment is applied per bucket rather than as one
# straight line because depreciation is steep early and flattens later — a linear
# slope fitted across the whole range overcharges high-mileage cars and
# undercharges low-mileage ones.
MILEAGE_BUCKETS = (0, 20_000, 50_000, 100_000, 150_000, 200_000, 300_000)


def tier(n: int) -> str:
    for threshold, label in _TIERS:
        if n >= threshold:
            return label
    return "insufficient"


def bucket(mileage: int | None) -> int | None:
    if mileage is None:
        return None
    for i in range(len(MILEAGE_BUCKETS) - 1, -1, -1):
        if mileage >= MILEAGE_BUCKETS[i]:
            return MILEAGE_BUCKETS[i]
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
    """What one cohort's peers say, computed once and reused per car.

    Built from a cohort's ``(price, mileage)`` pairs. The deal board scores tens
    of thousands of cars against a few thousand cohorts, so the bucket medians
    are computed once per cohort here rather than once per car.
    """

    base: float
    peer_count: int
    bucket_medians: dict[int, tuple[float, int]] = field(default_factory=dict)

    @property
    def confidence(self) -> str:
        return tier(self.peer_count)

    def adjusted(self, mileage: int | None) -> Adjusted:
        """Fair value for a car at this odometer reading.

        No usable bucket means no adjustment — the cohort median stands on its
        own rather than being nudged by a number nobody can defend.
        """
        key = bucket(mileage)
        if key is not None:
            median, n = self.bucket_medians.get(key, (None, 0))
            if median is not None and n >= MIN_PEERS:
                delta = median - self.base
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
        bucket_medians={
            k: (statistics.median(v), len(v)) for k, v in by_bucket.items()
        },
    )


def dispersion(prices: list[int], base: float) -> float | None:
    """Median absolute deviation over the median — scale-free, outlier-resistant."""
    if not base:
        return None
    return round(statistics.median([abs(p - base) for p in prices]) / base, 4)


def cohort_peers(*, model_id: int, variant_id, year_jalali) -> list[tuple[int, int]]:
    """Priced, active, verified peers of one cohort as ``(price, mileage)`` pairs.

    Cohort outliers are excluded: a price that is not believable must not help
    define the baseline that judges believability.
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

    fair_value = adjusted.fair_value
    return {
        "code": code,
        "available": True,
        "reason": "",
        "asking": ad.current_price,
        "fair_value": fair_value,
        "gap_pct": (
            round((ad.current_price - fair_value) / fair_value * 100, 1)
            if fair_value else None
        ),
        "components": components,
        "peer_count": baseline.peer_count,
        "dispersion": dispersion([p for p, _ in peers], baseline.base),
        "confidence": baseline.confidence,
    }
