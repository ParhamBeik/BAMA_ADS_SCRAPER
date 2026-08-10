"""Insights endpoints: liquidity, undervalued listings, market depth, depreciation.

Each is keyed by ``<int:model_id>`` with optional ``?variant`` and ``?year``.
Also exposes the deal-score / metrics endpoints under /api/analytics/.
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
from apps.core.services import metrics
from apps.core.services import insights
from apps.core.services.index import read_index
from apps.core.services.quality import verified, verified_by_ad
from apps.core.models import Ad, Model


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
    """Defensive int parse for ?limit with default + clamp, like market/views."""
    raw = params.get("limit")
    if raw in (None, "", "null"):
        return default
    try:
        return max(lo, min(int(raw), hi))
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer")


@extend_schema(
    tags=["Analytics"],
    responses={200: OpenApiTypes.OBJECT},
    parameters=[
        OpenApiParameter("variant", int, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("year", int, OpenApiParameter.QUERY, required=False),
    ],
)
@api_view(["GET"])
def insight(request, model_id: int, kind: str):
    model = get_object_or_404(Model, id=model_id)
    params = request.query_params
    try:
        variant = _opt_int(params, "variant")
        year = _opt_int(params, "year")
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    if kind == "liquidity":
        data = insights.liquidity(model.id, variant_id=variant, year=year)
    elif kind == "market-depth":
        data = insights.market_depth(model.id, variant_id=variant, year=year)
    elif kind == "undervalued":
        data = insights.undervalued(model.id, variant_id=variant, year=year)
    elif kind == "depreciation":
        data = insights.depreciation(model.id, variant_id=variant)
    else:
        return Response(
            {"detail": "kind must be one of: liquidity, market-depth, undervalued, depreciation"},
            status=status.HTTP_404_NOT_FOUND,
        )
    data["model_name"] = model.name_fa
    data["brand_name"] = model.brand.name_fa
    return Response(data)


# ---------------------------------------------------------------------------
# Deal scores (DealScoreCache joined to Ad)
# ---------------------------------------------------------------------------

def _deal_score_qs():
    # Gated on the read side as well as at build time. The cache is rebuilt
    # periodically, so between an ad going bad and the next rebuild its stale
    # score is still served — and a deal score is the single most acted-upon
    # number on the site.
    return (
        verified_by_ad(DealScoreCache.objects.select_related("ad"))
        .annotate(
            model_name=F("ad__model__name_fa"),
            brand_name=F("ad__brand__name_fa"),
        )
        .order_by("-score")
    )


def _deal_score_row(obj):
    return {
        "code": obj.ad_id,
        "score": obj.score,
        "discount_pct": obj.discount_pct,
        "peer_median": obj.peer_median,
        "price": obj.ad.current_price,
        "year": obj.ad.year,
        "mileage": obj.ad.mileage,
        "url": obj.ad.url,
        "title": obj.ad.title,
        "model_name": getattr(obj, "model_name", None),
        "brand_name": getattr(obj, "brand_name", None),
        "components": obj.components,
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
        OpenApiParameter("limit", int, OpenApiParameter.QUERY, required=False),
    ],
)
@api_view(["GET"])
def deal_scores(request):
    """Top deal scores, joined to ad. Filters: model/brand/year/min_score/limit."""
    params = request.query_params
    try:
        model = _opt_int(params, "model")
        year = _opt_int(params, "year")
        min_score = _opt_float(params, "min_score")
        limit = _clamp_limit(params, default=50, lo=1, hi=200)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    qs = _deal_score_qs()
    brand = params.get("brand")
    if brand:
        qs = qs.filter(ad__model__brand__slug=brand)
    if model is not None:
        qs = qs.filter(ad__model_id=model)
    if year is not None:
        qs = qs.filter(ad__year=year)
    if min_score is not None:
        qs = qs.filter(score__gte=min_score)
    qs = qs[:limit]
    return Response([_deal_score_row(o) for o in qs])


@extend_schema(
    tags=["Analytics"],
    operation_id="analytics_deal_score_detail",
    responses={200: OpenApiTypes.OBJECT},
)
@api_view(["GET"])
def deal_score_detail(request, code: str):
    """Single ad's deal score or 404."""
    obj = get_object_or_404(
        DealScoreCache.objects.select_related("ad")
        .annotate(
            model_name=F("ad__model__name_fa"),
            brand_name=F("ad__brand__name_fa"),
        ),
        ad_id=code,
    )
    return Response(_deal_score_row(obj))


# ---------------------------------------------------------------------------
# Metrics wrappers (rankings, regional, dealers, inventory/market trends, TOM,
# fast sellers, price drops, newest/oldest)
# ---------------------------------------------------------------------------

_RANKING_DIMS = {"brands", "models", "variants"}


@extend_schema(
    tags=["Analytics"],
    responses={200: OpenApiTypes.OBJECT},
    parameters=[
        OpenApiParameter("limit", int, OpenApiParameter.QUERY, required=False),
    ],
)
@api_view(["GET"])
def rankings(request, dim: str):
    if dim not in _RANKING_DIMS:
        return Response({"detail": f"dim must be one of {sorted(_RANKING_DIMS)}"},
                        status=status.HTTP_404_NOT_FOUND)
    try:
        limit = _clamp_limit(request.query_params, default=20, lo=1, hi=500)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(metrics.rankings(dim, limit=limit))


