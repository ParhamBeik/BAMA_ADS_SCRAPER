"""Read-only DRF views for the catalog app (dimensions + Ad snapshot)."""

from django.shortcuts import get_object_or_404
from rest_framework import viewsets
from rest_framework.generics import ListAPIView

from apps.core.filters import AdFilter
from apps.core.models import Ad, Brand, Model, Variant
from apps.core.services.quality import verified, without_high_outliers
from apps.core.serializers import (
    AdSerializer,
    BrandSerializer,
    ModelSerializer,
    VariantSerializer,
)


class BrandViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/brands/ and GET /api/brands/<slug>/."""

    queryset = Brand.objects.all()
    serializer_class = BrandSerializer
    lookup_field = "slug"
    pagination_class = None


class BrandModelsView(ListAPIView):
    """GET /api/brands/<slug>/models/ — list models for a brand (by name_fa)."""

    serializer_class = ModelSerializer
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Model.objects.none()
        brand = get_object_or_404(Brand, slug=self.kwargs["brand_slug"])
        return Model.objects.filter(brand=brand).order_by("name_fa")


class ModelVariantsView(ListAPIView):
    """GET /api/models/<pk>/variants/ — list variants for a model."""

    serializer_class = VariantSerializer
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Variant.objects.none()
        model = get_object_or_404(Model, pk=self.kwargs["model_pk"])
        return Variant.objects.filter(model=model).order_by("name_fa")


class AdViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/ads/ and GET /api/ads/<code>/.

    The default list queryset is restricted to publish-complete ads (those with
    a `publish_at` and a strictly positive `current_price`) and drops rows the
    cohort pass flagged as priced far ABOVE their peers — ?include_outliers=true
    restores them. Suspiciously cheap listings are never hidden.
    """

    serializer_class = AdSerializer
    lookup_field = "code"
    filterset_class = AdFilter
    ordering_fields = (
        "current_price",
        "year",
        "year_jalali",
        "mileage",
        "publish_at",
        "last_seen_at",
        "image_count",
    )
    search_fields = ("title", "brand__name_fa", "model__name_fa")

    def get_queryset(self):
        # verified() and not the bare table: a row that failed a hard rule is one
        # the source itself sent broken, and it was still being listed and
        # filtered on here while every analytical read excluded it. The catalog
        # and the statistics have to describe the same population or a user can
        # find an ad the market summary says does not exist.
        #
        # Only *high* outliers are excluded from the browse list. A price far
        # above its peers is noise (a 206 was listed at 5.8 trillion toman); a
        # price far below them is the underpriced car this product exists to
        # find, so `without_cohort_outliers` — which drops both — must not be
        # used here. ?include_outliers=true restores the hidden ones.
        qs = verified(Ad.objects).select_related(
            "brand", "model", "variant", "city", "dealer",
        )
        # Detail view should be able to fetch any ad by code; only the list
        # view applies the publish-complete restriction.
        if self.action == "list":
            # ACTIVE only. Without this the browse feed carried every REMOVED ad
            # too: 57,864 rows against the market summary's 35,309 active, so the
            # two screens contradicted each other and a buyer could open a car
            # that had already left the market with nothing saying so. The detail
            # route below stays unrestricted on purpose — a saved ad that gets
            # delisted must still open, and it renders its own inactive notice.
            qs = qs.filter(
                status=Ad.Status.ACTIVE, publish_at__isnull=False, current_price__gt=0
            )
            include_outliers = self.request.query_params.get("include_outliers", "").lower() == "true"
            if not include_outliers:
                qs = without_high_outliers(qs)
        return qs
