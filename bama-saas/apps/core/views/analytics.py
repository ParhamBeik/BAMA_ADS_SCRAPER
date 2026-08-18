"""Analytics endpoints: the deal board and the market index.

Everything else that used to live here (rankings, regional, dealers, inventory
trends, market overview, time-on-market, fast movers, price drops, newest and
oldest) was served by ``services/metrics.py`` and read by no screen; both are
gone.
"""

from __future__ import annotations

from django.db.models import F
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.core.models import DealScoreCache, MarketIndex
from apps.core.services.fair_price import _TIERS
from apps.core.services.listing_kind import condition_discounted
from apps.core.services.index import read_index
from apps.core.services.quality import verified_by_ad
from apps.core.views.research import envelope


def _opt_int(params, key):
    raw = params.get(key)
    if raw in (None, "", "null"):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be an integer")


def _opt_float(params, key):
    raw = params.get(key)
    if raw in (None, "", "null"):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number")


def _clamp_limit(params, default, lo, hi):
    """Defensive int parse for ?limit with default + clamp."""
    raw = params.get("limit")
    if raw in (None, "", "null"):
        return default
    try:
        return max(lo, min(int(raw), hi))
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer")


# ---------------------------------------------------------------------------
# Deal scores (DealScoreCache joined to Ad)
# ---------------------------------------------------------------------------

# Confidence label -> the peer-count range that produces it, derived from
# fair_price's own tiers rather than restated, so raising MIN_PEERS or retuning a
# tier cannot leave this filter selecting a band the badge no longer means.
_CONFIDENCE_PEERS = {
    label: (threshold, _TIERS[i - 1][0] if i else None)
    for i, (threshold, label) in enumerate(_TIERS)
}


def _deal_score_qs():
    # Gated on the read side as well as at build time. The cache is rebuilt
    # periodically, so between an ad going bad and the next rebuild its stale
    # score is still served — and a deal score is the single most acted-upon
    # number on the site.
    return (
        verified_by_ad(DealScoreCache.objects.select_related("ad", "ad__city"))
        .annotate(
            model_name=F("ad__model__name_fa"),
            brand_name=F("ad__brand__name_fa"),
        )
        # Tie-break on ad_id. Hundreds of rows share a score to one decimal, and
        # an unstable sort under LIMIT/OFFSET drops and repeats listings as the
        # reader pages through.
        .order_by("-score", "ad_id")
    )


def _deal_score_row(obj):
    components = obj.components or {}
    return {
        "code": obj.ad_id,
        "score": obj.score,
        "discount_pct": obj.discount_pct,
        "peer_median": obj.peer_median,
        "peer_count": components.get("peer_count"),
        "confidence": components.get("confidence"),
        "age_days": components.get("age_days"),
        "price": obj.ad.current_price,
        # year_jalali, never Ad.year: the raw column mixes 1399 and 2025 in one
        # field (see apps/core/filters.py) and rendering it produced a column
        # with both calendars in it.
        "year": obj.ad.year_jalali,
        "mileage": obj.ad.mileage,
        "url": obj.ad.url,
        "title": obj.ad.title,
        "model_name": getattr(obj, "model_name", None),
        "brand_name": getattr(obj, "brand_name", None),
        "primary_image_url": obj.ad.primary_image_url,
        "city_name": obj.ad.city.name_fa if obj.ad.city_id else "",
        # The listing's own explanation for being cheap. Not a reason to hide it
        # — a تصادفی car is really for sale at really that price — but the cohort
        # key has no condition dimension, so without this the gap looks like free
        # money instead of accident damage.
        "condition_flagged": condition_discounted(
            title=obj.ad.title, description=obj.ad.description
        ),
        "components": components,
    }


