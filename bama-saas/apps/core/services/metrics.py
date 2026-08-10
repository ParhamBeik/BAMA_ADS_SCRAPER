"""Derived market metrics: rankings, regional/dealer breakdowns, inventory trend,
market overview, time-on-market, fast sellers, recent price drops.

All functions are pure-ish (read ORM, return plain dicts/lists) and compute
medians in Python — median has no ORM aggregate, and at the working scale a
single pull is cheap. The ``_peer_qs`` pattern (priced + publish-complete) is
reused from ``insights.py`` for consistency.
"""

from __future__ import annotations

from datetime import timedelta

import statistics
from collections import defaultdict

from django.utils import timezone

from apps.core.models import DailyInventorySnapshot, MarketSnapshot
from apps.core.models import Ad
from apps.core.models import PriceDropEvent
from apps.core.services.quality import verified, verified_by_ad

_DIM_FIELDS = {
    "brands": ("model__brand_id", "model__brand__slug", "model__brand__name_fa"),
    "models": ("model_id", "model__name_fa", None),
    "variants": ("variant_id", "variant__name_fa", None),
}


def _active_priced_qs():
    """ACTIVE, priced, publish-complete queryset — the standard live-market slice."""
    return verified(Ad.objects).filter(
        status=Ad.Status.ACTIVE,
        current_price__gt=0,
        publish_at__isnull=False,
    )


def _removed_qs():
    """REMOVED ads with both lifecycle timestamps — the delisting slice.

    Separate from ``_active_priced_qs`` because the lifecycle metrics need
    REMOVED rather than ACTIVE, but it still goes through ``verified()``: a
    hard-flagged row is no more usable for a duration statistic than for a
    price one.
    """
    return verified(Ad.objects).filter(
        status=Ad.Status.REMOVED,
        first_seen_at__isnull=False,
        removed_at__isnull=False,
    )


def _summarize(prices: list[int]) -> dict:
    return {
        "median_price": int(statistics.median(prices)),
        "min_price": min(prices),
        "max_price": max(prices),
    }


def rankings(dim: str, limit: int = 20) -> list:
    """Group active priced ads by brand/model/variant, sorted by -ad_count."""
    if dim not in _DIM_FIELDS:
        return []
    key_field, name_field, _extra = _DIM_FIELDS[dim]
    qs = (
        _active_priced_qs()
        .exclude(**{f"{key_field}__isnull": True})
        .values_list(key_field, name_field, "current_price")
    )
    groups: dict = defaultdict(list)
    for key, name, price in qs:
        groups[key].append((name, price))

    out = []
    for key, items in groups.items():
        prices = [p for _, p in items]
        name = items[0][0] if items else None
        row = {"key": key, "id": key, "name": name, "ad_count": len(prices)}
        row.update(_summarize(prices))
        out.append(row)
    out.sort(key=lambda r: r["ad_count"], reverse=True)
    return out[:limit]


def regional(model_id: int | None = None, limit: int = 20) -> list:
    """Group active priced ads by city (skip null city), sorted -ad_count."""
    qs = _active_priced_qs().exclude(city__isnull=True)
    if model_id is not None:
        qs = qs.filter(model_id=model_id)
    rows = list(qs.values_list("city_id", "city__name_fa", "current_price"))
    groups: dict = defaultdict(list)
    for city_id, name, price in rows:
        groups[city_id].append((name, price))

    out = []
    for city_id, items in groups.items():
        prices = [p for _, p in items]
        out.append({
            "city_id": city_id,
            "city_name": items[0][0],
            "ad_count": len(prices),
            "median_price": int(statistics.median(prices)),
        })
    out.sort(key=lambda r: r["ad_count"], reverse=True)
    return out[:limit]


def dealer_stats(limit: int = 20) -> list:
    """Group active priced ads by dealer (skip null), sorted -ad_count."""
    rows = list(
        _active_priced_qs()
        .exclude(dealer__isnull=True)
        .values_list("dealer_id", "dealer__name", "current_price")
    )
    groups: dict = defaultdict(list)
    for dealer_id, name, price in rows:
        groups[dealer_id].append((name, price))

    out = []
    for dealer_id, items in groups.items():
        prices = [p for _, p in items]
        out.append({
            "dealer_id": dealer_id,
            "name": items[0][0],
            "ad_count": len(prices),
            "median_price": int(statistics.median(prices)),
        })
    out.sort(key=lambda r: r["ad_count"], reverse=True)
    return out[:limit]


