"""Bollinger-style price bands over time (the headline SaaS feature).

For a given model, aggregate all observed prices by day, then slide a
``window``-day SMA across the daily *medians* and plot ``sma ± sigma·std``. The
result is a price spectrum across recent months/days rather than a fixed number.

Two properties this shares with every other series in the codebase:

* The daily central value is the **median**, not the mean. One supercar in a
  Pride cohort dragged the daily mean up and, through the SMA, smeared that
  spike across the whole following window — the exact failure ``truemean.py``
  exists to prevent, reproduced here.
* The observation set is filtered by **ad** quality via ``verified_by_ad``.
  ``verified()`` only constrains Ad querysets, so the price-side series had been
  averaging observations belonging to ads the Ad-side analytics excluded.

Operates on ``PriceObservation`` (the price-through-time backbone) joined
through ``Ad.model_id``.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from apps.core.models import PriceObservation
from apps.core.services.quality import verified_by_ad


def bollinger(
    model_id: int,
    variant_id: int | None = None,
    window: int = 20,
    sigma: float = 2.0,
) -> dict:
    qs = verified_by_ad(
        PriceObservation.objects.filter(ad__model_id=model_id, price__gt=0)
    )
    if variant_id:
        qs = qs.filter(ad__variant_id=variant_id)

    # Bucket in Python: median has no ORM aggregate, and one pull of
    # (day, price) is cheap at this scale — the same trade metrics.py makes.
    by_day: dict = defaultdict(list)
    for observed_at, price in qs.values_list("observed_at", "price"):
        if observed_at is not None:
            by_day[observed_at.date()].append(price)

    if not by_day:
        return {"model_id": model_id, "variant_id": variant_id,
                "window": window, "sigma": sigma, "series": []}

    daily = [(day, by_day[day]) for day in sorted(by_day)]
    daily_medians = [float(statistics.median(prices)) for _, prices in daily]
    series = []
    for i, (day, prices) in enumerate(daily):
        start = max(0, i - window + 1)
        chunk = daily_medians[start : i + 1]
        sma = statistics.mean(chunk)
        std = statistics.pstdev(chunk) if len(chunk) > 1 else 0.0
        series.append({
            "date": day.isoformat(),
            "daily_median": int(daily_medians[i]),
            "mean": int(sma),  # the Bollinger middle (SMA of daily medians)
            "upper": int(sma + sigma * std),
            "lower": max(0, int(sma - sigma * std)),
            "sample_count": len(prices),
        })

    return {
        "model_id": model_id,
        "variant_id": variant_id,
        "window": window,
        "sigma": sigma,
        "series": series,
    }
