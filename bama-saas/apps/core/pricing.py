"""What a car is worth, and how far below that it is listed.

One cohort median, a condition-band adjustment, a mileage-bucket adjustment,
and the sample size behind all three. The listing page and the deal board read
the *same* ``Baseline``, so the two can no longer disagree about what a car is
worth.

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
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from statistics import median

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Max
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.common.verify import MAX_PLAUSIBLE_MILEAGE, MIN_PLAUSIBLE_PRICE
from apps.core.locks import DEAL_SCORES_REBUILD, rebuild_lock
from apps.core.models import Ad, DealScoreCache
from apps.core.quality import (
    COHORT_FLAGS,
    CONDITION_BANDS,
    FLAG_OUTLIER_HIGH,
    FLAG_OUTLIER_LOW,
    FLAG_SUSPECT_INSTALLMENT,
    FLAG_SUSPECT_MISFILED,
    INSTALLMENT_WORDS_RE,
    condition_band,
    exclude_unclear_price,
    red_flags,
    verified,
    verified_by_ad,
    without_cohort_outliers,
)

# A cohort needs this many priced peers before it has an opinion worth quoting.
MIN_PEERS = 8

# Confidence tiers. Sample size dominates how much a cohort median can be
# trusted, so it drives the label the user sees.
TIERS = ((40, "high"), (15, "medium"), (MIN_PEERS, "low"))
TIER_ORDER = ("insufficient", "low", "medium", "high")

# Below MIN_PEERS a group still gets a range, labelled with its headcount and
# listed ad by ad, but no fair value, verdict or deal score. Below this it gets
# nothing: two prices are not a range.
MIN_RANGE_PEERS = 3

# Peers are like for like: same model, trim, model year and condition band.
# Mileage differs and is adjusted for. One rung on purpose: the ladder used to
# widen to "same model, any year" when a group was thin, so a 1395 car was
# priced against 1403s and 21% of the deal board was judged that way
# (production, 2026-10-12). A thin group now says it is thin instead.
PEER_LADDER = (
    ("exact", ("model_id", "variant_id", "year_jalali", "condition_band")),
)

# A widened comparison cannot claim more confidence than this however many cars
# it found. Thirty-one cars of the same model across six model years is not
# "high" confidence about a 1396 car — it is a different, blunter question, and
# the headcount alone would happily call it high.
BASIS_CONFIDENCE_CAP = {"exact": "high", "model_year": "medium", "model": "low"}

# ...but not on its own. Forty peers nobody has seen in three days is not the
# same evidence as forty seen this morning, and the tier said "high" for both:
# confidence was a pure headcount, so a scope the crawler had stopped reaching
# kept its badge indefinitely while the prices behind it went stale.
#
# Two days rather than the envelope's 13 hours. `views.FRESH_WITHIN` judges the
# *sweep* — whether the crawl as a whole is current — and one missed tick makes
# it stale. This judges a cohort's own listings, which are re-seen on their own
# schedule as the feed reorders, so a bar that tight would mark most of the
# catalogue stale on a perfectly healthy crawl.
COHORT_STALE_AFTER = timedelta(days=2)

# Mileage buckets in km. Per-bucket rather than one straight line because
# depreciation is steep early and flattens later.
#
# The top edge used to be 300k and unbounded, so 574 production rows from
# 300,000km to a 9,000,000km typo all priced against one median. Extended so the
# genuinely worn-out end is separated from the merely high, and anything past
# `MAX_PLAUSIBLE_MILEAGE` is treated as unknown rather than as a valid point.
MILEAGE_BUCKETS = (0, 20_000, 50_000, 100_000, 150_000, 200_000,
                   300_000, 400_000, 600_000)

# Measured condition haircuts are pooled across the whole catalogue, so they
# must not be recomputed per model on an incremental rescore (218 models a
# tick). Cached like `deal_window` and dropped by the same full rebuild.
_HAIRCUT_CACHE_KEY = "pricing:condition_haircuts:v1"
_MILEAGE_HAIRCUT_CACHE_KEY = "pricing:mileage_haircuts:v1"
HAIRCUT_CACHE_SECONDS = 6 * 60 * 60


def tier(n: int) -> str:
    for threshold, label in TIERS:
        if n >= threshold:
            return label
    return "insufficient"


def bucket(mileage: int | None) -> int | None:
    """Mileage bucket edge, or None when the reading cannot be believed.

    An odometer past ``MAX_PLAUSIBLE_MILEAGE`` is a typo, not a worn-out car
    (production holds a 9,000,000km row). ``verify`` flags it softly and keeps
    the ad; pricing must decline to *adjust* on it, which lands the car on the
    unadjusted cohort median rather than in the deepest bucket.
    """
    if mileage is None or mileage < 0 or mileage > MAX_PLAUSIBLE_MILEAGE:
        return None
    for edge in reversed(MILEAGE_BUCKETS):
        if mileage >= edge:
            return edge
    return 0


@dataclass
class Adjusted:
    """One car's fair value, and the adjustments that produced it."""

    fair_value: int
    adjustment: int | None = None
    bucket: int | None = None
    bucket_peers: int = 0
    band: str | None = None
    band_adjustment: int | None = None
    band_peers: int = 0
    # What this car's own damage band actually asks, as a plain median. Reported
    # only — it must never move `fair_value`, for the sign reason in `adjusted`
    # below. It answers the question a buyer asks before opening the listing:
    # "what do other cars with this much paintwork go for?", which the
    # cohort-wide median cannot answer because it pools every condition together.
    band_median: int | None = None
    # "peers" when the band's own median was thick enough, "measured" when the
    # pooled haircut stood in for it, None when neither applied.
    band_basis: str | None = None
    mileage_basis: str | None = None


