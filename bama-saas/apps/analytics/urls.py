"""Analytics URL configuration.

The app is mounted under ``/api/`` (see config/urls.py). The legacy ``insights``
route keeps its bare ``/api/insights/...`` path for back-compat; the Phase-4
deal-score / metrics endpoints sit under ``/api/analytics/...`` as specified.
"""

from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("insights/<int:model_id>/<str:kind>/", views.insight, name="insight"),

    # Deal scores (under /api/analytics/...)
    path("analytics/deal-scores/", views.deal_scores, name="deal-scores"),
    path("analytics/deal-scores/<str:code>/", views.deal_score_detail, name="deal-score-detail"),

    # Metrics
    path("analytics/rankings/<str:dim>/", views.rankings, name="rankings"),
    path("analytics/regional/", views.regional, name="regional"),
    path("analytics/dealers/", views.dealers, name="dealers"),
    path("analytics/inventory-trends/<int:model_id>/", views.inventory_trend, name="inventory-trend"),
    path("analytics/market-overview/", views.market_overview, name="market-overview"),
    path("analytics/time-on-market/<int:model_id>/", views.time_on_market, name="time-on-market"),
    path("analytics/fast-sellers/<int:model_id>/", views.fast_sellers, name="fast-sellers"),
    path("analytics/price-drops/", views.price_drops, name="price-drops"),

    # Newest / oldest listings
    path("analytics/newest/", views.newest, name="newest"),
    path("analytics/oldest/", views.oldest, name="oldest"),
]
