"""Cohort-relative outlier detection: values that are plausible until you look
at their peers.

This is the layer the other two structurally cannot reach. ``verify.py`` bounds
each field against a fixed band and ``verify_temporal.py`` bounds a transition,
but a Pride advertised at 4,000,000,000 toman passes both: the price is inside
MIN/MAX_PLAUSIBLE_PRICE, and if it was wrong on the very first sighting there is
no transition to compare it to. Only its peers say it is wrong.

Two decisions carry this module.

**Median and MAD, never mean and standard deviation.** A single extreme value
drags the mean toward itself *and* inflates the standard deviation, so the
outlier widens the very band that is supposed to catch it and hides. The median
and the median absolute deviation both have a 50% breakdown point: the baseline
does not move until half the cohort is corrupt. That is also why no iterative
"remove and refit" loop is needed — one pass is already robust, and the outlier
is excluded from the baseline that judges it by construction rather than by
procedure.

**A flag is not a deletion.** A genuinely underpriced car is the single most
valuable thing this product can find, so an outlier is never dropped from the
catalog. It is excluded from the baselines it would distort (see
``quality.without_cohort_outliers``) and surfaced to the user with its reasons.
Data quality and the bargain/scam signal are the same mechanism read in two
directions.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from apps.core.models import Ad
from apps.core.services.deal_score import mileage_slope
from apps.core.services.quality import COHORT_FLAGS, FLAG_OUTLIER_HIGH, FLAG_OUTLIER_LOW, verified

# The standard consistency constant: for normally distributed data, MAD * 1.4826
# estimates the standard deviation, so 0.6745 = 1/1.4826 puts the modified
# z-score on the same scale as an ordinary one.
_MAD_TO_SIGMA = 0.6745

# Iglewicz-Hoaglin's conventional cutoff. Roughly "3.5 sigma" once the scale
# above is applied, but computed from statistics an outlier cannot move.
MODIFIED_Z_CUTOFF = 3.5

# Below this the cohort has no defensible opinion about its own spread: a MAD
# over a handful of prices is noise, and flagging against it would invent
# outliers in every thin cohort — which is most of the long tail.
MIN_COHORT_PEERS = 10

# A price must be BOTH statistically extreme and economically material to count.
#
# Measured against the live 60k-ad database, the z-score alone flagged 6.2% of the
# market, and 663 of those sat within 1.5x of their own cohort median. The reason
# is that MAD measures how tightly sellers cluster, and in a tight cohort — say
# everyone within a few percent of 500M — being 20% expensive is statistically
# extreme while being an entirely ordinary listing. Excluding those from every
# baseline would quietly discard a slice of the real market.
#
# This flag means "not believable as a market price", not "expensive". Genuine
# bargains in the believable range are the deal score's job, not this one's.
MIN_RATIO_FROM_MEDIAN = 2.0

# The flag vocabulary lives in quality.py — see the note there on why.
FLAG_HIGH = FLAG_OUTLIER_HIGH
FLAG_LOW = FLAG_OUTLIER_LOW


def _adjusted_prices(rows: list[Ad], slope: float | None) -> dict[str, float]:
    """Price normalised to the cohort's median mileage, per ad code.

    The cohort key (model, variant, year_jalali) says nothing about the odometer,
    so without this a high-mileage car reads as an outlier purely because it is
    worn — a real price difference with a known, boring explanation. Same
    adjustment and same slope-trust rules as the deal score, reused rather than
    reimplemented so the two can never disagree about what a fair comparison is.
    """
    prices = {ad.code: float(ad.current_price) for ad in rows}
    mileages = [ad.mileage for ad in rows if ad.mileage is not None]
    if slope is None or len(mileages) < MIN_COHORT_PEERS:
        return prices
    reference = statistics.median(mileages)
    for ad in rows:
        if ad.mileage is not None:
            prices[ad.code] += slope * (reference - ad.mileage)
    return prices


def _outliers(adjusted: dict[str, float]) -> dict[str, str]:
    """Map ad code -> flag for every code far from its cohort's centre."""
    values = list(adjusted.values())
    if len(values) < MIN_COHORT_PEERS:
        return {}
    median = statistics.median(values)
    mad = statistics.median([abs(v - median) for v in values])
    if mad <= 0:
        # More than half the cohort shares one price — common where sellers round
        # to the same round number. The spread is genuinely unmeasurable here, and
        # any nonzero deviation would score as infinitely far out.
        return {}
    high_bar = median * MIN_RATIO_FROM_MEDIAN
    low_bar = median / MIN_RATIO_FROM_MEDIAN
    flagged = {}
    for code, value in adjusted.items():
        z = _MAD_TO_SIGMA * (value - median) / mad
        if z > MODIFIED_Z_CUTOFF and value > high_bar:
            flagged[code] = FLAG_HIGH
        elif z < -MODIFIED_Z_CUTOFF and value < low_bar:
            flagged[code] = FLAG_LOW
    return flagged


def flag_cohort_outliers(*, model_id: int | None = None, cohorts=None) -> dict:
    """Recompute ``Ad.cohort_flags`` for whole cohorts.

    Recomputes rather than accumulates: an ad stops being an outlier when its
    peers move, and a flag that could only ever be added would slowly mark the
    entire market. Pass ``model_id`` or an explicit ``cohorts`` iterable of
    ``(model_id, variant_id, year_jalali)`` to rescore only what a fetch touched.
    """
    base = verified(Ad.objects).filter(
        status=Ad.Status.ACTIVE, current_price__gt=0, model_id__isnull=False
    )
    if model_id is not None:
        base = base.filter(model_id=model_id)
    if cohorts is not None:
        cohorts = list(cohorts)
        if not cohorts:
            return {"cohorts": 0, "scanned": 0, "flagged": 0, "cleared": 0}
        base = base.filter(model_id__in={c[0] for c in cohorts})

    grouped: dict[tuple, list[Ad]] = defaultdict(list)
    for ad in base.only(
        "code", "model_id", "variant_id", "year_jalali", "current_price",
        "mileage", "cohort_flags",
    ):
        grouped[(ad.model_id, ad.variant_id, ad.year_jalali)].append(ad)

    if cohorts is not None:
        wanted = set(cohorts)
        grouped = {k: v for k, v in grouped.items() if k in wanted}

    slope_cache: dict = {}
    to_flag, to_clear, scanned = [], [], 0

    for (mid, _variant, _year), rows in grouped.items():
        scanned += len(rows)
        flagged = _outliers(_adjusted_prices(rows, mileage_slope(mid, slope_cache)))
        for ad in rows:
            current = [f for f in (ad.cohort_flags or []) if f not in COHORT_FLAGS]
            verdict = flagged.get(ad.code)
            desired = sorted([*current, verdict]) if verdict else current
            if desired != (ad.cohort_flags or []):
                ad.cohort_flags = desired
                (to_flag if verdict else to_clear).append(ad)

    changed = to_flag + to_clear
    if changed:
        Ad.objects.bulk_update(changed, ["cohort_flags"], batch_size=500)

    return {
        "cohorts": len(grouped),
        "scanned": scanned,
        "flagged": len(to_flag),
        "cleared": len(to_clear),
    }