@dataclass
class Baseline:
    """What one cohort's peers say, computed once per cohort and reused per car."""

    base: float
    peer_count: int
    bucket_medians: dict[int, tuple[float, int]] = field(default_factory=dict)
    band_medians: dict[str, tuple[float, int]] = field(default_factory=dict)
    # The most recent time any peer in this cohort was seen on the feed. None
    # when the caller had nothing to say about it, which must mean "no opinion"
    # and never "stale" — a caller that cannot measure freshness must not have
    # its answers silently downgraded.
    newest_seen: object | None = None

    @property
    def stale(self) -> bool:
        """Has nothing in this cohort been seen recently enough to trust it?"""
        if self.newest_seen is None:
            return False
        return timezone.now() - self.newest_seen > COHORT_STALE_AFTER

    @property
    def confidence(self) -> str:
        """Sample size, dropped one tier when the cohort itself has gone quiet.

        One tier, not straight to "insufficient": stale peers are still
        evidence, just weaker evidence, and refusing to answer would hide a
        cohort rather than qualify it.
        """
        label = tier(self.peer_count)
        if not self.stale:
            return label
        order = [name for _, name in TIERS] + ["insufficient"]
        return order[min(order.index(label) + 1, len(order) - 1)]

    def adjusted(self, mileage: int | None, band: str | None = None,
                 haircuts: dict[str, float] | None = None,
                 mileage_haircuts: dict[int, float] | None = None) -> Adjusted:
        """Fair value for this car's condition and odometer.

        Condition first, because it is the larger effect and the one the cohort
        key cannot see: a repainted car judged against clean peers reads as a
        16.5% bargain, which is simply what a repainted car costs. The
        adjustment is *always* the pooled haircut measured across the whole
        catalogue. Unknown band, no adjustment.

        It used to prefer the band's own median wherever the band had
        ``MIN_PEERS`` of its own, which inverted the sign in production: the
        damaged cars in a cohort are often its newer, lower-mileage ones, so
        ``median(band) - median(cohort)`` came out *positive* and paint damage
        raised a car's value. Measured 2026-08-28, «چند لکه رنگ» on Tondar 90
        (37 band peers) scored +20,000,000, which is what put that car at the
        top of the whole deal board. The pooled haircut cannot do that: it is
        signed by construction.

        ``band_peers`` is still reported, because how many peers shared the
        band is worth showing even when it no longer moves the number.

        Mileage then applies as an absolute delta off the cohort median, exactly
        as before. The two are treated as additive because production says they
        are: measured jointly, clean/low reads -1.4% and severe/high +13.5%,
        with the middle cells landing near the sum of the parts.
        """
        base = self.base
        band_adj: int | None = None
        band_peers = 0
        band_basis: str | None = None
        band_median: int | None = None
        if band:
            raw_median, band_peers = self.band_medians.get(band, (None, 0))
            if raw_median is not None:
                band_median = int(raw_median)
            if haircuts and band in haircuts:
                band_adj, band_basis = int(-base * haircuts[band]), "measured"

        value = base + (band_adj or 0)

        key = bucket(mileage)
        mileage_adj: int | None = None
        bucket_peers = 0
        mileage_basis: str | None = None
        if key is not None:
            med, n = self.bucket_medians.get(key, (None, 0))
            if med is not None and n >= MIN_PEERS:
                mileage_adj, bucket_peers, mileage_basis = int(med - base), n, "peers"
                value += mileage_adj
            elif mileage_haircuts and key in mileage_haircuts:
                # Thin bucket: the cars that most need a mileage correction
                # used to get none, because MIN_PEERS starved them.
                mileage_adj, mileage_basis = int(-base * mileage_haircuts[key]), "measured"
                value += mileage_adj

        # Keyword, not positional: `band_median` sits with the other band_*
        # fields rather than at the end of the dataclass, so a positional call
        # here would silently shift `band_basis` and `mileage_basis` by one.
        return Adjusted(
            fair_value=int(value), adjustment=mileage_adj, bucket=key,
            bucket_peers=bucket_peers, band=band, band_adjustment=band_adj,
            band_peers=band_peers, band_median=band_median,
            band_basis=band_basis, mileage_basis=mileage_basis,
        )


def cohort_baseline(
    peers: Iterable[tuple[int, int | None] | tuple[int, int | None, str | None]],
) -> Baseline | None:
    """Median price of a cohort plus its per-bucket and per-band medians.

    Accepts ``(price, mileage)``, ``(price, mileage, band)`` and
    ``(price, mileage, band, last_seen_at)`` so a caller that has no condition
    or no freshness to offer still gets a usable baseline — it just gets no band
    strata, no condition adjustment, and no staleness opinion. Each extra
    element is additive and optional on purpose: three callers build these
    tuples and none of them should have to be changed to add a fourth fact.
    """
    triples = [(p, m, (t[0] if t else None), (t[1] if len(t) > 1 else None))
               for p, m, *t in peers if p]
    if len(triples) < MIN_PEERS:
        return None
    by_bucket: dict[int, list[int]] = {}
    by_band: dict[str, list[int]] = {}
    for price, mileage, band, _seen in triples:
        key = bucket(mileage)
        if key is not None:
            by_bucket.setdefault(key, []).append(price)
        if band:
            by_band.setdefault(band, []).append(price)
    seen = [s for _, _, _, s in triples if s is not None]
    return Baseline(
        base=statistics.median([p for p, _, _, _ in triples]),
        peer_count=len(triples),
        bucket_medians={k: (statistics.median(v), len(v)) for k, v in by_bucket.items()},
        band_medians={k: (statistics.median(v), len(v)) for k, v in by_band.items()},
        newest_seen=max(seen) if seen else None,
    )


def condition_haircuts(rows: Iterable[dict] | None = None) -> dict[str, float]:
    """How far under its cohort each condition band trades, catalogue-wide.

    The fallback for a cohort too thin to have its own opinion about a band, and
    deliberately *measured* rather than chosen: the number comes from every
    cohort that does have ``MIN_PEERS`` in both the band and the cohort, pooled
    as the median of per-cohort ratios. A constant here would be inventing the
    haircut, which is the thing this design is trying not to do.

    Ratios, not absolute toman, so one expensive model cannot dominate the pool.
    """
    cached = cache.get(_HAIRCUT_CACHE_KEY)
    if cached is not None and rows is None:
        return cached

    if rows is None:
        rows = without_cohort_outliers(scorable_rows()).values(
            "model_id", "variant_id", "year_jalali", "current_price",
            "body_status", "cohort_flags",
        )

    by_cohort: dict[tuple, list[tuple[int, str | None]]] = defaultdict(list)
    for r in rows:
        key = (r["model_id"], r["variant_id"], r["year_jalali"])
        # Same rule as any other baseline: a price nobody believes must not help
        # measure what a band is worth.
        if None in key or set(r.get("cohort_flags") or []) & COHORT_FLAGS:
            continue
        by_cohort[key].append((r["current_price"], condition_band(r.get("body_status"))))

    ratios: dict[str, list[float]] = defaultdict(list)
    for peers in by_cohort.values():
        prices = [p for p, _ in peers if p]
        if len(prices) < MIN_PEERS:
            continue
        cohort_median = statistics.median(prices)
        if cohort_median <= 0:
            continue
        for band in CONDITION_BANDS:
            in_band = [p for p, b in peers if b == band and p]
            if len(in_band) >= MIN_PEERS:
                ratios[band].append(statistics.median(in_band) / cohort_median)

    # A *discount*: positive means the band trades under its cohort. Clean cars
    # sit marginally above, which would be a negative haircut — clamped to zero
    # rather than used to mark clean cars up, because "cheap for a clean car"
    # should stay a discount the board can find.
    haircuts = {
        band: max(0.0, round(1 - statistics.median(vals), 4))
        for band, vals in ratios.items()
    }
    haircuts = _monotone(haircuts, CONDITION_BANDS)
    cache.set(_HAIRCUT_CACHE_KEY, haircuts, HAIRCUT_CACHE_SECONDS)
    return haircuts


