"""Market endpoints: the per-model market landing and a single ad's price series.

Paths use ``<int:model_id>`` rather than brand-slug/model-name because Bama model
names are Persian with spaces; an integer PK is unambiguous and avoids URL
encoding pain. The brand is implied by the model.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from django.db.models import F
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import cache_page
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.core.models import Ad, PriceObservation
from apps.core.services.quality import verified, verified_by_ad

# Shorter than the 5-minute worker tick, so a cached landing page is never more
# than one cycle behind the data it summarises.
MARKETS_CACHE_SECONDS = 120


@extend_schema(
    tags=["Markets"],
    responses={200: OpenApiTypes.OBJECT},
    parameters=[OpenApiParameter("limit", int, OpenApiParameter.QUERY, required=False)],
)
@cache_page(MARKETS_CACHE_SECONDS)
@api_view(["GET"])
def markets(request):
    """Landing: per-model market summary (publish-complete, priced), top-N.

    Reports the **median**, not the mean. Asking prices on this feed are heavily
    right-skewed and a handful of typo listings (a Peugeot 206 at 5.8 trillion
    toman) dragged the mean to roughly twice the median, so the headline number
    described no car actually on the market. Medians are computed in Python:
    there is no portable ORM median aggregate, the input is one narrow
    two-column scan, and the response is cached for two minutes.
    """
    try:
        limit = max(1, min(int(request.query_params.get("limit", 100)), 500))
    except ValueError:
        limit = 100

    rows = (
        verified(Ad.objects)
        .filter(current_price__gt=0, publish_at__isnull=False)
        .annotate(
            model_name=F("model__name_fa"),
            brand_slug=F("model__brand__slug"),
            brand_name=F("model__brand__name_fa"),
        )
        .values_list(
            "model_id", "model_name", "brand_slug", "brand_name", "current_price"
        )
    )

    prices: dict = defaultdict(list)
    meta: dict = {}
    for model_id, model_name, brand_slug, brand_name, price in rows.iterator():
        prices[model_id].append(price)
        if model_id not in meta:
            meta[model_id] = (model_name, brand_slug, brand_name)

    out = []
    for model_id, values in prices.items():
        model_name, brand_slug, brand_name = meta[model_id]
        out.append({
            "model_id": model_id,
            "model_name": model_name,
            "brand_slug": brand_slug,
            "brand_name": brand_name,
            "ad_count": len(values),
            "min_price": min(values),
            "max_price": max(values),
            "median_price": int(statistics.median(values)),
        })
    out.sort(key=lambda r: r["ad_count"], reverse=True)
    return Response(out[:limit])


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
