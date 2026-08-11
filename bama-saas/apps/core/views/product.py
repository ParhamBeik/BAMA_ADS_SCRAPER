"""Pro product endpoints: model comparison and CSV export."""

from __future__ import annotations

import csv
from io import StringIO

from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.entitlements import require_feature, require_verified
from apps.core.filters import AdFilter
from apps.core.models import Ad, Model
from apps.core.services.quality import verified
from apps.core.serializers import AdSerializer


class ModelCompareView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["research"])
    def get(self, request):
        require_verified(request.user)
        require_feature(request.user, "model_comparison")
        raw = request.query_params.get("ids", "")
        try:
            ids = [int(x) for x in raw.split(",") if x.strip()]
        except ValueError:
            return Response({"detail": "ids must be integers"}, status=400)
        if not (2 <= len(ids) <= 3):
            return Response({"detail": "Compare 2 or 3 models."}, status=400)
        models = list(Model.objects.filter(pk__in=ids).select_related("brand"))
        if len(models) != len(set(ids)):
            return Response({"detail": "One or more models not found."}, status=404)
        out = []
        for m in models:
            qs = verified(Ad.objects).filter(model=m, status=Ad.Status.ACTIVE, current_price__gt=0)
            prices = list(qs.values_list("current_price", flat=True)[:5000])
            prices.sort()
            n = len(prices)
            median = prices[n // 2] if n else None
            out.append({
                "model_id": m.id,
                "name_fa": m.name_fa,
                "brand": m.brand.name_fa,
                "inventory": n,
                "median_price": median,
                "min_price": prices[0] if prices else None,
                "max_price": prices[-1] if prices else None,
                "available": n >= 5,
                "reason": None if n >= 5 else "insufficient_data",
            })
        return Response({"models": out})


class AdsExportView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["catalog"])
    def get(self, request):
        require_verified(request.user)
        require_feature(request.user, "csv_export")
        qs = verified(Ad.objects).filter(publish_at__isnull=False, current_price__gt=0)
        qs = AdFilter(request.query_params, queryset=qs).qs
        qs = qs.select_related("brand", "model", "variant", "city")[:10_000]
        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "code", "brand", "model", "variant", "year_jalali", "mileage",
            "price", "city", "transmission", "url",
        ])
        for ad in qs.iterator(chunk_size=500):
            writer.writerow([
                ad.code,
                ad.brand.name_fa if ad.brand_id else "",
                ad.model.name_fa if ad.model_id else "",
                ad.variant.name_fa if ad.variant_id else "",
                ad.year_jalali or "",
                ad.mileage or "",
                ad.current_price or "",
                ad.city.name_fa if ad.city_id else "",
                ad.transmission,
                ad.url,
            ])
        resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="bama-ads.csv"'
        return resp