def _monotone(measured: dict, order, *, non_negative: bool = True) -> dict:
    """Force a measured haircut ladder to be non-decreasing along ``order``.

    Each rung is measured from a different pool of cohorts, so nothing in the
    arithmetic makes a worse car cost less — and in production it did not.
    Severity is the one thing about these ladders that is known a priori, so it
    is imposed rather than hoped for: a rung measured below the one before it is
    raised to match.

    When ``non_negative`` is True (condition bands), discounts cannot turn into
    premiums. When False (mileage buckets), low mileage can express a negative
    haircut (i.e. a premium over a worn-out cohort median).
    """
    out = {}
    floor = 0.0 if non_negative else None
    for key in order:
        if key not in measured:
            continue
        if floor is None:
            floor = measured[key]
        else:
            floor = max(floor, measured[key])
        out[key] = floor
    return out


def mileage_haircuts(rows: Iterable[dict] | None = None) -> dict[int, float]:
    """How far under (or above for low mileage) its cohort each mileage bucket trades.

    Same shape as ``condition_haircuts``: the fallback for a bucket too thin
    to have its own median. Supports signed adjustments so low-mileage cars in
    older cohorts do not receive a zero-premium penalty when their bucket is thin.
    """
    cached = cache.get(_MILEAGE_HAIRCUT_CACHE_KEY)
    if cached is not None and rows is None:
        return cached

    if rows is None:
        rows = without_cohort_outliers(scorable_rows()).values(
            "model_id", "variant_id", "year_jalali", "current_price",
            "mileage", "cohort_flags",
        )

    by_cohort: dict[tuple, list[tuple[int, int | None]]] = defaultdict(list)
    for r in rows:
        key = (r["model_id"], r["variant_id"], r["year_jalali"])
        if None in key or set(r.get("cohort_flags") or []) & COHORT_FLAGS:
            continue
        by_cohort[key].append((r["current_price"], bucket(r.get("mileage"))))

    ratios: dict[int, list[float]] = defaultdict(list)
    for peers in by_cohort.values():
        prices = [p for p, _ in peers if p]
        if len(prices) < MIN_PEERS:
            continue
        cohort_median = statistics.median(prices)
        if cohort_median <= 0:
            continue
        by_bucket: dict[int, list[int]] = defaultdict(list)
        for price, key in peers:
            if key is not None and price:
                by_bucket[key].append(price)
        for key, bucket_prices in by_bucket.items():
            if len(bucket_prices) >= MIN_PEERS:
                ratios[key].append(statistics.median(bucket_prices) / cohort_median)

    haircuts = {
        key: round(1.0 - statistics.median(vals), 4)
        for key, vals in ratios.items()
    }
    haircuts = _monotone(haircuts, MILEAGE_BUCKETS, non_negative=False)
    cache.set(_MILEAGE_HAIRCUT_CACHE_KEY, haircuts, HAIRCUT_CACHE_SECONDS)
    return haircuts


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


def scorable_rows():
    """The population every screen counts, lists, ranks and averages over.

    One definition, because it drifted apart twice already. First the per-cohort
    peer query used `current_price > 0` and no instalment filter while the board
    used the 10M sentinel and `exclude_unclear_price`, so the listing page quoted a
    median dragged down by down-payments the board had thrown out — 11.7% of
    active priced ads. Then the *browse* endpoints turned out never to have
    applied it either, which is why the app showed four different "active ads"
    totals on four screens, and why sorting the Explorer by cheapest-first
    returned eight حواله allocations priced at their deposit instead of cars.
    `views` now reads this same function; ARCHITECTURE.md asserts they agree, and this
    is what makes that true.

    Cohort outliers are deliberately NOT excluded here. They must be dropped
    from a *baseline* (an unbelievable price cannot help define believability)
    but kept as *candidates* — an outlier-low row is a genuinely underpriced car,
    and hiding it from the one board a buyer reads is the opposite of the job.
    `laddered_peers` adds that filter; `compute_deal_scores` applies it per cohort.
    """
    qs = exclude_unclear_price(
        verified(Ad.objects).filter(
            status=Ad.Status.ACTIVE,
            # A direct page check can establish unavailability while the ad
            # still remains in a previously fetched feed window.
            detail_state__in=("", "available"),
            # A cover photo the CDN now refuses; see Ad.image_dead_at.
            image_dead_at__isnull=True,
            # The 10M floor is the unit-switch sentinel, not a car.
            current_price__gt=MIN_PLAUSIBLE_PRICE,
            publish_at__isnull=False,
        )
    )
    if settings.ARCHIVE_ADMISSION_REQUIRED:
        return qs.filter(admission_state=Ad.Admission.READY)
    return qs


def cap_confidence(label: str, basis: str) -> str:
    """Never let a headcount claim more than the comparison can support."""
    cap = BASIS_CONFIDENCE_CAP.get(basis, "low")
    return label if TIER_ORDER.index(label) <= TIER_ORDER.index(cap) else cap


def strict_peers(*, model_id: int, variant_id, year_jalali, band) -> list[tuple]:
    """Every like-for-like peer of a car, however few.

    ``(price, mileage, band, last_seen_at, price_set_at, code)`` tuples, the
    shape ``cohort_baseline`` reads (it ignores the last two). Empty without a
    model, trim or year. An unrecognised condition (``band`` None) is a group of
    its own: those cars compare only with each other.
    """
    if None in (model_id, variant_id, year_jalali):
        return []
    rows = (price_set_rows(without_cohort_outliers(scorable_rows()))
            .filter(model_id=model_id, variant_id=variant_id, year_jalali=year_jalali)
            .values_list("current_price", "mileage", "last_seen_at", "price_set_at", "code",
                         "body_status"))
    return [(price, mileage, band, seen, set_at, code)
            for price, mileage, seen, set_at, code, status in rows
            if condition_band(status) == band]


# Sellers quote round numbers, so the most *common* asking price is a real
# feature of this market rather than an artefact — but only after rounding, or
# every price is its own mode. One percent of the cohort's own scale keeps the
# granularity sensible for a 200M car and a 6B one alike.
MODE_GRANULARITY = 0.01


