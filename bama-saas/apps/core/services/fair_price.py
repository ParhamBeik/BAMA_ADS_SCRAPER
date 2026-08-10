"""What a car is worth, and how much the asking price is likely to move.

Two questions, both answered from observed behaviour rather than opinion.

**Fair price.** The old deal score was ``discount_pct * exp(-age_days/90)``: one
mileage-adjusted discount against the cohort median, multiplied by an age decay
with no market meaning — a listing became a worse deal purely by existing longer,
even at an unchanged price. It also produced a bare number, so a user could not
tell whether 40 meant "cheap for its mileage" or "cheap because it is damaged".
This returns the components instead, because a number you can argue with is worth
more than one you cannot.

**Negotiation room.** Measured, not assumed: how far sellers in this cohort
actually moved between their first and last observed asking price, how many moved
at all, and how long they held out first. Every one of those is an observation
this system already stores; none of it is a guess about what a seller "might"
accept.

The word "sold" appears nowhere. The feed cannot tell a sale from an expiry or a
withdrawal, so the measurable event is a price *cut*, and the measurable ending is
a *delisting*.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from django.db.models import Count, F, Max, Min

from apps.core.models import Ad, ListingEpisode, PriceObservation
from apps.core.services.deal_score import MIN_FIT_R_SQUARED, mileage_slope
from apps.core.services.quality import COHORT_FLAGS, verified, verified_by_ad, without_cohort_outliers

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


def _tier(n: int) -> str:
    for threshold, label in _TIERS:
        if n >= threshold:
            return label
    return "insufficient"


def _bucket(mileage: int | None) -> int | None:
    if mileage is None:
        return None
    for i in range(len(MILEAGE_BUCKETS) - 1, -1, -1):
        if mileage >= MILEAGE_BUCKETS[i]:
            return MILEAGE_BUCKETS[i]
    return 0


@dataclass
class Component:
    """One named contribution to the estimate, in toman."""

    name: str
    amount: int
    detail: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "amount": self.amount, "detail": self.detail}


@dataclass
class FairPrice:
    code: str
    asking: int | None = None
    fair_value: int | None = None
    components: list[Component] = field(default_factory=list)
    peer_count: int = 0
    dispersion: float | None = None
    confidence: str = "insufficient"
    available: bool = False
    reason: str = ""

    def as_dict(self) -> dict:
        gap = None
        if self.asking and self.fair_value:
            gap = round((self.asking - self.fair_value) / self.fair_value * 100, 1)
        return {
            "code": self.code,
            "available": self.available,
            "reason": self.reason,
            "asking": self.asking,
            "fair_value": self.fair_value,
            "gap_pct": gap,
            "components": [c.as_dict() for c in self.components],
            "peer_count": self.peer_count,
            "dispersion": self.dispersion,
            "confidence": self.confidence,
        }


def fair_price(code: str) -> dict:
    """An explainable asking-price estimate for one listing."""
    ad = (
        verified(Ad.objects).filter(code=code)
        .select_related("model", "variant").first()
    )
    if ad is None:
        return FairPrice(code=code, reason="unknown_or_unverified_ad").as_dict()
    if not ad.model_id or not ad.current_price:
        return FairPrice(code=code, reason="missing_model_or_price").as_dict()

    # Peers exclude cohort outliers: a price that is not believable must not help
    # define the baseline that judges believability.
    peers = list(
        without_cohort_outliers(verified(Ad.objects))
        .filter(
            model_id=ad.model_id, variant_id=ad.variant_id,
            year_jalali=ad.year_jalali, current_price__gt=0,
            status=Ad.Status.ACTIVE,
        )
        .values_list("current_price", "mileage")
    )
    if len(peers) < MIN_PEERS:
        return FairPrice(
            code=code, asking=ad.current_price, peer_count=len(peers),
            reason="insufficient_peers",
        ).as_dict()

    prices = [p for p, _ in peers]
    base = statistics.median(prices)
    result = FairPrice(
        code=code, asking=ad.current_price, peer_count=len(peers),
        confidence=_tier(len(peers)), available=True,
    )
    result.components.append(Component(
        "cohort_median", int(base),
        f"median of {len(peers)} {ad.model.name_fa if ad.model else ''} peers",
    ))

    # Mileage: compare against peers in the same bucket where there are enough of
    # them, and fall back to the model-level slope otherwise. The bucket is
    # preferred because depreciation is not linear in distance.
    fair = float(base)
    bucket = _bucket(ad.mileage)
    if bucket is not None:
        same_bucket = [p for p, m in peers if _bucket(m) == bucket]
        if len(same_bucket) >= MIN_PEERS:
            adjustment = statistics.median(same_bucket) - base
            fair += adjustment
            result.components.append(Component(
                "mileage", int(adjustment),
                f"{len(same_bucket)} peers in the {bucket:,}km+ band",
            ))
        else:
            slope = mileage_slope(ad.model_id, {})
            known = [m for _, m in peers if m is not None]
            if slope is not None and known:
                reference = statistics.median(known)
                adjustment = slope * (ad.mileage - reference)
                fair += adjustment
                result.components.append(Component(
                    "mileage", int(adjustment),
                    f"fitted slope vs cohort median {int(reference):,}km "
                    f"(r²≥{MIN_FIT_R_SQUARED})",
                ))

    result.fair_value = int(fair)
    # Robust dispersion: how tightly this cohort prices, which is what tells a
    # buyer whether there is room to hunt or the market is already efficient.
    deviations = [abs(p - base) for p in prices]
    result.dispersion = round(statistics.median(deviations) / base, 4) if base else None
    return result.as_dict()


def negotiation_room(*, model_id: int, variant_id=None, year_jalali=None) -> dict:
    """How much sellers in this cohort actually conceded, and how quickly.

    Built from PriceObservation, which is change-only — one row per genuine price
    move — so "did this seller ever cut?" is directly answerable rather than
    inferred. Observations flagged by the temporal rules are excluded: a
    rial/toman unit switch reads as a 90% concession and would dominate every
    statistic here.
    """
    episodes = verified_by_ad(ListingEpisode.objects).filter(ad__model_id=model_id)
    if variant_id:
        episodes = episodes.filter(ad__variant_id=variant_id)
    if year_jalali:
        episodes = episodes.filter(ad__year_jalali=year_jalali)

    codes = list(episodes.values_list("ad_id", flat=True).distinct())
    if len(codes) < MIN_PEERS:
        return {"available": False, "reason": "insufficient_listings", "n": len(codes)}

    observations = (
        verified_by_ad(PriceObservation.objects)
        .filter(ad_id__in=codes, price__gt=0)
        .exclude(quality_flags__contains=["price_jump"])
        .values("ad_id")
        .annotate(
            first_price=Min("price"), last_price=Max("price"),
            moves=Count("id"), first_at=Min("observed_at"), last_at=Max("observed_at"),
        )
    )

    cuts, times_to_cut, total = [], [], 0
    for row in observations:
        total += 1
        if row["moves"] < 2:
            continue
        # Min/Max rather than first/last by time: the interesting quantity is the
        # full range a seller travelled, and a seller who cut then partially
        # restored still revealed how far they were willing to go.
        high, low = row["last_price"], row["first_price"]
        if high and low and high > low:
            cuts.append((high - low) / high * 100)
            span = (row["last_at"] - row["first_at"]).total_seconds() / 86400
            times_to_cut.append(round(span, 1))

    if not cuts:
        return {
            "available": True, "listings": total, "share_that_cut": 0.0,
            "median_cut_pct": None, "median_days_to_first_cut": None,
            "note": "no observed price movement in this cohort",
        }
    return {
        "available": True,
        "listings": total,
        "share_that_cut": round(len(cuts) / total, 4),
        "median_cut_pct": round(statistics.median(cuts), 2),
        "p90_cut_pct": round(sorted(cuts)[int(len(cuts) * 0.9)], 2),
        "median_days_to_first_cut": (
            round(statistics.median(times_to_cut), 1) if times_to_cut else None
        ),
        "confidence": _tier(total),
    }


def dispersion_leaderboard(*, limit: int = 20, min_peers: int = 15) -> list[dict]:
    """Where bargaining pays: cohorts whose prices are spread widest.

    A tight cohort is efficiently priced and there is little to find; a wide one
    means the same car sells for materially different money depending on who is
    selling it. This tells a buyer *where* to shop, which no per-listing score can.
    """
    rows = (
        without_cohort_outliers(verified(Ad.objects))
        .filter(status=Ad.Status.ACTIVE, current_price__gt=0, model_id__isnull=False)
        .values("model_id", "variant_id", "year_jalali", "model__name_fa")
        .annotate(n=Count("code"))
        .filter(n__gte=min_peers)
    )

    out = []
    for row in rows:
        prices = list(
            without_cohort_outliers(verified(Ad.objects))
            .filter(
                model_id=row["model_id"], variant_id=row["variant_id"],
                year_jalali=row["year_jalali"], status=Ad.Status.ACTIVE,
                current_price__gt=0,
            )
            .values_list("current_price", flat=True)
        )
        if len(prices) < min_peers:
            continue
        median = statistics.median(prices)
        if not median:
            continue
        # Median absolute deviation over the median: a scale-free spread that one
        # extreme listing cannot inflate.
        spread = statistics.median([abs(p - median) for p in prices]) / median
        out.append({
            "model_id": row["model_id"],
            "model_name": row["model__name_fa"],
            "variant_id": row["variant_id"],
            "year_jalali": row["year_jalali"],
            "peers": len(prices),
            "median_price": int(median),
            "dispersion": round(spread, 4),
        })
    out.sort(key=lambda r: -r["dispersion"])
    return out[:limit]
