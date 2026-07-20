"""Analytics serializers.

Most analytics endpoints return plain list/dict payloads from the
function-based views (matching the pattern in ``apps/core/views/market.py``) and so
do not need a serializer. ``DealScoreSerializer`` is provided for any consumer
that wants to (de)serialize a DealScoreCache row directly.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.core.models import DealScoreCache


class DealScoreSerializer(serializers.ModelSerializer):
    code = serializers.CharField(source="ad_id", read_only=True)
    price = serializers.IntegerField(source="ad.current_price", read_only=True)
    year = serializers.IntegerField(source="ad.year", read_only=True)
    mileage = serializers.IntegerField(source="ad.mileage", read_only=True)
    url = serializers.CharField(source="ad.url", read_only=True)
    title = serializers.CharField(source="ad.title", read_only=True)
    model_name = serializers.CharField(source="ad.model__name_fa", read_only=True)
    brand_name = serializers.CharField(source="ad.brand__name_fa", read_only=True)

    class Meta:
        model = DealScoreCache
        fields = (
            "code", "score", "discount_pct", "peer_median",
            "price", "year", "mileage", "url", "title",
            "model_name", "brand_name", "components", "calculated_at",
        )
        read_only_fields = fields
