"""Deal-score: how far below its peer median a listing sits, blunted by age.

Score (0–100) = ``discount_pct * exp(-age_days/90)`` so a fresh, cheap listing
tops the board but a stale one decays. Only positive discounts score; a peer
group smaller than ``min_peers`` is skipped (no reliable median). One pull per
peer group (median has no ORM aggregate) — same pattern as ``daily_snapshot``.

Refresh is idempotent: a full refresh drops every ``DealScoreCache`` row and
rebuilds, a per-model refresh drops only that model's ads' rows. This mirrors
the daily_snapshot / refresh_analytics commands.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict

from django.utils import timezone

from apps.core.models import DealScoreCache
from apps.core.models import Ad


def compute_deal_scores(*, min_peers: int = 3, model_id: int | None = None) -> dict:
    """Refresh deal scores for all eligible ads (or one model if ``model_id``).

    Eligible = ACTIVE, priced (``current_price > 0``), publish-complete
    (``publish_at`` not null). Peer group is same ``model_id``; the median is
    the Python median of peer prices. Skips peer groups with fewer than
    ``min_peers`` peers and ads priced at/above the median (only positive
    discounts score).
    """
    base = (
        Ad.objects.filter(
            status=Ad.Status.ACTIVE,
            current_price__gt=0,
            publish_at__isnull=False,
        )
        .select_related(None)
    )
    if model_id is not None:
        base = base.filter(model_id=model_id)

    # One pull: code, model_id, current_price, first_seen_at. Group in Python.
    rows = list(base.values("code", "model_id", "current_price", "first_seen_at"))

    peers_by_model: dict = defaultdict(list)
    for r in rows:
        if r["model_id"] is None:
            continue
        peers_by_model[r["model_id"]].append(r)

    now = timezone.now()
    objs: list[DealScoreCache] = []
    for mid, peers in peers_by_model.items():
        if len(peers) < min_peers:
            continue
        prices = [p["current_price"] for p in peers]
        peer_median = statistics.median(prices)
        if not peer_median:
            continue
        peer_count = len(peers)
        for r in peers:
            price = r["current_price"]
            discount_pct = (peer_median - price) / peer_median * 100
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