def inventory_trend(model_id: int, days: int = 90) -> dict:
    """Model-level daily inventory rollup from DailyInventorySnapshot.

    Aggregates per-(variant,year) snapshot rows for the model into one daily
    series, plus growth (latest minus prior ad_count).
    """
    qs = DailyInventorySnapshot.objects.filter(model_id=model_id).order_by("-date")
    by_date: dict = defaultdict(lambda: {"ad_count": 0, "new_count": 0, "prices": []})
    for row in qs.values("date", "ad_count", "new_count", "median_price"):
        d = by_date[row["date"]]
        d["ad_count"] += row["ad_count"]
        d["new_count"] += row["new_count"]
        if row["median_price"] is not None:
            d["prices"].append((row["median_price"], row["ad_count"]))

    series = []
    for date, g in sorted(by_date.items()):
        # Volume-weighted median across the day's slices (true weighted median).
        if g["prices"]:
            sorted_pairs = sorted(g["prices"], key=lambda x: x[0])
            total_weight = sum(w for _, w in sorted_pairs)
            cumulative_weight = 0
            median_price = None
            for val, w in sorted_pairs:
                cumulative_weight += w
                if cumulative_weight >= total_weight / 2:
                    median_price = val
                    break
            if median_price is None and sorted_pairs:
                median_price = sorted_pairs[0][0]
        else:
            median_price = None
        series.append({
            "date": date.isoformat(),
            "ad_count": g["ad_count"],
            "new_count": g["new_count"],
            "median_price": median_price,
        })
    if len(series) >= days:
        series = series[-days:]

    growth = None
    growth_pct = None
    if len(series) >= 2:
        prev, last = series[-2], series[-1]
        growth = last["ad_count"] - prev["ad_count"]
        growth_pct = round(growth / prev["ad_count"] * 100, 2) if prev["ad_count"] else None
    return {
        "model_id": model_id,
        "growth": growth,
        "growth_pct": growth_pct,
        "series": series,
    }


def market_overview(days: int = 90) -> list:
    """Last N days of MarketSnapshot rows as lightweight dicts (asc by date)."""
    qs = (
        MarketSnapshot.objects.all()
        .order_by("-date")[:days]
    )
    out = [
        {
            "date": s.date.isoformat(),
            "active_count": s.active_count,
            "new_count": s.new_count,
            "removed_count": s.removed_count,
            "median_price": s.median_price,
        }
        for s in reversed(list(qs))
    ]
    return out


def time_on_market(model_id: int) -> dict:
    """Days-listed for live ads + days-to-delisting for removed ones.

    "Removed" means the ad left the Bama feed. That can be a sale, an expiry, or
    the seller withdrawing it, and the payload does not distinguish them — so
    these are *delisting* durations, deliberately not called days-to-sell. They
    are an upper bound on time-to-sale and nothing stronger.
    """
    now = timezone.now()
    active_rows = list(
        _active_priced_qs()
        .filter(model_id=model_id)
        .values_list("publish_at", flat=True)
    )
    days_listed = [(now - p).days for p in active_rows if p]
    removed_rows = list(
        _removed_qs().filter(model_id=model_id).values_list("first_seen_at", "removed_at")
    )
    days_to_delist = [
        (removed_at - first_seen).days for first_seen, removed_at in removed_rows
    ]
    return {
        "model_id": model_id,
        "active_count": len(days_listed),
        "avg_days_listed": round(statistics.mean(days_listed), 1) if days_listed else 0,
        "median_days_listed": int(statistics.median(days_listed)) if days_listed else 0,
        "removed_count": len(days_to_delist),
        "avg_days_to_delist": (
            round(statistics.mean(days_to_delist), 1) if days_to_delist else 0
        ),
        "median_days_to_delist": (
            int(statistics.median(days_to_delist)) if days_to_delist else 0
        ),
    }


def fast_movers(model_id: int, limit: int = 20) -> list:
    """Removed ads for a model, shortest time-on-feed first.

    Named "movers", not "sellers": leaving the feed is not evidence of a sale
    (see :func:`time_on_market`). Short-lived listings are still the useful
    signal — they are where demand is, whatever ended them.
    """
    rows = list(
        _removed_qs()
        .filter(model_id=model_id)
        .values("code", "first_seen_at", "removed_at", "current_price", "year_jalali")
    )
    out = [
        {
            "code": r["code"],
            "days_to_delist": (r["removed_at"] - r["first_seen_at"]).days,
            "last_price": r["current_price"],
            # Response key stays "year"; the value is the canonical Jalali cohort
            # year, since raw `Ad.year` mixes Jalali and Gregorian.
            "year": r["year_jalali"],
        }
        for r in rows
    ]
    out.sort(key=lambda x: x["days_to_delist"])
    return out[:limit]


def price_drops(
    model_id: int | None = None,
    min_pct: float = 0.0,
    days: int = 30,
    limit: int = 50,
) -> list:
    """Recent PriceDropEvent rows, joined to ad, filtered by min_pct and model."""
    since = timezone.now() - timedelta(days=days)
    # Filter by the *ad's* quality, not the event's: a drop on a hard-flagged ad
    # is a drop between two unusable prices. `verified()` builds an Ad-queryset
    # exclusion, so apply it via the ad__code join rather than to the events.
    qs = verified_by_ad(
        PriceDropEvent.objects.filter(observed_at__gte=since, drop_pct__gte=min_pct)
    )
    if model_id is not None:
        qs = qs.filter(ad__model_id=model_id)
    rows = list(
        qs.order_by("-observed_at")[:limit].values(
            "ad__code",
            "ad__title",
            "ad__url",
            "ad__year_jalali",
            "old_price",
            "new_price",
            "drop_amount",
            "drop_pct",
            "observed_at",
        )
    )
    return [
        {
            "code": r["ad__code"],
            "title": r["ad__title"],
            "url": r["ad__url"],
            "year": r["ad__year_jalali"],
            "old_price": r["old_price"],
            "new_price": r["new_price"],
            "drop_amount": r["drop_amount"],
            "drop_pct": r["drop_pct"],
            "observed_at": r["observed_at"].isoformat() if r["observed_at"] else None,
        }
        for r in rows
    ]
