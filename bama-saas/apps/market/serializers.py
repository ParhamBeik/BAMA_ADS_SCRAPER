"""Serializers for the market/price views."""

from rest_framework import serializers

from apps.market.models import PriceObservation


class PriceObservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceObservation
        fields = ["observed_at", "price", "payment", "prepayment", "installments",
                  "price_type", "fingerprint"]
