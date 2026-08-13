"""Which cars hold their value, by age.

The curve here is a table of medians per model year, not a fitted line. It makes
no assumption about shape, it degrades gracefully when a year is thin, and every
point is traceable to the listings behind it. (The straight-line price-vs-mileage
fit that used to sit alongside it explained a median 18.5% of the variance and
has been deleted.)

Retention is expressed against the newest year with enough data rather than
against an original sale price, which this system never observes. That makes it a
statement about the *used market*, which is the market being described.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from apps.core.models import Ad
from apps.core.services.quality import verified, without_cohort_outliers

# Per model-year point. Below this a median is a coin flip presented as a fact.
MIN_PER_YEAR = 8

# A model needs this many usable years before a curve is worth drawing.
MIN_YEARS = 3


def _base_qs():
    # Outliers excluded throughout: these are all baselines, and an unbelievable
    # price must not help define what a year of age is worth.
    return without_cohort_outliers(verified(Ad.objects)).filter(
        status=Ad.Status.ACTIVE, current_price__gt=0
    )


def depreciation_curve(model_id: int, *, variant_id=None) -> dict:
    """Median asking price by model year, plus value retention against the newest.

    Reports ``pct_of_newest`` rather than a yearly percentage: compounding a
    single average rate across a decade is how a plausible-looking curve ends up
    predicting a negative price.
    """
    qs = _base_qs().filter(model_id=model_id, year_jalali__isnull=False)
    if variant_id:
        qs = qs.filter(variant_id=variant_id)

    by_year: dict[int, list[int]] = defaultdict(list)
    for year, price in qs.values_list("year_jalali", "current_price"):
        by_year[year].append(price)

    points = [
        {
            "year_jalali": year,
            "n": len(prices),
            "median_price": int(statistics.median(prices)),
        }
        for year, prices in sorted(by_year.items())
        if len(prices) >= MIN_PER_YEAR
    ]
    if len(points) < MIN_YEARS:
        return {
            "available": False, "reason": "insufficient_years",
            "model_id": model_id, "years": len(points), "required": MIN_YEARS,
        }

    newest = points[-1]
    for point in points:
        point["pct_of_newest"] = round(
            point["median_price"] / newest["median_price"] * 100, 1
        )
    # Annualised only across the observed span, and only as a summary of what the
    # points already show rather than as a rate to project forward with.
    span = newest["year_jalali"] - points[0]["year_jalali"]
    oldest_ratio = points[0]["median_price"] / newest["median_price"]
    return {
        "available": True,
        "model_id": model_id,
        "variant_id": variant_id,
        "reference_year": newest["year_jalali"],
        "points": points,
        "retained_over_span_pct": round(oldest_ratio * 100, 1),
        "span_years": span,
        "avg_annual_decline_pct": (
            round((1 - oldest_ratio ** (1 / span)) * 100, 1) if span > 0 else None
        ),
    }
