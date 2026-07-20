"""Core serializers, split by theme (catalog / history / market / analytics).

Flat import site preserved: ``from apps.core.serializers import AdSerializer``.
"""

from apps.core.serializers.analytics import DealScoreSerializer
from apps.core.serializers.catalog import (
    AdSerializer,
    BrandSerializer,
    CitySerializer,
    DealerSerializer,
    ModelSerializer,
    VariantSerializer,
)
from apps.core.serializers.history import (
    AdChangeEventSerializer,
    AdObservationSerializer,
    AdVersionSerializer,
    FetchRunSerializer,
    TimelineEntrySerializer,
)
from apps.core.serializers.market import PriceObservationSerializer

__all__ = [
    "BrandSerializer", "ModelSerializer", "VariantSerializer",
    "CitySerializer", "DealerSerializer", "AdSerializer",
    "FetchRunSerializer", "AdVersionSerializer", "AdObservationSerializer",
    "AdChangeEventSerializer", "TimelineEntrySerializer",
    "PriceObservationSerializer",
    "DealScoreSerializer",
]
