"""Read-only DRF views for the catalog app (dimensions + Ad snapshot)."""

from django.shortcuts import get_object_or_404
from rest_framework import viewsets
from rest_framework.generics import ListAPIView

from apps.core.filters import AdFilter
from apps.core.models import Ad, Brand, Model, Variant
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
    a `publish_at` and a strictly positive `current_price`).
    """

    serializer_class = AdSerializer
    lookup_field = "code"
    filterset_class = AdFilter
    ordering_fields = (
        "current_price",
        "year",
        "mileage",
        "publish_at",
        "last_seen_at",
    )

    def get_queryset(self):
        qs = Ad.objects.all()
        # Detail view should be able to fetch any ad by code; only the list
        # view applies the publish-complete restriction.
        if self.action == "list":
            qs = qs.filter(publish_at__isnull=False, current_price__gt=0)
        return qs
