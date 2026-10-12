"""Model-first asking-price evidence. Thin groups remain visible as observations."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from apps.core.coverage import coverage_state
from apps.core.models import Model
from apps.core.pricing import (
    MILEAGE_BUCKETS,
    MIN_PEERS,
    peer_distribution,
    price_set_rows,
    scorable_rows,
)
from apps.core.quality import peer_band, without_cohort_outliers

RANGE_KEYS = ("p10", "p25", "median", "p75", "p90", "width_pct", "wide", "clusters",
              "old_price_pct", "old_price_days")


def _bucket(km: int | None) -> str:
    if km is None:
        return "unknown"
    edge = max((n for n in MILEAGE_BUCKETS if km >= n), default=0)
    return str(edge)


def _summary(rows: list[dict]) -> dict:
    prices = [row["current_price"] for row in rows]
    dist = peer_distribution(prices, set_at=[row["price_set_at"] for row in rows])
    return {
        "count": len(rows),
        "thin": len(prices) < MIN_PEERS,
        "asking_range": {k: dist[k] for k in RANGE_KEYS} if dist else None,
        "observed_min": min(prices) if prices else None,
        "observed_max": max(prices) if prices else None,
    }


def _facet(rows: list[dict], key: str, *, name_key: str | None = None) -> list[dict]:
    groups: dict[str | int | None, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    result = []
    for value, group in groups.items():
        label = group[0].get(name_key) if name_key else value
        result.append({"value": value, "label": label, **_summary(group)})
    return sorted(result, key=lambda row: (-row["count"], str(row["label"])))[:60]


def model_exploration(model_id: int, *, variant_id: int | None = None,
                      year: int | None = None, city_id: int | None = None,
                      condition: str | None = None, mileage_min: int | None = None,
                      mileage_max: int | None = None) -> dict:
    model = Model.objects.select_related("brand").get(pk=model_id)
    # The same population every range is built from: extremes and suspect
    # prices (down payments, likely mis-filed ads) stay out.
    base = price_set_rows(without_cohort_outliers(scorable_rows())).filter(model_id=model_id)
    fields = ("code", "title", "current_price", "variant_id", "variant__name_fa",
              "year_jalali", "mileage", "body_status", "city_id", "city__name_fa",
              "publish_at", "last_seen_at", "price_set_at")
    national = list(base.values(*fields))
    for row in national:
        row["mileage_bucket"] = _bucket(row["mileage"])
        row["condition_band"] = peer_band(row["body_status"], row["mileage"])
    selected = [
        row for row in national
        if (variant_id is None or row["variant_id"] == variant_id)
        and (year is None or row["year_jalali"] == year)
        and (city_id is None or row["city_id"] == city_id)
        and (condition is None or row["condition_band"] == condition)
        and (mileage_min is None or
             (row["mileage"] is not None and row["mileage"] >= mileage_min))
        and (mileage_max is None or
             (row["mileage"] is not None and row["mileage"] <= mileage_max))
    ]
    recent = sorted(selected, key=lambda row: row["publish_at"] or timezone.now() -
                    timedelta(days=10000), reverse=True)[:12]
    for row in recent:
        row["image_url"] = f"/api/img/{row['code']}/thumb/"
        row["listing_url"] = f"/listing/{row['code']}"
    coverage = coverage_state()
    return {
        "model": {"id": model.pk, "name": model.name_fa, "brand": model.brand.name_fa},
        "basis": "observed Bama cash asking prices",
        "national": _summary(national),
        # True peers only once trim, year and condition are all pinned; until
        # then the range mixes cars that are not comparable, and says so.
        "selected": {**_summary(selected),
                     "strict": None not in (variant_id, year, condition)},
        "facets": {
            "variants": _facet(national, "variant_id", name_key="variant__name_fa"),
            "years": _facet(national, "year_jalali"),
            "conditions": _facet(national, "condition_band"),
            "mileage": _facet(national, "mileage_bucket"),
            "cities": _facet(national, "city_id", name_key="city__name_fa"),
        },
        "ads": recent,
        "evidence": {
            "feed_coverage_complete": coverage.complete,
            "latest_selected_sighting": max(
                (row["last_seen_at"] for row in selected if row["last_seen_at"]),
                default=None,
            ),
            "minimum_for_range": MIN_PEERS,
            "rare_groups_are_valid": True,
        },
        "purchase_price": None,
        "purchase_price_reason": "No validated transaction-price evidence is available.",
    }
