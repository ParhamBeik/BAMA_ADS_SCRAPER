"""Core URL configuration (merged catalog + history + market + analytics).

All routes are mounted under ``/api/`` (see config/urls.py), so the prefixes
below are relative to ``/api/``. Paths are unchanged from the pre-merge apps.
"""

from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.core import views
from apps.core.views import research
from apps.core.views.product import AdsExportView, ModelCompareView

app_name = "core"

router = DefaultRouter()
# catalog
router.register(r"brands", views.BrandViewSet, basename="brand")
router.register(r"ads", views.AdViewSet, basename="ad")
# history
router.register(r"changes", views.ChangeViewSet, basename="change")
router.register(r"observations", views.ObservationViewSet, basename="observation")
router.register(r"fetch-runs", views.FetchRunViewSet, basename="fetchrun")

urlpatterns = [
    # --- catalog ---
    path("brands/<slug:brand_slug>/models/", views.BrandModelsView.as_view(), name="brand-models"),
    path("models/<int:model_pk>/variants/", views.ModelVariantsView.as_view(), name="model-variants"),

    # --- history (per-ad provenance, nested under ads/<code>/) ---
    path("ads/<str:code>/versions/", views.AdVersionsView.as_view(), name="ad-versions"),
    path("ads/<str:code>/changes/", views.AdChangesView.as_view(), name="ad-changes"),
    path("ads/<str:code>/timeline/", views.AdTimelineView.as_view(), name="ad-timeline"),

    # --- market ---
    path("markets/", views.markets, name="markets"),
    path("markets/<int:model_id>/true-mean/", views.market_true_mean, name="true-mean"),
    path("markets/<int:model_id>/bollinger/", views.market_bollinger, name="bollinger"),
    path("markets/<int:model_id>/price-trends/", views.market_price_trends, name="price-trends"),
    path("ads/<str:code>/price-history/", views.ad_price_history, name="ad-price-history"),

    # --- analytics: legacy insights keeps bare /api/insights/... ---
    path("insights/<int:model_id>/<str:kind>/", views.insight, name="insight"),

    # --- analytics: deal scores (under /api/analytics/...) ---
    path("analytics/deal-scores/", views.deal_scores, name="deal-scores"),
    path("analytics/deal-scores/<str:code>/", views.deal_score_detail, name="deal-score-detail"),

    # --- analytics: metrics ---
    path("analytics/rankings/<str:dim>/", views.rankings, name="rankings"),
    path("analytics/regional/", views.regional, name="regional"),
    path("analytics/dealers/", views.dealers, name="dealers"),
    path("analytics/inventory-trends/<int:model_id>/", views.inventory_trend, name="inventory-trend"),
    path("analytics/market-overview/", views.market_overview, name="market-overview"),
    path("analytics/market-index/", views.market_index, name="market-index"),
    path("analytics/time-on-market/<int:model_id>/", views.time_on_market, name="time-on-market"),
    path("analytics/fast-movers/<int:model_id>/", views.fast_movers, name="fast-movers"),
    path("analytics/price-drops/", views.price_drops, name="price-drops"),

    # --- research: the three insight products (see views/research.py) ---
    path("analytics/overview/", research.overview_view, name="market-overview-public"),
    path("research/liquidity/<int:model_id>/", research.liquidity_view, name="liquidity"),
    path("research/price-position/<int:model_id>/", research.price_position_view, name="price-position"),
    path("research/negotiation/<int:model_id>/", research.negotiation_view, name="negotiation"),
    path("research/depreciation/<int:model_id>/", research.depreciation_view, name="depreciation"),
    path("research/dispersion/", research.dispersion_view, name="dispersion"),
    path("research/retention/", research.retention_view, name="retention"),
    path("research/regional/", research.regional_view, name="regional-adjusted"),
    path("ads/<str:code>/fair-price/", research.fair_price_view, name="fair-price"),
    path("ads/<str:code>/identity/", research.ad_identity_view, name="ad-identity"),

    # --- analytics: newest / oldest listings ---
    path("research/compare/", ModelCompareView.as_view(), name="model-compare"),
    path("ads/export/", AdsExportView.as_view(), name="ads-export"),
    path("analytics/newest/", views.newest, name="newest"),
    path("analytics/oldest/", views.oldest, name="oldest"),
] + router.urls