def price_mode(prices: list[int]) -> dict:
    """The most commonly asked price, and how many asked it."""
    if not prices:
        return {}
    step = max(1, int(statistics.median(prices) * MODE_GRANULARITY))
    counts = Counter(round(p / step) * step for p in prices)
    value, n = counts.most_common(1)[0]
    return {"value": int(value), "count": n, "step": step}


def position_pct(prices: list[int], asking: int | None) -> int | None:
    """What share of the cohort is cheaper than this car, 0-100."""
    if not prices or not asking:
        return None
    return round(sum(1 for p in prices if p < asking) / len(prices) * 100)


# The gap has to clear the cohort's own noise before it is worth calling. A
# fixed percentage would label half a tight cohort overpriced and nothing in a
# loose one; half the median absolute deviation scales with the spread that is
# actually there. The floor stops a suspiciously uniform cohort from making
# every 1% wobble a verdict.
MIN_VERDICT_BAND_PCT = 3.0


def price_verdict(gap_pct: float | None, spread: float | None) -> str:
    """``below`` / ``inline`` / ``above`` — where an ask sits against fair value."""
    if gap_pct is None:
        return "unknown"
    band = max(MIN_VERDICT_BAND_PCT, (spread or 0) * 100 / 2)
    if gap_pct <= -band:
        return "below"
    if gap_pct >= band:
        return "above"
    return "inline"


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

    band = condition_band(ad.body_status)
    peers = strict_peers(model_id=ad.model_id, variant_id=ad.variant_id,
                         year_jalali=ad.year_jalali, band=band)
    basis = "exact" if peers else "none"
    baseline = cohort_baseline(peers)
    if baseline is None:
        prices = [p for p, *_ in peers]
        set_at = [t[4] for t in peers]
        if len(prices) < MIN_RANGE_PEERS:
            return {
                "code": code, "available": False, "reason": "insufficient_peers",
                "asking": ad.current_price, "peer_count": len(peers),
                "min_peers": MIN_RANGE_PEERS, "basis": basis, "condition_band": band,
            }
        # A thin group still has a range, but too few cars for a fair value or a
        # verdict. The ads it came from are listed so "based on 5 ads" can be
        # checked rather than trusted.
        return {
            "code": code, "available": True, "reason": "few_peers",
            "asking": ad.current_price, "fair_value": None, "gap_pct": None,
            "verdict": "unknown", "basis": basis, "components": [],
            "peer_count": len(peers), "confidence": "insufficient",
            "condition_band": band, "distribution": peer_distribution(prices, set_at=set_at),
            "position_pct": position_pct(prices, ad.current_price),
            "peers": [c for *_, c in peers],
        }

    # No condition haircut: every peer already shares this car's band, so the
    # median already prices the condition. Mileage still differs and adjusts.
    adjusted = baseline.adjusted(ad.mileage, None, None, mileage_haircuts())
    components = [{
        "name": "cohort_median",
        "amount": int(baseline.base),
        # Facts, not a sentence. The UI is Persian and phrases these itself, the
        # same way it does with `reason` codes and cohort flags — a prose string
        # built here arrived on screen as English inside a Persian table.
        "facts": {
            "peers": baseline.peer_count,
            "model": ad.model.name_fa if ad.model else "",
            "band": band,
        },
    }]
    if adjusted.band_adjustment is not None:
        components.append({
            "name": "condition",
            "amount": adjusted.band_adjustment,
            "facts": {
                "basis": adjusted.band_basis,
                "peers": adjusted.band_peers,
                "band": adjusted.band,
                "body_status": ad.body_status or "",
            },
        })
    if adjusted.adjustment is not None:
        components.append({
            "name": "mileage",
            "amount": adjusted.adjustment,
            "facts": {"peers": adjusted.bucket_peers, "bucket": adjusted.bucket},
        })

    prices = [p for p, *_ in peers]
    spread = dispersion(prices, baseline.base)
    gap_pct = (
        round((ad.current_price - adjusted.fair_value) / adjusted.fair_value * 100, 1)
        if adjusted.fair_value else None
    )
    return {
        "code": code,
        "available": True,
        "reason": "",
        "asking": ad.current_price,
        "fair_value": adjusted.fair_value,
        "gap_pct": gap_pct,
        # The headline. Positive `gap_pct` means the seller is asking above what
        # the peers support, so this is the one field a reader can act on
        # without reading anything else on the panel.
        "verdict": price_verdict(gap_pct, spread),
        # Which comparison produced all of the above. `exact` is like-for-like;
        # anything else widened the net and the screen has to say so.
        "basis": basis,
        "components": components,
        "peer_count": baseline.peer_count,
        "dispersion": spread,
        "confidence": cap_confidence(baseline.confidence, basis),
        # The freshness of the cohort this number was built from, as opposed to
        # the freshness of the crawl as a whole (which is what the envelope's
        # `coverage` describes). A healthy sweep can still be quoting a cohort
        # nothing has re-seen in a week.
        "cohort_stale": baseline.stale,
        "cohort_last_seen": baseline.newest_seen,
        # Where this car sits among its peers, as a shape rather than a verdict.
        # A components table answers "how was the number built"; this answers
        # "is this cheap", which is the question people actually arrive with.
        # Free: it is the same peer list the baseline was computed from.
        "distribution": peer_distribution(prices, set_at=[t[4] for t in peers]),
        # "Cheaper than 72% of comparable cars" is the sentence people actually
        # want, and it needs no arithmetic from the reader.
        "position_pct": position_pct(prices, ad.current_price),
    }


# A middle half wider than this share of the median is too loose to price a car
# by. Measured 2026-10-12: the typical strict group is 13% wide and 14% of ads
# sit in groups wider than 20%.
WIDE_RANGE_PCT = 20.0

# Two clusters: an empty stretch inside P10-P90 wider than this share of the
# median, with a real group of cars on each side. 15% of groups had one.
CLUSTER_GAP_PCT = 10.0
CLUSTER_MIN_SIDE = 3
CLUSTER_MIN_SHARE = 0.2


