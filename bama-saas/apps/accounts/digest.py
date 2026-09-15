"""The followed-car digest: what each watchlist is worth reporting today.

Split out of ``views.py``. This is domain logic — it reads the stored market
index, decides which scope a watchlist resolves to, and builds the row the
Saved screen renders — and it was the reason an accounts view module imported
another app's view module.
"""

from __future__ import annotations

from urllib.parse import urlencode

from apps.accounts.models import Watchlist
from apps.core import research
from apps.core.models import MarketIndex

# How far back the followed-car trend looks, and how many points the sparkline
# beside it draws.
DIGEST_DAYS = 30
DIGEST_SPARK = 14
DIGEST_CAP = 50

def _from_stored_index(series: list) -> dict:
    if len(series) < 2:
        return {"available": False, "reason": "insufficient_clean_history", "series": series}
    first, last = series[0]["index_value"], series[-1]["index_value"]
    change = round((last / first - 1) * 100, 2) if first else None
    return {
        "available": True,
        "change_pct": change,
        "latest_index": last,
        "series": series,
        "window": {"days": len(series)},
    }


def _stored_scope(watch: Watchlist) -> tuple[str, str] | None:
    """Which persisted index series answers this scope, if one does.

    Trim and model-year scopes are not persisted (see ``research.cohort_series``)
    and fall through to the on-demand path; a bare market scope has nothing
    personal to say.
    """
    if watch.variant_id or watch.year_jalali:
        return None
    if watch.model_id:
        return MarketIndex.Scope.MODEL, str(watch.model_id)
    if watch.brand_slug:
        return MarketIndex.Scope.BRAND, watch.brand_slug
    return None


def _digest_trends(rows: list[Watchlist]) -> dict[int, dict]:
    """One trend per followed scope, in a bounded number of queries.

    Two shapes of work hide behind a followed car, and both used to run once per
    row. The stored brand and model series are read in one query per scope kind
    (``research.read_indexes``) rather than one per row. The trim and model-year
    scopes have no stored series and are computed from daily snapshots; those
    stay one query each — an index range scan over a single cohort, served by
    ``snap_cohort_date_idx`` — but they are cached now, so a reload is free.

    Measured before this existed: fifteen followed cars cost seventeen queries,
    one per row, none of them cached, on every load of Saved.
    """
    from apps.core.views import movement_payload

    wanted: dict[str, set[str]] = {}
    for watch in rows:
        scope = _stored_scope(watch)
        if scope is not None:
            wanted.setdefault(scope[0], set()).add(scope[1])
    stored = {
        kind: research.read_indexes(kind, ids, days=DIGEST_DAYS)
        for kind, ids in wanted.items()
    }

    trends: dict[int, dict] = {}
    for watch in rows:
        scope = _stored_scope(watch)
        if scope is not None:
            trends[watch.id] = _from_stored_index(stored[scope[0]].get(scope[1], []))
        elif watch.variant_id or watch.year_jalali:
            if not watch.model_id:
                trends[watch.id] = {"available": False, "reason": "incomplete_scope"}
            else:
                trends[watch.id] = movement_payload(
                    watch.model_id, watch.variant_id, watch.year_jalali, DIGEST_DAYS
                )
        else:
            trends[watch.id] = {"available": False, "reason": "market_scope"}
    return trends


def _analyse_path(watch: Watchlist) -> str:
    query = {}
    if watch.brand_slug:
        query["brand"] = watch.brand_slug
    if watch.model_id:
        query["model"] = str(watch.model_id)
    if watch.variant_id:
        query["variant"] = str(watch.variant_id)
    if watch.year_jalali:
        query["year"] = str(watch.year_jalali)
    return "/analyse?" + urlencode(query) if query else "/analyse"


def _digest_row(watch: Watchlist, trend: dict, brand_names: dict[str, str]) -> dict:
    series = trend.get("series") or []
    spark = [point.get("index_value") for point in series[-DIGEST_SPARK:]]
    brand_name = ""
    if watch.model_id and watch.model is not None and watch.model.brand is not None:
        brand_name = watch.model.brand.name_fa
    elif watch.brand_slug:
        brand_name = brand_names.get(watch.brand_slug, watch.brand_slug)
    return {
        "id": watch.id,
        "scope_key": watch.scope_key,
        "brand_slug": watch.brand_slug,
        "model": watch.model_id,
        "variant": watch.variant_id,
        "year_jalali": watch.year_jalali,
        "brand_name": brand_name,
        "model_name": watch.model.name_fa if watch.model_id and watch.model else "",
        "variant_name": watch.variant.name_fa if watch.variant_id and watch.variant else "",
        "analyse_path": _analyse_path(watch),
        "available": bool(trend.get("available")),
        "reason": trend.get("reason"),
        "change_pct": trend.get("change_pct"),
        "latest_index": trend.get("latest_index"),
        "spark": spark,
        "window_days": (trend.get("window") or {}).get("days"),
    }
