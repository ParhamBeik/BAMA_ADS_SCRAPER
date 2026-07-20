"""Core views, split by theme (catalog / history / market / analytics).

Flat import site preserved: ``from apps.core.views import AdViewSet, markets``.
"""

from apps.core.views.catalog import (
    AdViewSet,
    BrandModelsView,
    BrandViewSet,
    ModelVariantsView,
)
from apps.core.views.history import (
    AdChangesView,
    AdTimelineView,
    AdVersionsView,
    ChangeViewSet,
    FetchRunViewSet,
    ObservationViewSet,
)
from apps.core.views.market import (
    ad_price_history,
    market_bollinger,
    market_price_trends,
    market_true_mean,
    markets,
)
from apps.core.views.analytics import (
    dealers,
    deal_score_detail,
    deal_scores,
    fast_sellers,
    insight,
    inventory_trend,
    market_overview,
    newest,
    oldest,
    price_drops,
    rankings,
    regional,
    time_on_market,
)

__all__ = [
    # catalog
    "BrandViewSet", "BrandModelsView", "ModelVariantsView", "AdViewSet",
    # history
    "AdVersionsView", "AdChangesView", "AdTimelineView",
    "ChangeViewSet", "ObservationViewSet", "FetchRunViewSet",
    # market
    "markets", "market_true_mean", "market_bollinger",
    "market_price_trends", "ad_price_history",
    # analytics
    "insight", "deal_scores", "deal_score_detail", "rankings", "regional",
    "dealers", "inventory_trend", "market_overview", "time_on_market",
    "fast_sellers", "price_drops", "newest", "oldest",
]
