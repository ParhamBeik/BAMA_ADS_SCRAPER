"""Routes under ``/api/``. Every one of these backs a screen."""

from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.core import views

app_name = "core"

router = DefaultRouter()
router.register(r"brands", views.BrandViewSet, basename="brand")
router.register(r"ads", views.AdViewSet, basename="ad")

urlpatterns = [
    path("brands/<slug:brand_slug>/models/", views.BrandModelsView.as_view(), name="brand-models"),
    path("models/", views.model_search, name="model-search"),
    path("models/<int:model_pk>/variants/", views.ModelVariantsView.as_view(),
         name="model-variants"),

    # Listing photos, proxied and cached. Not under /ads/ because it is bytes,
    # not JSON, and it is the one route the SPA points an <img> at.
    # `thumb` is the card-sized file, a genuinely different (narrower) upload
    # than gallery photo 0 — see images.thumb_path.
    path("img/<str:code>/thumb/", views.listing_image, name="listing-thumb"),
    path("img/<str:code>/<int:index>/", views.listing_image, name="listing-image"),

    path("markets/", views.markets, name="markets"),
    path("ads/<str:code>/price-history/", views.ad_price_history, name="ad-price-history"),

    path("analytics/deal-scores/", views.deal_scores, name="deal-scores"),
    path("analytics/deal-scores/<str:code>/", views.deal_score_detail, name="deal-score-detail"),
    path("analytics/market-index/", views.market_index, name="market-index"),
    path("analytics/overview/", views.overview_view, name="market-overview-public"),

    path("research/liquidity/<int:model_id>/", views.liquidity_view, name="liquidity"),
    path("research/depreciation/<int:model_id>/", views.depreciation_view, name="depreciation"),
    path("ads/<str:code>/fair-price/", views.fair_price_view, name="fair-price"),

    path("notifier-settings/", views.notifier_settings, name="notifier-settings"),
] + router.urls
