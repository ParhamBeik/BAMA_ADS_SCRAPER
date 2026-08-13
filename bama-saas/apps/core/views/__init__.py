"""Core views, split by theme (catalog / market / analytics).

Flat import site preserved: ``from apps.core.views import AdViewSet, markets``.
"""

from apps.core.views.catalog import (
    AdViewSet,
    BrandModelsView,
    BrandViewSet,
    ModelVariantsView,
)
from apps.core.views.market import (
    ad_price_history,
    markets,
)
from apps.core.views.analytics import (
    deal_score_detail,
    deal_scores,
    market_index,
)

__all__ = [
    # catalog
    "BrandViewSet", "BrandModelsView", "ModelVariantsView", "AdViewSet",
    # market
    "markets", "ad_price_history",
    # analytics
    "deal_scores", "deal_score_detail", "market_index",
]