@extend_schema(
    tags=["Analytics"],
    responses={200: OpenApiTypes.OBJECT},
    parameters=[
        OpenApiParameter("model", int, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("limit", int, OpenApiParameter.QUERY, required=False),
    ],
)
@api_view(["GET"])
def regional(request):
    params = request.query_params
    try:
        model = _opt_int(params, "model")
        limit = _clamp_limit(params, default=20, lo=1, hi=500)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(metrics.regional(model_id=model, limit=limit))


@extend_schema(
    tags=["Analytics"],
    responses={200: OpenApiTypes.OBJECT},
    parameters=[
        OpenApiParameter("limit", int, OpenApiParameter.QUERY, required=False),
    ],
)
@api_view(["GET"])
def dealers(request):
    try:
        limit = _clamp_limit(request.query_params, default=20, lo=1, hi=500)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(metrics.dealer_stats(limit=limit))


@extend_schema(
    tags=["Analytics"],
    responses={200: OpenApiTypes.OBJECT},
    parameters=[
        OpenApiParameter("days", int, OpenApiParameter.QUERY, required=False),
    ],
)
@api_view(["GET"])
def inventory_trend(request, model_id: int):
    try:
        days = max(1, min(int(request.query_params.get("days", 90)), 3650))
    except ValueError:
        return Response({"detail": "days must be an integer"},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response(metrics.inventory_trend(model_id, days=days))


@extend_schema(
    tags=["Analytics"],
    responses={200: OpenApiTypes.OBJECT},
    parameters=[
        OpenApiParameter("days", int, OpenApiParameter.QUERY, required=False),
    ],
)
@api_view(["GET"])
def market_overview(request):
    try:
        days = max(1, min(int(request.query_params.get("days", 90)), 3650))
    except ValueError:
        return Response({"detail": "days must be an integer"},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response(metrics.market_overview(days=days))


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
        days = max(2, min(int(params.get("days", 90)), 3650))
    except (TypeError, ValueError):
        return Response({"detail": "days must be an integer"},
                        status=status.HTTP_400_BAD_REQUEST)

    series = read_index(scope, scope_id, days=days)
    latest = series[-1] if series else None
    return Response({
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
        "series": series,
    })


@extend_schema(
    tags=["Analytics"],
    responses={200: OpenApiTypes.OBJECT},
)
@api_view(["GET"])
def time_on_market(request, model_id: int):
    return Response(metrics.time_on_market(model_id))


@extend_schema(
    tags=["Analytics"],
    responses={200: OpenApiTypes.OBJECT},
    parameters=[
        OpenApiParameter("limit", int, OpenApiParameter.QUERY, required=False),
    ],
)
@api_view(["GET"])
def fast_movers(request, model_id: int):
    """Listings that left the feed fastest. Leaving ≠ sold — see metrics docstring."""
    try:
        limit = _clamp_limit(request.query_params, default=20, lo=1, hi=500)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(metrics.fast_movers(model_id, limit=limit))


@extend_schema(
    tags=["Analytics"],
    responses={200: OpenApiTypes.OBJECT},
    parameters=[
        OpenApiParameter("model", int, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("min_pct", float, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("days", int, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("limit", int, OpenApiParameter.QUERY, required=False),
    ],
)
@api_view(["GET"])
def price_drops(request):
    params = request.query_params
    try:
        model = _opt_int(params, "model")
        min_pct = _opt_float(params, "min_pct")
        days = max(1, min(int(params.get("days", 30)), 3650))
        limit = _clamp_limit(params, default=50, lo=1, hi=500)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(metrics.price_drops(
        model_id=model, min_pct=min_pct or 0.0, days=days, limit=limit,
    ))


def _listings_by_publish(order_sign: str, params):
    """Newest (desc) / oldest (asc) ACTIVE publish-complete ads, lightweight rows."""
    try:
        model = _opt_int(params, "model")
        limit = _clamp_limit(params, default=20, lo=1, hi=200)
    except ValueError as exc:
        raise ValueError(str(exc))
    # verified(): this is a listing endpoint, and it was reading the bare table
    # while every other analytical surface excluded hard-failed rows. "Newest
    # listings" showing an ad the rest of the site refuses to count is the kind
    # of inconsistency users report and nobody can reproduce.
    qs = verified(Ad.objects).filter(
        status=Ad.Status.ACTIVE, publish_at__isnull=False
    )
    if model is not None:
        qs = qs.filter(model_id=model)
    qs = qs.order_by(f"{order_sign}publish_at")[:limit]
    return list(qs.values("code", "title", "current_price", "year", "publish_at", "url"))


@extend_schema(
    tags=["Analytics"],
    responses={200: OpenApiTypes.OBJECT},
    parameters=[
        OpenApiParameter("model", int, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("limit", int, OpenApiParameter.QUERY, required=False),
    ],
)
@api_view(["GET"])
def newest(request):
    try:
        rows = _listings_by_publish("-", request.query_params)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(rows)


@extend_schema(
    tags=["Analytics"],
    responses={200: OpenApiTypes.OBJECT},
    parameters=[
        OpenApiParameter("model", int, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("limit", int, OpenApiParameter.QUERY, required=False),
    ],
)
@api_view(["GET"])
def oldest(request):
    try:
        rows = _listings_by_publish("", request.query_params)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(rows)