@extend_schema(
    tags=["Analytics"],
    operation_id="analytics_deal_scores_list",
    responses={200: OpenApiTypes.OBJECT},
    parameters=[
        OpenApiParameter("model", int, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("brand", str, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("year", int, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("min_score", float, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("max_score", float, OpenApiParameter.QUERY, required=False,
                         description=(
                             "Upper bound on discount %. The UI defaults to 30 — "
                             "above that the gap is dominated by attributes the "
                             "cohort cannot see (damage, pre-sale, bait)."
                         )),
        OpenApiParameter("confidence", str, OpenApiParameter.QUERY, required=False,
                         enum=["high", "medium", "low"]),
        OpenApiParameter("price_min", int, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("price_max", int, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("limit", int, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("offset", int, OpenApiParameter.QUERY, required=False),
    ],
)
@api_view(["GET"])
def deal_scores(request):
    """Deal scores, joined to ad. Filtered, ordered by discount, paginated.

    Returns ``count`` alongside ``results`` so the board can page: the cache
    holds ~9,800 rows and the screen used to show a hard-coded top 50 with no
    way forward, which put every genuine 5–20% deal out of reach.
    """
    params = request.query_params
    try:
        model = _opt_int(params, "model")
        year = _opt_int(params, "year")
        min_score = _opt_float(params, "min_score")
        max_score = _opt_float(params, "max_score")
        price_min = _opt_int(params, "price_min")
        price_max = _opt_int(params, "price_max")
        limit = _clamp_limit(params, default=50, lo=1, hi=200)
        offset = max(0, _opt_int(params, "offset") or 0)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    qs = _deal_score_qs()
    brand = params.get("brand")
    if brand:
        qs = qs.filter(ad__model__brand__slug=brand)
    if model is not None:
        qs = qs.filter(ad__model_id=model)
    if year is not None:
        qs = qs.filter(ad__year_jalali=year)
    if min_score is not None:
        qs = qs.filter(score__gte=min_score)
    if max_score is not None:
        qs = qs.filter(score__lte=max_score)
    if price_min is not None:
        qs = qs.filter(ad__current_price__gte=price_min)
    if price_max is not None:
        qs = qs.filter(ad__current_price__lte=price_max)
    # peer_count and confidence live in the components JSON. Filtering
    # confidence by its peer-count threshold instead keeps it in SQL, and the
    # thresholds are fair_price's own so the two cannot drift.
    confidence = params.get("confidence")
    if confidence in _CONFIDENCE_PEERS:
        lo, hi = _CONFIDENCE_PEERS[confidence]
        qs = qs.filter(components__peer_count__gte=lo)
        if hi is not None:
            qs = qs.filter(components__peer_count__lt=hi)

    count = qs.count()
    rows = qs[offset:offset + limit]
    return envelope({
        "count": count,
        "limit": limit,
        "offset": offset,
        "results": [_deal_score_row(o) for o in rows],
    })


@extend_schema(
    tags=["Analytics"],
    operation_id="analytics_deal_score_detail",
    responses={200: OpenApiTypes.OBJECT},
)
@api_view(["GET"])
def deal_score_detail(request, code: str):
    """Single ad's deal score or 404."""
    obj = get_object_or_404(
        verified_by_ad(DealScoreCache.objects.select_related("ad"))
        .annotate(
            model_name=F("ad__model__name_fa"),
            brand_name=F("ad__brand__name_fa"),
        ),
        ad_id=code,
    )
    return Response(_deal_score_row(obj))


@extend_schema(
    tags=["Analytics"],
    responses={200: OpenApiTypes.OBJECT},
    parameters=[
        OpenApiParameter("scope", str, OpenApiParameter.QUERY, required=False,
                         enum=["market", "brand", "model"]),
        OpenApiParameter("id", str, OpenApiParameter.QUERY, required=False,
                         description="Brand slug or model id; omit for scope=market."),
        OpenApiParameter("days", int, OpenApiParameter.QUERY, required=False),
    ],
)
@api_view(["GET"])
def market_index(request):
    """Composition-controlled price index (base 100).

    Unlike the raw median, this cannot move because the mix of listings changed
    — only because prices within a cohort changed. See
    ``apps/core/services/index.py``.
    """
    params = request.query_params
    scope = params.get("scope", MarketIndex.Scope.MARKET)
    if scope not in MarketIndex.Scope.values:
        return Response(
            {"detail": f"scope must be one of {sorted(MarketIndex.Scope.values)}"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    scope_id = params.get("id") or None
    if scope != MarketIndex.Scope.MARKET and not scope_id:
        return Response({"detail": f"scope={scope} requires ?id="},
                        status=status.HTTP_400_BAD_REQUEST)
    if scope == MarketIndex.Scope.MARKET:
        scope_id = None  # the market series is keyed on NULL, never on a stray ?id
    try:
        requested_days = max(2, min(int(params.get("days", 90)), 3650))
    except (TypeError, ValueError):
        return Response({"detail": "days must be an integer"},
                        status=status.HTTP_400_BAD_REQUEST)

    series = read_index(scope, scope_id, days=requested_days)
    latest = series[-1] if series else None
    # The real window, clamped to what exists. A caller asking for 90 days
    # against 29 days of history used to get a short series with nothing saying
    # it was short, and the screen went on calling it "90 days".
    window = {
        "requested_days": requested_days,
        "days": len(series),
        "clamped": len(series) < requested_days,
        "first_date": series[0]["date"] if series else None,
        "last_date": latest["date"] if latest else None,
    }
    return envelope({
        "scope": scope,
        "scope_id": scope_id,
        "base_value": 100.0,
        "latest_index": latest["index_value"] if latest else None,
        # Total move across the returned window, which is what a reader actually
        # wants ("prices are up 3% this month"), not the raw index level.
        "change_pct": (
            round((latest["index_value"] / series[0]["index_value"] - 1) * 100, 2)
            if latest and series[0]["index_value"] else None
        ),
        "window": window,
        "series": series,
    })
