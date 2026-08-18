"""Core URL configuration (catalog + market + analytics + research).

All routes are mounted under ``/api/`` (see config/urls.py), so the prefixes
below are relative to ``/api/``. Every route here backs a screen; the ~25 that
backed nothing were deleted along with their views.
"""

from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.core import views
from apps.core.views import notifier, research

app_name = "core"

router = DefaultRouter()
router.register(r"brands", views.BrandViewSet, basename="brand")
router.register(r"ads", views.AdViewSet, basename="ad")

urlpatterns = [
    # --- catalog ---
    path("brands/<slug:brand_slug>/models/", views.BrandModelsView.as_view(), name="brand-models"),
    path("models/<int:model_pk>/variants/", views.ModelVariantsView.as_view(), name="model-variants"),

    # --- market ---
    path("markets/", views.markets, name="markets"),
    path("ads/<str:code>/price-history/", views.ad_price_history, name="ad-price-history"),

    # --- analytics: the deal board and the index ---
    path("analytics/deal-scores/", views.deal_scores, name="deal-scores"),
    path("analytics/deal-scores/<str:code>/", views.deal_score_detail, name="deal-score-detail"),
    path("analytics/market-index/", views.market_index, name="market-index"),

    # --- research (see views/research.py) ---
    path("analytics/overview/", research.overview_view, name="market-overview-public"),
    path("research/liquidity/<int:model_id>/", research.liquidity_view, name="liquidity"),
    path("research/depreciation/<int:model_id>/", research.depreciation_view, name="depreciation"),
    path("ads/<str:code>/fair-price/", research.fair_price_view, name="fair-price"),

    # --- notifier rules (edited from the deal board) ---
    path("notifier-settings/", notifier.notifier_settings, name="notifier-settings"),
] + router.urls
