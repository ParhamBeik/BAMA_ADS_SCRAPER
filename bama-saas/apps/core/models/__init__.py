"""Core app models, split by theme but living in one Django app (label ``core``).

Import site stays flat: ``from apps.core.models import Ad, FetchRun, ...``.
The four modules mirror the old per-app split (catalog / history / market /
analytics) so the code is easy to navigate; the DB tables keep their original
``db_table`` names, so consolidating apps did not rename a single table.
"""

from apps.core.models.analytics import (
    DailyInventorySnapshot,
    DealScoreCache,
    MarketIndex,
    MarketSnapshot,
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
    DataQualitySnapshot,
    FetchRun,
    JobRun,
    ListingEpisode,
    IngestReject,
    PageCoverage,
    UnknownTimePhrase,
    VehicleIdentity,
)
from apps.core.models.price import PriceDropEvent, PriceObservation

__all__ = [
    # catalog
    "Brand", "Model", "Variant", "City", "Dealer", "Ad",
    # history / provenance
    "FetchRun", "AdVersion", "AdObservation", "AdChangeEvent",
    "UnknownTimePhrase", "PageCoverage", "IngestReject",
    "DataQualitySnapshot", "JobRun",
    "VehicleIdentity", "ListingEpisode",
    # market / price
    "PriceObservation", "PriceDropEvent",
    # analytics
    "DailyInventorySnapshot",
    "DealScoreCache", "MarketSnapshot", "MarketIndex",
]
