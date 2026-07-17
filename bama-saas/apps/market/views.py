"""Market analytics endpoints: market landing, true-mean, Bollinger, price trends.

Paths use ``<int:model_id>`` rather than brand-slug/model-name because Bama model
names are Persian with spaces; an integer PK is unambiguous and avoids URL
encoding pain. The brand is implied by the model.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from django.db.models import Avg, Count, F, Max, Min
from django.db.models.functions import Trunc
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.analytics.services.bollinger import bollinger
from apps.analytics.services.truemean import true_mean
from apps.catalog.models import Ad, Model
from apps.market.models import PriceObservation

_BUCKET_CHOICES = {"day", "week", "month"}


def _opt_int(params, key):
    raw = params.get(key)
    if raw in (None, "", "null"):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be an integer")


@api_view(["GET"])
def markets(request):
    """Landing: per-model market summary (publish-complete, priced), top-N."""
    try:
        limit = max(1, min(int(request.query_params.get("limit", 100)), 500))
    except ValueError:
        limit = 100
    qs = (
        Ad.objects.filter(current_price__gt=0, publish_at__isnull=False)
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
    qs = PriceObservation.objects.filter(ad__model_id=model.id, price__gt=0)
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


@api_view(["GET"])
def ad_price_history(request, code: str):
    """Single ad's change-only price series over time."""
    ad = get_object_or_404(Ad, code=code)
    rows = (
        PriceObservation.objects.filter(ad=ad)
        .order_by("observed_at")
        .values("observed_at", "price", "payment", "prepayment",
                "installments", "price_type")
    )
    return Response({"code": ad.code, "current_price": ad.current_price,
                     "series": list(rows)})
