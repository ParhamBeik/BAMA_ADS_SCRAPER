"""Staff read-only window over catalog and ingestion tables.

These endpoints are the ops UI's source of truth. They query the same rows the
worker writes and do **not** apply ``verified()`` — a hard-flagged or removed
ad must still be inspectable. Public serializers stay curated.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsStaff
from apps.core.models import (
    Ad,
    Brand,
    FetchRun,
    IngestReject,
    Model,
    PageCoverage,
    Variant,
)
from apps.jobs.services.coverage import find_gaps, known_feed_depth


def _page_spec(request, default=50, cap=200):
    page = max(int(request.query_params.get("page", 1) or 1), 1)
    size = min(max(int(request.query_params.get("page_size", default) or default), 1), cap)
    return page, size, (page - 1) * size


def _ad_row(ad: Ad, *, detail: bool = False) -> dict:
    row = {
        "code": ad.code,
        "title": ad.title,
        "status": ad.status,
        "brand_slug": ad.brand_id,
        "brand_name": ad.brand.name_fa if ad.brand_id else None,
        "model_id": ad.model_id,
        "model_name": ad.model.name_fa if ad.model_id else None,
        "variant_id": ad.variant_id,
        "variant_name": ad.variant.name_fa if ad.variant_id else None,
        "year": ad.year,
        "year_jalali": ad.year_jalali,
        "year_gregorian": ad.year_gregorian,
        "year_calendar": ad.year_calendar,
        "mileage": ad.mileage,
        "category": ad.category,
        "current_price": ad.current_price,
        "price_type": ad.price_type,
        "first_seen_at": ad.first_seen_at,
        "last_seen_at": ad.last_seen_at,
        "publish_at": ad.publish_at,
        "removed_at": ad.removed_at,
        "quality_flags": ad.quality_flags or [],
        "cohort_flags": ad.cohort_flags or [],
        "city_name": ad.city.name_fa if ad.city_id else None,
        "dealer_id": ad.dealer_id,
        "trim": ad.trim,
    }
    if detail:
        row.update({
            "transmission": ad.transmission,
            "body_type": ad.body_type,
            "body_color": ad.body_color,
            "body_status": ad.body_status,
            "fuel": ad.fuel,
            "url": ad.url,
            "canonical_path": ad.canonical_path,
            "source_modified_at": ad.source_modified_at,
            "description": ad.description,
            "image_count": ad.image_count,
            "seller_authenticated": ad.seller_authenticated,
            "raw_payload": ad.raw_payload,
        })
    return row


class InspectAdsView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin-inspect"])
    def get(self, request):
        qs = Ad.objects.select_related("brand", "model", "variant", "city").all()
        params = request.query_params
        if code := (params.get("code") or "").strip():
            qs = qs.filter(code__iexact=code)
        if status := (params.get("status") or "").strip():
            qs = qs.filter(status=status)
        if brand := (params.get("brand") or "").strip():
            qs = qs.filter(brand_id=brand)
        if model := (params.get("model") or "").strip():
            qs = qs.filter(model_id=int(model))
        if variant := (params.get("variant") or "").strip():
            qs = qs.filter(variant_id=int(variant))
        if flag := (params.get("quality_flag") or "").strip():
            qs = qs.filter(quality_flags__contains=[flag])
        if params.get("flagged") in ("1", "true"):
            qs = qs.exclude(Q(quality_flags=[]) | Q(quality_flags__isnull=True))
        if q := (params.get("q") or "").strip():
            qs = qs.filter(Q(code__icontains=q) | Q(title__icontains=q))
        qs = qs.order_by("-last_seen_at", "-publish_at")
        page, size, start = _page_spec(request)
        count = qs.count()
        rows = [_ad_row(ad) for ad in qs[start:start + size]]
        return Response({"count": count, "page": page, "page_size": size, "results": rows})


class InspectAdDetailView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin-inspect"])
    def get(self, request, code: str):
        ad = get_object_or_404(
            Ad.objects.select_related("brand", "model", "variant", "city", "dealer"),
            code=code,
        )
        body = _ad_row(ad, detail=True)
        body["observation_count"] = ad.observations.count()
        body["version_count"] = ad.versions.count()
        return Response(body)


class InspectBrandsView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin-inspect"])
    def get(self, request):
        qs = Brand.objects.annotate(
            ad_count=Count("ads"),
            active_ad_count=Count("ads", filter=Q(ads__status=Ad.Status.ACTIVE)),
        ).order_by("name_fa")
        if request.query_params.get("unconfirmed") in ("1", "true"):
            qs = qs.filter(is_confirmed=False)
        page, size, start = _page_spec(request, default=100)
        count = qs.count()
        rows = [
            {
                "slug": b.slug,
                "name_fa": b.name_fa,
                "name_en": b.name_en,
                "is_confirmed": b.is_confirmed,
                "ad_count": b.ad_count,
                "active_ad_count": b.active_ad_count,
            }
            for b in qs[start:start + size]
        ]
        return Response({"count": count, "page": page, "page_size": size, "results": rows})


class InspectModelsView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin-inspect"])
    def get(self, request):
        qs = Model.objects.select_related("brand").annotate(
            ad_count=Count("ads"),
            active_ad_count=Count("ads", filter=Q(ads__status=Ad.Status.ACTIVE)),
        ).order_by("brand__name_fa", "name_fa")
        if brand := (request.query_params.get("brand") or "").strip():
            qs = qs.filter(brand_id=brand)
        if request.query_params.get("unconfirmed") in ("1", "true"):
            qs = qs.filter(is_confirmed=False)
        page, size, start = _page_spec(request, default=100)
        count = qs.count()
        rows = [
            {
                "id": m.id,
                "brand_slug": m.brand_id,
                "brand_name": m.brand.name_fa,
                "name_fa": m.name_fa,
                "is_confirmed": m.is_confirmed,
                "ad_count": m.ad_count,
                "active_ad_count": m.active_ad_count,
            }
            for m in qs[start:start + size]
        ]
        return Response({"count": count, "page": page, "page_size": size, "results": rows})


class InspectVariantsView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin-inspect"])
    def get(self, request):
        qs = Variant.objects.select_related("model", "model__brand").annotate(
            ad_count=Count("ads"),
            active_ad_count=Count("ads", filter=Q(ads__status=Ad.Status.ACTIVE)),
        ).order_by("model__name_fa", "name_fa")
        if model := (request.query_params.get("model") or "").strip():
            qs = qs.filter(model_id=int(model))
        page, size, start = _page_spec(request, default=100)
        count = qs.count()
        rows = [
            {
                "id": v.id,
                "model_id": v.model_id,
                "model_name": v.model.name_fa,
                "brand_slug": v.model.brand_id,
                "name_fa": v.name_fa,
                "ad_count": v.ad_count,
                "active_ad_count": v.active_ad_count,
            }
            for v in qs[start:start + size]
        ]
        return Response({"count": count, "page": page, "page_size": size, "results": rows})


def _fetch_run_row(run: FetchRun) -> dict:
    return {
        "id": str(run.id),
        "source": run.source,
        "status": run.status,
        "mode": run.mode,
        "start_page": run.start_page,
        "pages_fetched": run.pages_fetched,
        "deepest_rank": run.deepest_rank,
        "reached_end": run.reached_end,
        "stop_reason": run.stop_reason,
        "resume_from_page": run.resume_from_page,
        "fetched_count": run.fetched_count,
        "created_count": run.created_count,
        "updated_count": run.updated_count,
        "skipped_count": run.skipped_count,
        "price_change_count": run.price_change_count,
        "error": run.error,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
    }


class InspectFetchRunsView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin-inspect"])
    def get(self, request):
        qs = FetchRun.objects.all().order_by("-created_at")
        if status := (request.query_params.get("status") or "").strip():
            qs = qs.filter(status=status)
        if mode := (request.query_params.get("mode") or "").strip():
            qs = qs.filter(mode=mode)
        page, size, start = _page_spec(request)
        count = qs.count()
        rows = [_fetch_run_row(r) for r in qs[start:start + size]]
        return Response({"count": count, "page": page, "page_size": size, "results": rows})


class InspectFetchRunPagesView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin-inspect"])
    def get(self, request, run_id):
        run = get_object_or_404(FetchRun, pk=run_id)
        qs = PageCoverage.objects.filter(fetch_run=run).order_by("page_index")
        page, size, start = _page_spec(request, default=100, cap=500)
        count = qs.count()
        rows = list(qs[start:start + size].values(
            "page_index", "rank_lo", "rank_hi", "ad_count",
            "new_count", "changed_count", "fetched_at",
        ))
        return Response({
            "fetch_run": _fetch_run_row(run),
            "count": count,
            "page": page,
            "page_size": size,
            "results": rows,
        })


class InspectGapsView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin-inspect"])
    def get(self, request):
        hours = int(request.query_params.get("since_hours", 24) or 24)
        since = timezone.now() - timedelta(hours=hours)
        depth = known_feed_depth()
        gaps = find_gaps(since=since, max_rank=depth)
        return Response({
            "since_hours": hours,
            "known_feed_depth": depth,
            "gap_count": len(gaps),
            "gaps": [{"rank_lo": lo, "rank_hi": hi} for lo, hi in gaps],
        })


class InspectRejectsView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin-inspect"])
    def get(self, request):
        qs = IngestReject.objects.all().order_by("-observed_at")
        if rule := (request.query_params.get("rule") or "").strip():
            qs = qs.filter(rule=rule)
        if code := (request.query_params.get("code") or "").strip():
            qs = qs.filter(code__iexact=code)
        page, size, start = _page_spec(request)
        count = qs.count()
        rows = list(qs[start:start + size].values(
            "id", "code", "rule", "detail", "raw_payload", "observed_at", "fetch_run_id",
        ))
        for row in rows:
            row["fetch_run_id"] = str(row["fetch_run_id"]) if row["fetch_run_id"] else None
        return Response({"count": count, "page": page, "page_size": size, "results": rows})
