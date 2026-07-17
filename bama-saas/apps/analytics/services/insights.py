"""Derived market insights: depth, undervalued listings, depreciation, liquidity.

Stats are computed in Python (numpy) after a single priced pull per peer group —
at ~50k rows the pull is cheap and the code stays DB-agnostic.
"""

from __future__ import annotations

import math

import numpy as np
from django.utils import timezone

from apps.catalog.models import Ad


def _peer_qs(model_id, variant_id=None, year=None):
    qs = (
        Ad.objects.filter(model_id=model_id, current_price__gt=0)
        .exclude(publish_at__isnull=True)
    )
    if variant_id:
        qs = qs.filter(variant_id=variant_id)
    if year:
        qs = qs.filter(year=year)
    return qs


def market_depth(model_id, variant_id=None, year=None) -> dict:
    prices = list(_peer_qs(model_id, variant_id, year).values_list("current_price", flat=True))
    if not prices:
        return {"model_id": model_id, "variant_id": variant_id, "year": year, "count": 0}
    arr = np.array(prices, dtype=float)
    p10, p25, p50, p75, p90 = np.percentile(arr, [10, 25, 50, 75, 90])
    return {
        "model_id": model_id, "variant_id": variant_id, "year": year,
        "count": len(prices),
        "p10": int(p10), "p25": int(p25), "median": int(p50),
        "p75": int(p75), "p90": int(p90),
        "min": int(arr.min()), "max": int(arr.max()),
        "spread": round(float((p75 - p25) / p50), 4) if p50 else None,
        "market_depth": round(float((p90 - p10) / p50), 4) if p50 else None,
    }


def undervalued(model_id, variant_id=None, year=None, min_discount_pct: float = 10.0, limit: int = 20) -> dict:
    """Listings priced well below the peer median (peer_count ≥ 5)."""
    qs = _peer_qs(model_id, variant_id, year)
    rows = list(qs.values("code", "current_price", "year", "mileage", "url", "title"))
    if len(rows) < 5:
        return {"model_id": model_id, "variant_id": variant_id, "year": year,
                "peer_count": len(rows), "median": None, "listings": []}
    prices = np.array([r["current_price"] for r in rows], dtype=float)
    median = float(np.percentile(prices, 50))
    listings = []
    for r in rows:
        discount_pct = (median - r["current_price"]) / median * 100 if median else 0
        if discount_pct >= min_discount_pct:
            listings.append({
                "code": r["code"], "price": r["current_price"], "year": r["year"],
                "mileage": r["mileage"], "title": r["title"], "url": r["url"],
                "median": int(median),
                "discount_pct": round(discount_pct, 2),
            })
    listings.sort(key=lambda x: x["discount_pct"], reverse=True)
    return {
        "model_id": model_id, "variant_id": variant_id, "year": year,
        "peer_count": len(rows), "median": int(median),
        "listings": listings[:limit],
    }


def depreciation(model_id, variant_id=None, min_points: int = 6) -> dict:
    """OLS price-vs-mileage: how much value each 10,000 km costs the model."""
    rows = list(
        _peer_qs(model_id, variant_id)
        .exclude(mileage__isnull=True)
        .exclude(mileage=0)
        .values_list("mileage", "current_price")
    )
    if len(rows) < min_points:
        return {"model_id": model_id, "variant_id": variant_id,
                "count": len(rows), "available": False}
    mileages = np.array([float(r[0]) for r in rows])
    prices = np.array([float(r[1]) for r in rows])
    slope, intercept = np.polyfit(mileages, prices, 1)
    predicted = slope * mileages + intercept
    ss_res = float(np.sum((prices - predicted) ** 2))
    ss_tot = float(np.sum((prices - prices.mean()) ** 2))
    r_squared = 1 - ss_res / ss_tot if ss_tot else 0.0
    return {
        "model_id": model_id, "variant_id": variant_id,
        "count": len(rows), "available": True,
        "slope_per_km": round(float(slope), 4),
        "delta_per_10k_km": int(slope * 10000),
        "intercept": int(intercept),
        "r_squared": round(r_squared, 4),
    }


def _mean_age_days(qs) -> float:
    """Average (now - publish_at) in days across the queryset (Python-side)."""
    dates = list(qs.values_list("publish_at", flat=True))
    now = timezone.now()
    deltas = [(now - d).total_seconds() / 86400 for d in dates if d]
    return (sum(deltas) / len(deltas)) if deltas else 0.0


def liquidity(model_id, variant_id=None, year=None) -> dict:
    """0–100 liquidity score: abundant + tightly priced + recently posted."""
    qs = _peer_qs(model_id, variant_id, year)
    prices = list(qs.values_list("current_price", flat=True))
    count = len(prices)
    if count == 0:
        return {"model_id": model_id, "variant_id": variant_id, "year": year,
                "count": 0, "score": 0}
    arr = np.array(prices, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std())
    dispersion = (std / mean) if mean else 1.0
    mean_age_days = _mean_age_days(qs)
    score = min(
        100.0,
        15.0 * math.log1p(count)
        + 45.0 / (1.0 + dispersion)
        + (20.0 / (1.0 + mean_age_days / 7.0)),
    )
    return {
        "model_id": model_id, "variant_id": variant_id, "year": year,
        "count": count, "dispersion": round(dispersion, 4),
        "mean_age_days": round(mean_age_days, 1),
        "score": round(score, 1),
    }
