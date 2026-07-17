"""Catalog URL configuration.

Routes are mounted under /api/ (see config/urls.py), so the prefixes below are
relative to /api/.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AdViewSet,
    BrandModelsView,
    BrandViewSet,
    ModelVariantsView,
)

app_name = "catalog"

router = DefaultRouter()
router.register(r"brands", BrandViewSet, basename="brand")
router.register(r"ads", AdViewSet, basename="ad")

urlpatterns = [
    path("brands/<slug:brand_slug>/models/", BrandModelsView.as_view(), name="brand-models"),
    path("models/<int:model_pk>/variants/", ModelVariantsView.as_view(), name="model-variants"),
] + router.urls