def price_clusters(prices: list[int]) -> dict | None:
    """The widest empty gap inside P10-P90, when it splits two real groups.

    ``None`` when there is no such gap. P10-P90 rather than the full list so a
    lone straggler at either end cannot pass for a second cluster.
    """
    if len(prices) < MIN_PEERS:
        return None
    ordered = sorted(prices)
    mid = statistics.median(ordered)
    inner = [p for p in ordered if percentile(ordered, 10) <= p <= percentile(ordered, 90)]
    best = None
    for i in range(1, len(inner)):
        gap = inner[i] - inner[i - 1]
        if best is None or gap > best[0]:
            best = (gap, i)
    if best is None or not mid or best[0] / mid * 100 <= CLUSTER_GAP_PCT:
        return None
    gap, i = best
    side = max(CLUSTER_MIN_SIDE, math.ceil(CLUSTER_MIN_SHARE * len(inner)))
    if i < side or len(inner) - i < side:
        return None
    return {"gap_from": inner[i - 1], "gap_to": inner[i],
            "below": sum(1 for p in ordered if p <= inner[i - 1]),
            "above": sum(1 for p in ordered if p >= inner[i])}


def peer_distribution(prices: list[int], *, set_at: list | None = None) -> dict:
    """The one price range every screen shows, from a group's asking prices.

    The range is P25-P75: where the middle half of real asks sit, robust to a
    junk ad at either end. p10/p90 and min/max ride along for drawing the tails.
    ``thin`` marks a group under MIN_PEERS, ``wide`` a middle half too loose to
    price by, ``clusters`` a group whose prices split into two lumps. Empty
    below MIN_RANGE_PEERS.

    Given ``set_at`` (when each price was last set), ``old_price_pct`` says how
    many of the asks are older than PEER_PRICE_FRESH_DAYS. The range stays the
    real asks — rescaling old prices by an index would invent numbers no seller
    quoted — and the share tells the reader how much of it predates the market.
    """
    if len(prices) < MIN_RANGE_PEERS:
        return {}
    mid = statistics.median(prices)
    p25, p75 = int(percentile(prices, 25)), int(percentile(prices, 75))
    width_pct = round((p75 - p25) / mid * 100, 1) if mid else None
    return {
        "min": min(prices),
        "p10": int(percentile(prices, 10)),
        "p25": p25,
        "median": int(mid),
        "p75": p75,
        "p90": int(percentile(prices, 90)),
        "max": max(prices),
        "count": len(prices),
        # Median answers "what is the middle"; mode answers "what are people
        # actually asking", and in a market that quotes round numbers those are
        # different and both worth showing.
        "mode": price_mode(prices),
        "thin": len(prices) < MIN_PEERS,
        "width_pct": width_pct,
        "wide": width_pct is not None and width_pct > WIDE_RANGE_PCT,
        "clusters": price_clusters(prices),
        "old_price_pct": _old_share(set_at),
        "old_price_days": PEER_PRICE_FRESH_DAYS,
    }


def _old_share(set_at: list | None) -> int | None:
    known = [t for t in set_at or [] if t is not None]
    if not known:
        return None
    cutoff = timezone.now() - timedelta(days=PEER_PRICE_FRESH_DAYS)
    return round(sum(1 for t in known if t < cutoff) / len(known) * 100)


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


# A price under this share of its cohort median, in an ad that talks about
# instalments, is taken to be a down payment rather than the car. 137 such ads
# on 2026-10-12, mostly dealer stock ("نقد و اقساط تا ۵ سال").
INSTALLMENT_MAX_RATIO = 0.8

# A car this far from its own group's median, outside its P10-P90, with an
# ordinary odometer for its group, and sitting inside another trim's or year's
# middle half of the same model and condition, is more likely filed under the
# wrong trim or year than priced oddly. Typical mileage is required because a
# 196,000 km car priced like an older year is simply a high-mileage car.
MISFILED_MIN_GAP = 0.20


def flag_suspects(*, model_id: int | None = None) -> dict:
    """Recompute the two suspect-price flags across scorable ads.

    Idempotent and self-clearing, like ``flag_high_outliers``: a flag set when
    the peers looked one way is removed when they no longer support it.
    """
    base = scorable_rows().filter(has_high_outlier=False)
    if model_id is not None:
        base = base.filter(model_id=model_id)
    rows = list(base.values("code", "model_id", "variant_id", "year_jalali",
                            "body_status", "mileage", "current_price", "cohort_flags"))

    cohorts: dict = defaultdict(list)
    for r in rows:
        r["band"] = condition_band(r["body_status"])
        cohorts[(r["model_id"], r["variant_id"], r["year_jalali"])].append(r["current_price"])
    medians = {k: median(v) for k, v in cohorts.items() if len(v) >= MIN_PEERS}

    # Instalment: cheap first, then read text only for those few rows.
    cheap = {r["code"] for r in rows
             if (m := medians.get((r["model_id"], r["variant_id"], r["year_jalali"])))
             and r["current_price"] < INSTALLMENT_MAX_RATIO * m}
    installment = {
        code for code, title, description in
        Ad.objects.filter(code__in=cheap).values_list("code", "title", "description")
        if INSTALLMENT_WORDS_RE.search(f"{title or ''}\n{description or ''}")
    }

    groups: dict = defaultdict(list)
    for r in rows:
        if r["code"] not in installment:
            groups[(r["model_id"], r["variant_id"], r["year_jalali"], r["band"])].append(r)
    stats = {}
    for key, peers in groups.items():
        if len(peers) < MIN_PEERS:
            continue
        prices = [r["current_price"] for r in peers]
        km = [r["mileage"] for r in peers if r["mileage"] is not None]
        stats[key] = {
            "median": median(prices), "p10": percentile(prices, 10),
            "p90": percentile(prices, 90), "p25": percentile(prices, 25),
            "p75": percentile(prices, 75),
            "km": (percentile(km, 25), percentile(km, 75)) if len(km) >= MIN_PEERS else None,
        }
    by_model_band: dict = defaultdict(list)
    for key in stats:
        by_model_band[(key[0], key[3])].append(key)

    misfiled = set()
    for key, peers in groups.items():
        own = stats.get(key)
        if own is None or own["km"] is None:
            continue
        for r in peers:
            price, km = r["current_price"], r["mileage"]
            if (own["p10"] <= price <= own["p90"]
                    or abs(price - own["median"]) < MISFILED_MIN_GAP * own["median"]
                    or km is None or not own["km"][0] <= km <= own["km"][1]):
                continue
            if any(stats[o]["p25"] <= price <= stats[o]["p75"]
                   for o in by_model_band[(key[0], key[3])] if o != key):
                misfiled.add(r["code"])

    changed = []
    for r in rows:
        flags = [f for f in (r["cohort_flags"] or [])
                 if f not in (FLAG_SUSPECT_INSTALLMENT, FLAG_SUSPECT_MISFILED)]
        if r["code"] in installment:
            flags.append(FLAG_SUSPECT_INSTALLMENT)
        if r["code"] in misfiled:
            flags.append(FLAG_SUSPECT_MISFILED)
        if sorted(flags) != sorted(r["cohort_flags"] or []):
            changed.append(Ad(code=r["code"], cohort_flags=flags))
    if changed:
        Ad.objects.bulk_update(changed, ["cohort_flags"], batch_size=500)
    return {"installment": len(installment), "misfiled": len(misfiled),
            "changed": len(changed), "model_id": model_id}


