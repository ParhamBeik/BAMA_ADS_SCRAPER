"""The deal board: how far below its cohort's fair value a listing is priced.

    score = discount_pct = (fair_value - asking) / fair_value * 100

That is the whole formula, and it is deliberately the *same* number the listing
page shows, computed by the same code in ``fair_price.py``.

Three things this used to do and no longer does:

* **Its own peer logic.** It grouped cohorts at ``min_peers=3`` while the
  fair-price engine required 8, so the board's top rows were medians of three
  cars that the listing page refused to quote at all.
* **A mileage OLS.** ``insights.depreciation``'s fit (median r² 0.185) was used
  to normalise every peer's price. On a weak slope the adjustment exceeded the
  car's own price, producing negative adjusted prices and discounts of 123% and
  148%. ``fair_price`` now adjusts by bucket median or not at all.
* **``exp(-age_days / 90)``.** An uncalibrated half-life multiplied a number
  labelled "deal score", so an unchanged listing became a worse deal purely by
  existing. Age is still reported — as its own field, for the reader to weigh.

Rows with a non-positive fair value, an asking price at or above it, or an
asking price below half the peer median, are not written at all: a board of
typos and non-deals is worse than a short board.

Neither is an ad whose price is not one car's cash price — see
``listing_kind.exclude_unclear_price``. That filter replaced a
``title__startswith("حواله")`` special case which caught one vocabulary of one
artifact: an audit of the top 200 rows found 74% of them were installment ads
advertising their down payment, and the ``حواله`` rule had caught none of them.

Refresh is idempotent: a full refresh drops every ``DealScoreCache`` row and
rebuilds, a per-model refresh drops only that model's ads' rows.
"""

from __future__ import annotations

from collections import defaultdict

from django.utils import timezone

from apps.core.models import Ad, DealScoreCache
from apps.core.services.fair_price import MIN_PEERS, cohort_baseline, dispersion
from apps.core.services.listing_kind import exclude_unclear_price
from apps.core.services.quality import COHORT_FLAGS, verified
from apps.jobs.services.verify import MIN_PLAUSIBLE_PRICE

# Asking below half the peer median is a deposit or a missing-zero typo, not a
# deal. Typical cohort MAD/median is ~0.1 (see ``fair_price.dispersion``), so
# this is ~5 MADs below the median — the same cutoff, written as a ratio so a
# noisy cohort cannot admit a 50M-vs-2B row.
MIN_ASK_VS_MEDIAN = 0.5


def compute_deal_scores(*, model_id: int | None = None) -> dict:
    """Refresh deal scores for all eligible ads (or one model if ``model_id``).

    Eligible = ACTIVE, priced, publish-complete, in a cohort of at least
    ``MIN_PEERS``. One query, grouped in Python: the median has no ORM aggregate
    and the whole eligible set is a narrow scan.
    """
    from apps.core.services.outliers import flag_high_outliers

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
        if r["model_id"] is None or r["variant_id"] is None or r["year_jalali"] is None:
            continue
        peers_by_cohort[(r["model_id"], r["variant_id"], r["year_jalali"])].append(r)

    now = timezone.now()
    objs: list[DealScoreCache] = []
    for (mid, vid, yj), peers in peers_by_cohort.items():
        # The baseline is built from peers the cohort pass did not flag, but
        # every peer is still scored against it. Both halves matter: an outlier
        # that helps set the baseline drags it toward itself and shrinks its own
        # apparent discount, while an outlier dropped from the *results* is a
        # genuinely underpriced car hidden from the one board a buyer reads.
        clean = [r for r in peers if not set(r["cohort_flags"] or []) & COHORT_FLAGS]
        baseline_rows = clean if len(clean) >= MIN_PEERS else peers
        baseline = cohort_baseline(
            [(r["current_price"], r["mileage"]) for r in baseline_rows]
        )
        if baseline is None:
            continue
        peer_prices = [r["current_price"] for r in baseline_rows if r["current_price"]]
        spread = dispersion(peer_prices, baseline.base)
        # Half the peer median. MAD-equivalent of ~5 MADs at typical
        # dispersion (~0.1); a wide MAD must not relax this or deposits pass.
        floor = MIN_ASK_VS_MEDIAN * baseline.base

        for r in peers:
            adjusted = baseline.adjusted(r["mileage"])
            fair_value = adjusted.fair_value
            # A fair value at or below zero means the adjustment ate the car.
            # Bucket medians cannot produce one, but the last generation of this
            # file shipped 123% discounts because nothing ever checked.
            if fair_value <= 0:
                continue
            price = r["current_price"]
            if price < floor:
                continue  # deposit / missing-zero typo, not a deal
            discount_pct = (fair_value - price) / fair_value * 100
            if discount_pct <= 0:
                continue  # priced at or above fair value: not a deal
            first_seen = r["first_seen_at"]
            age_days = (now - first_seen).days if first_seen else 0
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
                    "age_days": age_days,
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


def refresh_cohort_deal_scores(model_ids: set[int] | list[int]) -> dict:
    """Refresh deal scores incrementally for a set of model IDs."""
    total_scored = 0
    refreshed_models = 0
    for mid in set(model_ids):
        if mid is None:
            continue
        total_scored += compute_deal_scores(model_id=mid).get("scored", 0)
        refreshed_models += 1
    return {"refreshed_models": refreshed_models, "total_scored": total_scored}
