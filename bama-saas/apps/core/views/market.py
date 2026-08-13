"""Market analytics endpoints: market landing, true-mean, Bollinger, price trends.

Paths use ``<int:model_id>`` rather than brand-slug/model-name because Bama model
names are Persian with spaces; an integer PK is unambiguous and avoids URL
encoding pain. The brand is implied by the model.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from django.db.models import Avg, Count, F, Max, Min
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import cache_page
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.core.services.bollinger import bollinger
from apps.core.services.quality import verified, verified_by_ad
from apps.core.services.truemean import true_mean
from apps.core.models import Ad, Model
from apps.core.models import PriceObservation

_BUCKET_CHOICES = {"day", "week", "month"}

# Shorter than the 5-minute worker tick, so a cached landing page is never more
# than one cycle behind the data it summarises.
MARKETS_CACHE_SECONDS = 120


def _opt_int(params, key):
    raw = params.get(key)
    if raw in (None, "", "null"):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be an integer")


@extend_schema(
    tags=["Markets"],
    responses={200: OpenApiTypes.OBJECT},
    parameters=[OpenApiParameter("limit", int, OpenApiParameter.QUERY, required=False)],
)
@cache_page(MARKETS_CACHE_SECONDS)
@api_view(["GET"])
def markets(request):
    """Landing: per-model market summary (publish-complete, priced), top-N.

    Cached: this aggregates every priced ad in the database on each call and the
    underlying data only changes when the worker ticks. Safe to share across
    users — the response contains no per-user data.
    """
    try:
        limit = max(1, min(int(request.query_params.get("limit", 100)), 500))
    except ValueError:
        limit = 100
    qs = (
        verified(Ad.objects)
        .filter(current_price__gt=0, publish_at__isnull=False)
        .annotate(
            model_name=F("model__name_fa"),
            brand_slug=F("model__brand__slug"),
            brand_name=F("model__brand__name_fa"),
        )
        .values("model_id", "model_name", "brand_slug", "brand_name")
        .annotate(
            ad_count=Count("code"),  # Ad PK is `code`, not `id`
            min_price=Min("current_price"),
            max_price=Max("current_price"),
            avg_price=Avg("current_price"),
        )
        .order_by("-ad_count")[:limit]
    )
    return Response(list(qs))


@extend_schema(
    tags=["Markets"],
    responses={200: OpenApiTypes.OBJECT},
    parameters=[
        OpenApiParameter("variant", int, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("year", int, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("method", str, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("z", float, OpenApiParameter.QUERY, required=False),
    ],
)
@api_view(["GET"])
def market_true_mean(request, model_id: int):
    """True-mean (outlier-trimmed) price for a model/variant/year peer group."""
    model = get_object_or_404(Model, id=model_id)
    params = request.query_params
    try:
        variant = _opt_int(params, "variant")
        year = _opt_int(params, "year")
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    method = params.get("method", "zscore")
    if method not in ("zscore", "percentile"):
        return Response({"detail": "method must be 'zscore' or 'percentile'"},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        z = float(params.get("z", 2.0))
    except ValueError:
        return Response({"detail": "z must be a number"}, status=status.HTTP_400_BAD_REQUEST)

    data = true_mean(model.id, variant_id=variant, year=year, method=method, z=z)
    data["model_name"] = model.name_fa
    data["brand_name"] = model.brand.name_fa
    return Response(data)


@extend_schema(
    tags=["Charts"],
    responses={200: OpenApiTypes.OBJECT},
    parameters=[
        OpenApiParameter("window", int, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("sigma", float, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("variant", int, OpenApiParameter.QUERY, required=False),
    ],
)
@api_view(["GET"])
def market_bollinger(request, model_id: int):
    """Bollinger-style price spectrum over time for a model."""
    model = get_object_or_404(Model, id=model_id)
    params = request.query_params
    try:
        window = max(1, int(params.get("window", 20)))
        sigma = float(params.get("sigma", 2.0))
    except ValueError:
        return Response({"detail": "window/sigma must be numbers"},
                        status=status.HTTP_400_BAD_REQUEST)
    variant = _opt_int(params, "variant")
    data = bollinger(model.id, variant_id=variant, window=window, sigma=sigma)
    data["model_name"] = model.name_fa
    data["brand_name"] = model.brand.name_fa
    return Response(data)


@extend_schema(
    tags=["Market history"],
    responses={200: OpenApiTypes.OBJECT},
    parameters=[
        OpenApiParameter("bucket", str, OpenApiParameter.QUERY, required=False),
        OpenApiParameter("variant", int, OpenApiParameter.QUERY, required=False),
    ],
)
@api_view(["GET"])
def market_price_trends(request, model_id: int):
    """Median + count of observed prices per day/week/month for a model."""
    model = get_object_or_404(Model, id=model_id)
    bucket = request.query_params.get("bucket", "month")
    if bucket not in _BUCKET_CHOICES:
        return Response({"detail": f"bucket must be one of {sorted(_BUCKET_CHOICES)}"},
                        status=status.HTTP_400_BAD_REQUEST)
    variant = _opt_int(request.query_params, "variant")

    # Pull the priced change-only series and bucket+median in Python so the trend
    # is outlier-robust (median), not mean-skewed.
    qs = verified_by_ad(
        PriceObservation.objects.filter(ad__model_id=model.id, price__gt=0)
    )
    if variant:
        qs = qs.filter(ad__variant_id=variant)
    rows = qs.values_list("observed_at", "price")
    buckets: dict = defaultdict(list)
    for observed_at, price in rows:
        if observed_at is None:
            continue
        if bucket == "day":
            key = observed_at.date().isoformat()
        elif bucket == "week":
            iso = observed_at.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
        else:
            key = f"{observed_at.year:04d}-{observed_at.month:02d}"
        buckets[key].append(price)

    series = [
        {"bucket": key, "median": int(statistics.median(prices)),
         "mean": int(statistics.mean(prices)), "count": len(prices)}
        for key, prices in sorted(buckets.items())
    ]
    return Response({"model_id": model.id, "model_name": model.name_fa,
                     "bucket": bucket, "series": series})


@extend_schema(tags=["Price history"], responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def ad_price_history(request, code: str):
    """Single ad's change-only price series over time."""
    ad = get_object_or_404(verified(Ad.objects.all()), code=code)
    rows = (
        verified_by_ad(PriceObservation.objects.filter(ad=ad))
        .order_by("observed_at")
        .values("observed_at", "price", "payment", "prepayment",
                "installments", "price_type")
    )
    return Response({"code": ad.code, "current_price": ad.current_price,
                     "series": list(rows)})