# What makes a deal, rather than a cheap number. Measured 2026-10-12 with the
# market rising ~10% a month: the deep "deals" were old prices on freshly
# bumped ads (36% of the 20-25% band had a price over 30 days old, against 4%
# at 5-10%). So a deal is a recently priced car, cheaper than most cars like it
# priced recently, by an amount its own group can explain. No fixed percentage:
# each group's spread decides what is unusual.
DEAL_PRICE_FRESH_DAYS = 14   # the deal's own price was set this recently
PEER_PRICE_FRESH_DAYS = 30   # peers it is compared with, ditto ("today's market")
DEAL_MIN_PEERS = 15          # a median worth beating needs this many fresh peers
DEAL_MAX_SPREADS = 3.0       # further below than this many robust spreads: check it


def price_set_rows(qs):
    """``qs`` annotated with ``price_set_at``: when the current price was set.

    `PriceObservation` is written only when a price changes (STORAGE-POLICY
    rule 3), so its latest row is that moment; an ad never repriced falls back
    to its first sighting. A bump does not touch it, which is the point.
    """
    return qs.annotate(price_set_at=Coalesce(Max("price_observations__observed_at"),
                                             "first_seen_at"))


def assess_deals(rows: list[dict], *, now) -> dict[str, dict]:
    """Deal verdicts for rows carrying ``price_set_at`` and ``condition_band``.

    Only cars priced below the P25 of their fresh, tight peer group are judged.
    ``deal`` when nothing explains the gap; ``check`` with the reasons when
    something does. Absent from the result: not cheaper than most peers, or no
    peer group solid enough to say.
    """
    def age(r):
        return (now - r["price_set_at"]).days if r.get("price_set_at") else None

    groups: dict = defaultdict(list)
    for r in rows:
        groups[(r["model_id"], r["variant_id"], r["year_jalali"], r["condition_band"])].append(r)

    out: dict[str, dict] = {}
    for peers in groups.values():
        fresh = [p for p in peers
                 if (a := age(p)) is not None and a <= PEER_PRICE_FRESH_DAYS
                 and not set(p["cohort_flags"] or []) & COHORT_FLAGS]
        if len(fresh) < DEAL_MIN_PEERS:
            continue
        prices = [p["current_price"] for p in fresh]
        dist = peer_distribution(prices)
        if dist["wide"] or dist["clusters"]:
            continue
        mid, p25 = dist["median"], dist["p25"]
        spread = 1.4826 * median([abs(x - mid) for x in prices]) or mid * FALLBACK_SPREAD_RATIO
        km = [p["mileage"] for p in fresh if p["mileage"] is not None]
        km_band = (percentile(km, 25), percentile(km, 75)) if len(km) >= MIN_PEERS else None
        for r in peers:
            price = r["current_price"]
            if price >= p25:
                continue
            reasons = []
            if (a := age(r)) is None or a > DEAL_PRICE_FRESH_DAYS:
                reasons.append("stale_price")
            km_ok = km_band is not None and r["mileage"] is not None \
                and km_band[0] <= r["mileage"] <= km_band[1]
            if not km_ok:
                reasons.append("atypical_mileage")
            z = (mid - price) / spread
            if z > DEAL_MAX_SPREADS:
                reasons.append("beyond_spread")
            if FLAG_SUSPECT_MISFILED in (r["cohort_flags"] or []):
                reasons.append("suspect_misfiled")
            others = [p["current_price"] for p in fresh if p is not r]
            out[r["code"]] = {
                "deal_reasons": reasons, "fresh_peers": len(fresh), "fresh_median": int(mid),
                "cheaper_than_pct": round(sum(1 for x in others if x > price) / len(others) * 100),
                "spread_z": round(z, 2),
            }
    # Text last, and only for the few candidates.
    texts = Ad.objects.filter(code__in=out).values_list(
        "code", "title", "description", "body_status")
    for code, title, description, status in texts:
        if red_flags(title=title, description=description, body_status=status):
            out[code]["deal_reasons"].append("red_flags")
    for verdict in out.values():
        verdict["deal_status"] = "check" if verdict["deal_reasons"] else "deal"
    return out


# ---------------------------------------------------------------------------
# The deal board
# ---------------------------------------------------------------------------
#
#     score = discount_pct = (peer_median - asking) / peer_median * 100
#
# Against the peer median, not `fair_value`, because the median is the number
# every surface prints beside the badge. Scoring against one and displaying the
# other made the card's own arithmetic unverifiable.

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
# Pinned at 7 days (the last freshness band that is still "recent"). Widening
# out to 30 to refill the board after scoring got stricter would put old
# listings back on the suggestions page — the thing we just stopped doing.
MAX_WINDOW_DAYS = 7
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

    Walks the window out one day at a time, up to ``MAX_WINDOW_DAYS``, and
    stops at the first width that holds ``MIN_CANDIDATES`` listings at or above
    that width's own ``CANDIDATE_PERCENTILE`` discount. A quiet day is a short
    board, not a month of stale listings.

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
        # Measured over the population the window is *for*. Leaving the
        # review-band conditions in would set the top band's discount floor from
        # rows the top band cannot contain — and repainted cars sit well below
        # their cohorts, so the floor would be pulled up by exactly the listings
        # it is meant to be independent of.
        .filter(needs_review=False)
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


def _turnover_rates() -> dict[int, dict]:
    """Per-model time-to-leave, or nothing if there is not enough history yet.

    Wrapped so a rebuild is never taken down by the liquidity join: turnover
    needs clean episode history that a fresh install simply does not have, and a
    board that refuses to build because it cannot annotate is worse than a board
    that builds without the annotation.
    """
    from apps.core import research  # local: research imports this module

    try:
        return research.turnover_rates()
    except Exception:  # pragma: no cover - defensive; the board must still build
        return {}


def compute_deal_scores(*, model_id: int | None = None,
                        liquidity: dict[int, dict] | None = None) -> dict:
    """Value every eligible ad against its peers, or one model's.

    The deal-board lock is taken before the rows are read, not just around the
    write: a slow full rebuild that read its rows, then waited for a per-model
    one to commit, would otherwise publish its older snapshot over the newer
    rows. See ``_compute_deal_scores`` for what is computed.
    """
    with transaction.atomic():
        rebuild_lock(DEAL_SCORES_REBUILD)
        return _compute_deal_scores(model_id=model_id, liquidity=liquidity)


