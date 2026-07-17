"""Bollinger-style price bands over time (the headline SaaS feature).

For a given model, aggregate all observed prices by day, then slide an
``window``-day SMA across the daily means and plot ``sma ± sigma·std``. The
result is a price *spectrum* across recent months/days rather than a fixed
number — exactly what the user asked for.

Operates on ``PriceObservation`` (the price-through-time backbone) joined
through ``Ad.model_id``.
"""

from __future__ import annotations

import statistics

from django.db.models import Avg, Count
from django.db.models.functions import TruncDate

from apps.market.models import PriceObservation


def bollinger(
    model_id: int,
    variant_id: int | None = None,
    window: int = 20,
    sigma: float = 2.0,
) -> dict:
    qs = PriceObservation.objects.filter(ad__model_id=model_id, price__gt=0)
    if variant_id:
        qs = qs.filter(ad__variant_id=variant_id)

    daily = list(
        qs.annotate(day=TruncDate("observed_at"))
        .values("day")
        .annotate(mean=Avg("price"), n=Count("id"))
        .order_by("day")
    )
    if not daily:
        return {"model_id": model_id, "variant_id": variant_id,
                "window": window, "sigma": sigma, "series": []}

    daily_means = [float(d["mean"]) for d in daily]
    series = []
    for i, d in enumerate(daily):
        start = max(0, i - window + 1)
        chunk = daily_means[start : i + 1]
        sma = statistics.mean(chunk)
        std = statistics.pstdev(chunk) if len(chunk) > 1 else 0.0
        series.append({
            "date": d["day"].isoformat(),
            "daily_mean": int(d["mean"]),
            "mean": int(sma),  # the Bollinger middle (SMA)
            "upper": int(sma + sigma * std),
            "lower": max(0, int(sma - sigma * std)),
            "sample_count": d["n"],
        })

    return {
        "model_id": model_id,
        "variant_id": variant_id,
        "window": window,
        "sigma": sigma,
        "series": series,
    }
