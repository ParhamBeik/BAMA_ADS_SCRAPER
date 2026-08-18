"""Flag listings priced far *above* their peers, so browsing is not noise.

Only the high side. A price far below its cohort is the underpriced car this
product exists to find; a price far above it is a typo, a placeholder or a
dealer fishing, and a 206 asking 5.8 trillion toman is not a data point anyone
is browsing for.

Median and MAD, never mean and standard deviation: one extreme value drags the
mean toward itself *and* inflates sigma, so the outlier widens the very band
meant to catch it. Median/MAD has a 50% breakdown point, which also removes any
need for a remove-and-refit loop.

This is the live plausibility guard for high prices. A global price ceiling
cannot distinguish a new luxury model from a typo, so every otherwise-valid ad
is stored first and only a listing far above comparable peers is flagged.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median

from apps.core.models import Ad
from apps.core.services.quality import FLAG_OUTLIER_HIGH, verified
from apps.core.services.fair_price import MIN_PEERS

# How many MADs above the cohort median counts as implausible.
#
# Deliberately loose. This is a visibility filter, not a verification rule: the
# cost of hiding a real listing is much higher than the cost of showing an
# absurd one, and a genuinely expensive car in a cheap cohort (an unusual trim,
# a low-mileage collector) must survive. At 6 MADs a typical cohort
# (MAD/median ~0.1) only flags above roughly 1.6x its own median.
MAD_THRESHOLD = 6.0

# A MAD of zero means more than half the cohort shares one price, which happens
# in small or heavily-rounded cohorts. Scale off the median instead so the band
# stays finite rather than flagging every non-modal price.
FALLBACK_SPREAD_RATIO = 0.1


def _cohort_flags_without_high(flags) -> list[str]:
    return [f for f in (flags or []) if f != FLAG_OUTLIER_HIGH]


def flag_high_outliers(*, model_id: int | None = None) -> dict:
    """Recompute ``price_outlier_high`` across active priced ads.

    Idempotent, and it *clears* the flag from rows that no longer deserve it —
    a cohort's median moves, and a flag that can only ever be added would leave
    listings permanently hidden by a threshold that has since passed them.
    """
    base = verified(Ad.objects).filter(
        status=Ad.Status.ACTIVE, current_price__gt=0
    )
    if model_id is not None:
        base = base.filter(model_id=model_id)

    rows = list(base.values(
        "code", "model_id", "variant_id", "year_jalali",
        "current_price", "cohort_flags",
    ))

    cohorts: dict = defaultdict(list)
    for r in rows:
        if r["model_id"] is None or r["year_jalali"] is None:
            continue
        cohorts[(r["model_id"], r["variant_id"], r["year_jalali"])].append(r)

    flagged: list[Ad] = []
    cleared: list[Ad] = []
    for peers in cohorts.values():
        if len(peers) < MIN_PEERS:
            # A new or thinly listed model cannot be judged from price alone.
            # Keep it visible until the market supplies a meaningful
            # comparison — but still clear any flag set while the cohort was
            # large enough, or a shrunk cohort would hide a listing forever.
            for r in peers:
                if FLAG_OUTLIER_HIGH in (r["cohort_flags"] or []):
                    cleared.append(Ad(
                        code=r["code"],
                        cohort_flags=_cohort_flags_without_high(r["cohort_flags"]),
                    ))
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
                flagged.append(Ad(
                    code=r["code"],
                    cohort_flags=[*_cohort_flags_without_high(r["cohort_flags"]),
                                  FLAG_OUTLIER_HIGH],
                ))
            elif has_flag and not is_high:
                cleared.append(Ad(
                    code=r["code"],
                    cohort_flags=_cohort_flags_without_high(r["cohort_flags"]),
                ))

    for batch in (flagged, cleared):
        if batch:
            Ad.objects.bulk_update(batch, ["cohort_flags"], batch_size=500)

    return {
        "cohorts": len(cohorts),
        "flagged": len(flagged),
        "cleared": len(cleared),
        "model_id": model_id,
    }
