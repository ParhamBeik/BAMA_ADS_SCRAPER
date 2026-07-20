"""Core app models, split by theme but living in one Django app (label ``core``).

Import site stays flat: ``from apps.core.models import Ad, FetchRun, ...``.
The four modules mirror the old per-app split (catalog / history / market /
analytics) so the code is easy to navigate; the DB tables keep their original
``db_table`` names, so consolidating apps did not rename a single table.
"""

from apps.core.models.analytics import (
    AnalyticsCache,
    DailyInventorySnapshot,
    DealScoreCache,
    MarketSnapshot,
    PriceStatistics,
)
from apps.core.models.catalog import (
    Ad,
    Brand,
    City,
    Dealer,
    Model,
    Variant,
)
from apps.core.models.history import (
    AdChangeEvent,
    AdObservation,
    AdVersion,
    AuditRun,
    FetchRun,
    UnknownTimePhrase,
)
from apps.core.models.price import PriceDropEvent, PriceObservation

__all__ = [
    # catalog
    "Brand", "Model", "Variant", "City", "Dealer", "Ad",
    # history / provenance
    "FetchRun", "AdVersion", "AdObservation", "AdChangeEvent",
    "AuditRun", "UnknownTimePhrase",
    # market / price
    "PriceObservation", "PriceDropEvent",
    # analytics
    "PriceStatistics", "AnalyticsCache", "DailyInventorySnapshot",
    "DealScoreCache", "MarketSnapshot",
]
