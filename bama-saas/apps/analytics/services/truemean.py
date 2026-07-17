"""True-mean market price: trim outliers, report the central value as a band.

Two methods:
- ``zscore`` (default, matches the user's spec): drop prices whose z-score
  exceeds ``z`` against the raw mean/std, then recompute. This removes the
  far-from-mean high-deviation listings.
- ``percentile``: ported from ``analyze.py``'s ``trim_price_outliers`` (drop
  outside the 5–95 percentile band).

Both operate on the *current* snapshot prices (``Ad.current_price``) for the
peer group — the live market — not the price history.
"""

from __future__ import annotations

import statistics

import numpy as np

from apps.catalog.models import Ad


def _percentile_trim(prices: list[int], lo_pct: float = 5, hi_pct: float = 95) -> list[int]:
    """5–95 percentile band, matching analyze.py's trim_price_outliers exactly."""
    if len(prices) < 4:
        return list(prices)
    arr = np.array(prices, dtype=float)
    lo, hi = np.percentile(arr, [lo_pct, hi_pct])
    return [p for p in prices if lo <= p <= hi]


def _zscore_trim(prices: list[int], z: float) -> list[int]:
    if len(prices) < 3:
        return list(prices)
    mean = statistics.mean(prices)
    std = statistics.pstdev(prices)
    if std == 0:
        return list(prices)
    return [p for p in prices if abs(p - mean) / std <= z]


def true_mean(
    model_id: int,
    variant_id: int | None = None,
    year: int | None = None,
    method: str = "zscore",
    z: float = 2.0,
) -> dict:
    qs = (
        Ad.objects.filter(model_id=model_id, current_price__gt=0)
        .exclude(publish_at__isnull=True)
    )
    if variant_id:
        qs = qs.filter(variant_id=variant_id)
    if year:
        qs = qs.filter(year=year)

    prices = list(qs.values_list("current_price", flat=True))
    count_before = len(prices)
    if not prices:
        return {
            "model_id": model_id, "variant_id": variant_id, "year": year,
            "method": method, "mean": None, "median": None, "std_dev": None,
            "cv": None, "count_before": 0, "count_after": 0,
            "fair_min": None, "fair_max": None,
        }

    if method == "percentile":
        trimmed = _percentile_trim(prices)
    else:
        trimmed = _zscore_trim(prices, z)
    if not trimmed:
        trimmed = prices  # never return an empty result

    mean = statistics.mean(trimmed)
    median = statistics.median(trimmed)
    std = statistics.pstdev(trimmed) or 0.0
    cv = (std / mean) if mean else 0.0

    # Fair band: trimmed mean ± z trimmed std (a tight, outlier-free range).
    fair_min = max(0, int(mean - z * std))
    fair_max = int(mean + z * std)

    return {
        "model_id": model_id,
        "variant_id": variant_id,
        "year": year,
        "method": method,
        "mean": int(mean),
        "median": int(median),
        "std_dev": int(std),
        "cv": round(cv, 4),
        "count_before": count_before,
        "count_after": len(trimmed),
        "fair_min": fair_min,
        "fair_max": fair_max,
    }
