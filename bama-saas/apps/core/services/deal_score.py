"""Deal-score: how far below its peer median a listing sits, blunted by age.

Score (0–100) = ``discount_pct * exp(-age_days/90)`` so a fresh, cheap listing
tops the board but a stale one decays. Only positive discounts score; a peer
group smaller than ``min_peers`` is skipped (no reliable median). One pull per
peer group (median has no ORM aggregate) — same pattern as ``daily_snapshot``.

**Mileage is normalised before the comparison.** The cohort
(model, variant, year_jalali) says nothing about odometer reading, so a
300,000 km car was previously scored against a 20,000 km one and the "best
deals" board filled up with exactly the cars a buyer should walk away from —
the discount was real and entirely explained by wear. Each peer's price is
adjusted to what it would be at the cohort's median mileage using the
model-level price-per-km slope that ``insights.depreciation`` already fits:

    adjusted = price + slope_per_km * (median_mileage - mileage)

``slope_per_km`` is negative (more km, less money), so a high-mileage car is
adjusted *up* toward its cohort and stops looking like a bargain. The fit is
reused, not reimplemented: one OLS per model, cached for the length of the run.

The adjustment is skipped — and the raw price used — whenever the fit is not
trustworthy: ``depreciation`` reports ``available: False`` on too few points,
or the fit explains too little variance (``r_squared`` below
``MIN_FIT_R_SQUARED``). A weak slope applied confidently would inject more
error than the bias it removes.

Refresh is idempotent: a full refresh drops every ``DealScoreCache`` row and
rebuilds, a per-model refresh drops only that model's ads' rows. This mirrors
the daily_snapshot command.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict

from django.utils import timezone

from apps.core.models import DealScoreCache
from apps.core.models import Ad
from apps.core.services.insights import depreciation
from apps.core.services.quality import COHORT_FLAGS, verified

# Below this, the price-vs-mileage line explains too little of the spread to be
# worth trusting on an individual car.
MIN_FIT_R_SQUARED = 0.15


def mileage_slope(model_id: int, cache: dict) -> float | None:
    """Price change per km for a model, or None when the fit is untrustworthy.

    Three conditions, all necessary:

    * ``available`` — ``depreciation`` had enough points to fit at all.
    * ``r_squared >= MIN_FIT_R_SQUARED`` — the line explains enough of the
      spread to be worth applying to an individual car.
    * ``slope < 0`` — cars lose value with distance. A fitted *positive* slope
      is never a real depreciation curve; it means the regression latched onto
      a confounder (a cheap low-mileage outlier, or a cohort where the
      low-km cars are simply the damaged ones). Applying it would push
      high-mileage cars' adjusted prices *down* and manufacture exactly the
      false bargains this adjustment exists to eliminate.

    Memoised per run: ``depreciation`` runs an OLS over the model's whole
    priced population, and a full refresh visits every cohort of that model.
    """
    if model_id not in cache:
        fit = depreciation(model_id)
        slope = fit.get("slope_per_km")
        usable = (
            fit.get("available")
            and slope is not None
            and slope < 0
            and (fit.get("r_squared") or 0) >= MIN_FIT_R_SQUARED
        )
        cache[model_id] = float(slope) if usable else None
    return cache[model_id]


def compute_deal_scores(*, min_peers: int = 3, model_id: int | None = None) -> dict:
    """Refresh deal scores for all eligible ads (or one model if ``model_id``).

    Eligible = ACTIVE, priced (``current_price > 0``), publish-complete
    (``publish_at`` not null). Peer group is same ``model_id``; the median is
    the Python median of peer prices. Skips peer groups with fewer than
    ``min_peers`` peers and ads priced at/above the median (only positive
    discounts score).
    """
    base = (
        verified(Ad.objects)
        .filter(
            status=Ad.Status.ACTIVE,
            current_price__gt=0,
            publish_at__isnull=False,
        )
        .select_related(None)
    )
    if model_id is not None:
        base = base.filter(model_id=model_id)

    # One pull: cohort key + price + age + mileage. Group in Python.
    rows = list(base.values(
        "code", "model_id", "variant_id", "year_jalali",
        "current_price", "first_seen_at", "mileage", "cohort_flags",
    ))

    peers_by_cohort: dict = defaultdict(list)
    for r in rows:
        if r["model_id"] is None or r["variant_id"] is None or r["year_jalali"] is None:
            continue
        cohort_key = (r["model_id"], r["variant_id"], r["year_jalali"])
        peers_by_cohort[cohort_key].append(r)

    now = timezone.now()
    slope_cache: dict = {}
    objs: list[DealScoreCache] = []
    for cohort_key, peers in peers_by_cohort.items():
        mid, vid, yj = cohort_key
        if len(peers) < min_peers:
            continue
        peer_count = len(peers)

        # Normalise to the cohort's median mileage so the discount reflects
        # price, not wear. Needs a trustworthy slope AND enough peers with a
        # known odometer to locate that median; otherwise compare raw prices.
        slope = mileage_slope(mid, slope_cache)
        known_mileages = [
            r["mileage"] for r in peers if r["mileage"] is not None
        ]
        if slope is not None and len(known_mileages) >= min_peers:
            ref_mileage = statistics.median(known_mileages)
            def adjusted(r, _s=slope, _ref=ref_mileage):
                # An ad with no odometer reading cannot be adjusted; it keeps
                # its raw price and is simply compared as-is.
                if r["mileage"] is None:
                    return r["current_price"]
                return r["current_price"] + _s * (_ref - r["mileage"])
        else:
            ref_mileage = None
            def adjusted(r):
                return r["current_price"]

        # The median is built from peers the cohort pass did not flag, but every
        # peer is still scored against it. Both halves matter: an outlier that
        # helps set the baseline drags the baseline toward itself and shrinks its
        # own apparent discount, while an outlier removed from the *results* is a
        # genuinely underpriced car hidden from the one board a buyer reads.
        # Falls back to the full group when exclusion leaves too little to measure.
        clean = [r for r in peers if not set(r["cohort_flags"] or []) & COHORT_FLAGS]
        baseline = clean if len(clean) >= min_peers else peers
        peer_median = statistics.median([adjusted(r) for r in baseline])
        if not peer_median or peer_median <= 0:
            continue
        for r in peers:
            price = r["current_price"]
            adj_price = adjusted(r)
            discount_pct = (peer_median - adj_price) / peer_median * 100
            if discount_pct <= 0:
                continue  # only positive discounts score
            first_seen = r["first_seen_at"]
            age_days = (now - first_seen).days if first_seen else 0
            score = max(0.0, discount_pct) * math.exp(-age_days / 90.0)
            score = round(min(100.0, max(0.0, score)), 1)
            objs.append(
                DealScoreCache(
                    ad_id=r["code"],
                    score=score,
                    discount_pct=round(discount_pct, 2),
                    peer_median=int(peer_median),
                    components={
                        "discount_pct": round(discount_pct, 2),
                        "peer_median": int(peer_median),
                        "age_days": age_days,
                        "price": price,
                        "peer_count": peer_count,
                        "model_id": mid,
                        "variant_id": vid,
                        "year_jalali": yj,
                        # Provenance for the UI's "why did this score?" panel,
                        # and the audit trail for whether the fit was applied.
                        "mileage": r["mileage"],
                        "mileage_adjusted": ref_mileage is not None,
                        "reference_mileage": (
                            int(ref_mileage) if ref_mileage is not None else None
                        ),
                        "adjusted_price": int(adj_price),
                        "slope_per_km": round(slope, 4) if slope is not None else None,
                    },
                )
            )

    # Idempotent full refresh (or per-model refresh): drop then bulk_create.
    if model_id is not None:
        DealScoreCache.objects.filter(ad__model_id=model_id).delete()
    else:
        DealScoreCache.objects.all().delete()
    if objs:
        DealScoreCache.objects.bulk_create(objs, batch_size=500)

    return {"scored": len(objs), "min_peers": min_peers, "model_id": model_id}


def refresh_cohort_deal_scores(model_ids: set[int] | list[int], *, min_peers: int = 3) -> dict:
    """Refresh deal scores incrementally for a set of model IDs."""
    total_scored = 0
    refreshed_models = 0
    for mid in set(model_ids):
        if mid is None:
            continue
        res = compute_deal_scores(min_peers=min_peers, model_id=mid)
        total_scored += res.get("scored", 0)
        refreshed_models += 1
    return {"refreshed_models": refreshed_models, "total_scored": total_scored}