def _compute_deal_scores(*, model_id: int | None = None,
                         liquidity: dict[int, dict] | None = None) -> dict:
    """Value every eligible ad against its peers, or one model's.

    This used to write only bargains, and the name still says "deal" because
    that is what the board reads. But refusing to store a row for a car priced
    at or above its peers meant the product could say "this is cheap" and could
    never say "this is fair" or "you are being overcharged" — and on production
    that silence covered 16,459 of 23,698 live cars. Every valued car now gets
    a row; ``discount_pct`` simply goes negative when the ask is above the peer
    median, and the board filters on the sign.

    Eligible = ACTIVE, priced, publish-complete, price is one car's cash price,
    and *some* rung of ``PEER_LADDER`` has at least ``MIN_PEERS``. Still not
    written: a non-positive fair value, or an ask below half the peer median.
    Those are typos, not prices, and no verdict on them is meaningful.

    Idempotent — a full refresh drops every row and rebuilds, a per-model
    refresh drops only that model's.

    ``liquidity`` is the per-model turnover table, accepted from the caller
    because it does not depend on ``model_id`` at all: it is one scan of the
    whole episode history and the answer is identical for every model. The
    incremental path rescores ~200 models a tick, and computing it here meant
    ~200 identical full scans per tick — see ``refresh_cohort_deal_scores``.
    """
    outliers = flag_high_outliers(model_id=model_id)
    suspects = flag_suspects(model_id=model_id)
    base = price_set_rows(scorable_rows())
    if model_id is not None:
        base = base.filter(model_id=model_id)

    rows = list(base.values(
        "code", "model_id", "variant_id", "year_jalali",
        "current_price", "first_seen_at", "last_seen_at", "mileage",
        "cohort_flags", "body_status", "price_set_at", "reposted_from_id",
    ))
    for r in rows:
        # "unknown" rather than None so the ladder does not skip the row: an
        # unrecognised condition is a peer group of its own (see strict_peers).
        r["condition_band"] = condition_band(r["body_status"]) or "unknown"
    # A repost at the same price keeps the original's price date: deleting and
    # re-listing is a bump by other means.
    originals = {o["code"]: o for o in price_set_rows(Ad.objects.filter(
        code__in={r["reposted_from_id"] for r in rows if r["reposted_from_id"]}))
        .values("code", "current_price", "price_set_at")}
    for r in rows:
        o = originals.get(r["reposted_from_id"])
        if o and o["current_price"] == r["current_price"] and o["price_set_at"]:
            r["price_set_at"] = min(r["price_set_at"] or o["price_set_at"], o["price_set_at"])
    now = timezone.now()
    verdicts = assess_deals(rows, now=now)

    # Pooled across the whole catalogue, never just this model's slice — a
    # per-model rescore would otherwise measure the haircut on a handful of rows
    # and get a different answer every tick. Cached; the full rebuild drops it.
    haircuts = condition_haircuts(rows if model_id is None else None)
    mile_haircuts = mileage_haircuts(rows if model_id is None else None)

    # How fast each model's listings leave the feed, joined onto the score so a
    # card can say whether the discount is on something that actually moves.
    # Empty until there is enough clean episode history, and a missing rate is
    # left absent rather than defaulted — "we do not know how fast this sells"
    # is not "it sells slowly".
    if liquidity is None:
        liquidity = _turnover_rates()

    # Group at every rung of the ladder in one pass, so a car whose exact cohort
    # is too thin is still compared against something rather than dropped. A
    # quarter of live listings are in that position.
    groups: dict[str, dict[tuple, list]] = {b: defaultdict(list) for b, _ in PEER_LADDER}
    for r in rows:
        for basis, fields in PEER_LADDER:
            if any(r[f] is None for f in fields):
                continue
            groups[basis][tuple(r[f] for f in fields)].append(r)

    built: dict[tuple[str, tuple], tuple | None] = {}

    def cohort_for(basis: str, key: tuple):
        """``(baseline, spread)`` for one group, computed once and reused.

        The baseline is built from unflagged peers, but every peer is still
        scored against it. Both halves matter: an outlier that helps set the
        baseline shrinks its own apparent discount, while an outlier dropped
        from the results is a genuinely underpriced car hidden from the one
        board a buyer reads.
        """
        if (basis, key) in built:
            return built[(basis, key)]
        peers = groups[basis][key]
        clean = [r for r in peers if not set(r["cohort_flags"] or []) & COHORT_FLAGS]
        baseline_rows = clean if len(clean) >= MIN_PEERS else peers
        baseline = cohort_baseline(
            [(r["current_price"], r["mileage"], condition_band(r["body_status"]),
              r["last_seen_at"])
             for r in baseline_rows]
        )
        result = None
        if baseline is not None:
            result = (baseline, dispersion(
                [r["current_price"] for r in baseline_rows if r["current_price"]],
                baseline.base,
            ))
        built[(basis, key)] = result
        return result

    objs: list[DealScoreCache] = []
    explained_low: list[Ad] = []
    cleared_low: list[Ad] = []
    for r in rows:
        found = None
        for basis, fields in PEER_LADDER:
            if any(r[f] is None for f in fields):
                continue
            got = cohort_for(basis, tuple(r[f] for f in fields))
            if got is not None:
                found = (basis, *got)
                break
        if found is None:
            continue
        basis, baseline, spread = found
        flags = set(r["cohort_flags"] or [])
        if FLAG_SUSPECT_INSTALLMENT in flags:
            # A down payment is not a deal, and no verdict on it means anything.
            continue
        mid, vid, yj = r["model_id"], r["variant_id"], r["year_jalali"]
        band = condition_band(r["body_status"])
        floor = MIN_ASK_VS_MEDIAN * baseline.base

        # Peers share this car's band, so condition is already in the median.
        adjusted = baseline.adjusted(r["mileage"], None, None, mile_haircuts)
        fair_value = adjusted.fair_value
        price = r["current_price"]
        has_low = FLAG_OUTLIER_LOW in (r["cohort_flags"] or [])
        explained = (
            price < baseline.base * 0.9
            and ((adjusted.band_adjustment or 0) + (adjusted.adjustment or 0)) != 0
            and (fair_value <= 0 or (fair_value - price) / max(fair_value, 1) * 100 <= 0)
        )
        if explained and not has_low:
            flags = [f for f in (r["cohort_flags"] or []) if f != FLAG_OUTLIER_LOW]
            flags.append(FLAG_OUTLIER_LOW)
            explained_low.append(Ad(code=r["code"], cohort_flags=flags))
        elif has_low and not explained:
            cleared_low.append(Ad(
                code=r["code"],
                cohort_flags=[f for f in (r["cohort_flags"] or [])
                              if f != FLAG_OUTLIER_LOW],
            ))
        # fair_value <= 0 means the adjustment ate the car. Bucket medians
        # cannot produce one, but the last generation shipped 123% discounts
        # because nothing ever checked.
        if fair_value <= 0 or price < floor:
            continue
        # Measured against the cohort median, which is the number every
        # surface prints next to it. It used to be measured against
        # `fair_value` while the card struck through `peer_median`, so the
        # two figures on screen could not be reconciled by the reader — a
        # Tondar 90 showed price 1.20B, median 1.20B and a badge of 9%.
        # The condition and mileage adjustments still ride along in
        # `components` and still drive the fair-price estimate on the
        # listing page; they simply no longer move the headline number.
        discount_pct = (baseline.base - price) / baseline.base * 100
        # A row is now written whatever the sign. A negative `discount_pct` is a
        # car priced *above* its peers, and that is an answer a buyer asked for
        # — "is this price fair?" is a different question from "is this a
        # bargain?", and only the second one used to be answerable. The board
        # still shows bargains only; it filters on the sign at query time
        # instead of the sign deciding whether the car is valued at all.
        first_seen = r["first_seen_at"]
        gap_pct = (
            round((price - fair_value) / fair_value * 100, 1) if fair_value else None
        )
        objs.append(DealScoreCache(
            ad_id=r["code"],
            score=round(min(100.0, discount_pct), 1),
            discount_pct=round(discount_pct, 2),
            peer_median=int(baseline.base),
            # Condition is in the peer key now, so a repainted car is judged
            # against repainted cars and needs no review of its own.
            needs_review=FLAG_SUSPECT_MISFILED in flags,
            components={
                "discount_pct": round(discount_pct, 2),
                "peer_median": int(baseline.base),
                "fair_value": fair_value,
                "price": price,
                # Against `fair_value` rather than the median, so the listing
                # page and the board agree on the verdict word even though they
                # headline different numbers.
                "gap_pct": gap_pct,
                "verdict": price_verdict(gap_pct, spread),
                # Which rung of the ladder produced this. Anything but "exact"
                # widened the comparison and the screen has to say so.
                "basis": basis,
                "age_days": (now - first_seen).days if first_seen else 0,
                "peer_count": baseline.peer_count,
                "confidence": cap_confidence(baseline.confidence, basis),
                # Why the confidence may be lower than the peer count alone
                # would suggest. Without this the badge appears to
                # contradict the number printed next to it.
                "cohort_stale": baseline.stale,
                "dispersion": spread,
                "model_id": mid,
                "variant_id": vid,
                "year_jalali": yj,
                "mileage": r["mileage"],
                "mileage_adjustment": adjusted.adjustment,
                "mileage_bucket": adjusted.bucket,
                "mileage_bucket_peers": adjusted.bucket_peers,
                "mileage_basis": adjusted.mileage_basis,
                "body_status": r["body_status"],
                "condition_band": band,
                "suspect_misfiled": FLAG_SUSPECT_MISFILED in flags,
                "price_age_days": (now - r["price_set_at"]).days if r["price_set_at"] else None,
                **verdicts.get(r["code"], {"deal_status": None}),
                "condition_adjustment": adjusted.band_adjustment,
                "condition_band_peers": adjusted.band_peers,
                # The like-for-like number: what cars with *this* much damage
                # ask, as opposed to `peer_median`, which pools every condition
                # in the cohort together.
                "condition_band_median": adjusted.band_median,
                "condition_basis": adjusted.band_basis,
                # Absent, not zero, when there is no measured rate for this
                # model: the UI must be able to tell "sells slowly" from
                # "we have not watched it long enough to say".
                **({"liquidity": liquidity[mid]} if mid in liquidity else {}),
            },
        ))

    # Inside `compute_deal_scores`' transaction and lock: readers never see
    # the board between the delete and the insert, and a concurrent rebuild
    # (hot tick, admin refresh, a full cadence) waits instead of inserting on
    # top of this one and tripping the one-row-per-ad constraint.
    if model_id is not None:
        DealScoreCache.objects.filter(ad__model_id=model_id).delete()
    else:
        DealScoreCache.objects.all().delete()
    if objs:
        DealScoreCache.objects.bulk_create(objs, batch_size=500)
    for batch in (explained_low, cleared_low):
        if batch:
            Ad.objects.bulk_update(batch, ["cohort_flags"], batch_size=500)

    # The window is measured from these rows, so it is wrong the instant they
    # are replaced. Dropped rather than recomputed here: the next reader pays
    # for it, and a rebuild that crashes afterwards leaves no stale answer.
    # Again on commit: until then other readers still see the old rows and
    # could cache a window measured from them.
    cache.delete(_WINDOW_CACHE_KEY)
    transaction.on_commit(lambda: cache.delete(_WINDOW_CACHE_KEY))
    # The haircuts are NOT dropped here: unlike the window, a full rebuild has
    # just re-measured them from the same rows and `condition_haircuts` already
    # wrote that answer through. Deleting it would force the next reader to
    # recompute the identical figure over a fresh 25k-row scan.

    return {
        "scored": len(objs),
        "condition_haircuts": haircuts,
        "mileage_haircuts": mile_haircuts,
        "min_peers": MIN_PEERS,
        "model_id": model_id,
        "outliers_flagged": outliers["flagged"],
        "outliers_cleared": outliers["cleared"],
        "suspects": suspects,
    }


def refresh_cohort_deal_scores(model_ids) -> dict:
    """Rescore just the models a fetch touched.

    The turnover table is measured once for the whole pass, not once per model.
    It is a scan of every clean episode joined to its ad, and it does not vary
    with ``model_id`` — so computing it inside the loop ran the same full scan
    once for each of the ~200 models a hot tick touches, and again on every
    coverage tick, for an answer that was identical every time.
    """
    totals = {"refreshed_models": 0, "total_scored": 0,
              "total_outliers_flagged": 0, "total_outliers_cleared": 0}
    targets = {m for m in model_ids if m is not None}
    if not targets:
        return totals
    liquidity = _turnover_rates()
    for mid in targets:
        result = compute_deal_scores(model_id=mid, liquidity=liquidity)
        totals["refreshed_models"] += 1
        totals["total_scored"] += result["scored"]
        totals["total_outliers_flagged"] += result["outliers_flagged"]
        totals["total_outliers_cleared"] += result["outliers_cleared"]
    return totals
