"""Which cars hold their value, by age and by distance.

``insights.depreciation`` fits one straight line of price against mileage for a
model. That is enough to adjust a single listing, but it cannot answer "which car
should I buy if I care what it is worth in four years", because it says nothing
about age and a straight line is the wrong shape anyway: cars lose value fastest
early and flatten out later.

So the curve here is a table of medians per model year, not a fitted line. It
makes no assumption about shape, it degrades gracefully when a year is thin, and
every point is traceable to the listings behind it.

Retention is expressed against the newest year with enough data rather than
against an original sale price, which this system never observes. That makes it a
statement about the *used market*, which is the market being described.

Regional comparisons are cohort-adjusted. A raw city-by-city median mostly
measures which cities list which cars — comparing Tehran's SUVs against a smaller
city's hatchbacks — so the adjustment is what makes the number mean anything.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from django.db.models import Count

from apps.core.models import Ad
from apps.core.services.quality import verified, without_cohort_outliers

# Per model-year point. Below this a median is a coin flip presented as a fact.
MIN_PER_YEAR = 8

# A model needs this many usable years before a curve is worth drawing.
MIN_YEARS = 3

# Per city, for the regional comparison.
MIN_PER_CITY = 12


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


def retention_leaderboard(*, limit: int = 20, min_years: int = MIN_YEARS) -> list[dict]:
    """Models ranked by how much value they keep across the observed age span."""
    models = (
        _base_qs()
        .filter(model_id__isnull=False, year_jalali__isnull=False)
        .values("model_id", "model__name_fa", "model__brand__name_fa")
        .annotate(n=Count("code"))
        .filter(n__gte=MIN_PER_YEAR * min_years)
    )

    out = []
    for row in models:
        curve = depreciation_curve(row["model_id"])
        if not curve["available"] or curve["span_years"] < min_years:
            continue
        out.append({
            "model_id": row["model_id"],
            "model_name": row["model__name_fa"],
            "brand_name": row["model__brand__name_fa"],
            "span_years": curve["span_years"],
            "retained_pct": curve["retained_over_span_pct"],
            "avg_annual_decline_pct": curve["avg_annual_decline_pct"],
            "listings": row["n"],
        })
    out.sort(key=lambda r: -(r["retained_pct"] or 0))
    return out[:limit]


def regional_spread(*, model_id: int | None = None, min_per_city: int = MIN_PER_CITY) -> dict:
    """City price differences with the model mix held constant.

    Each city is compared against the national median *of the same cohort*, and
    those per-cohort gaps are averaged. Without that, the comparison mostly
    reports that expensive cities list expensive cars.
    """
    qs = _base_qs().filter(city_id__isnull=False, model_id__isnull=False)
    if model_id:
        qs = qs.filter(model_id=model_id)

    rows = list(qs.values_list(
        "city_id", "city__name_fa", "model_id", "variant_id", "year_jalali",
        "current_price",
    ))
    if not rows:
        return {"available": False, "reason": "no_data"}

    national: dict[tuple, list[int]] = defaultdict(list)
    by_city: dict[tuple, list[int]] = defaultdict(list)
    city_names: dict[int, str] = {}
    for city_id, city_name, mid, vid, year, price in rows:
        cohort = (mid, vid, year)
        national[cohort].append(price)
        by_city[(city_id, cohort)].append(price)
        city_names[city_id] = city_name

    national_median = {
        cohort: statistics.median(prices)
        for cohort, prices in national.items()
        if len(prices) >= MIN_PER_YEAR
    }

    gaps: dict[int, list[float]] = defaultdict(list)
    counts: dict[int, int] = defaultdict(int)
    for (city_id, cohort), prices in by_city.items():
        baseline = national_median.get(cohort)
        # Needs enough of that cohort in that city, or the "adjustment" is one
        # listing's opinion.
        if not baseline or len(prices) < 3:
            continue
        gaps[city_id].append((statistics.median(prices) - baseline) / baseline * 100)
        counts[city_id] += len(prices)

    # Across the whole market a city premium resting on a single cohort is that
    # cohort's quirk, so several are required. Scoped to one model the caller has
    # explicitly asked about that cohort, and demanding three would make the
    # question unanswerable rather than careful.
    min_cohorts = 1 if model_id else 3

    out = [
        {
            "city_id": city_id,
            "city_name": city_names[city_id],
            "cohorts_compared": len(values),
            "listings": counts[city_id],
            "premium_pct": round(statistics.median(values), 2),
        }
        for city_id, values in gaps.items()
        if counts[city_id] >= min_per_city and len(values) >= min_cohorts
    ]
    out.sort(key=lambda r: -r["premium_pct"])
    return {"available": bool(out), "model_id": model_id, "cities": out}
