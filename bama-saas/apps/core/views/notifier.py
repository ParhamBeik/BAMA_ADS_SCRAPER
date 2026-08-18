"""Read and edit the deal-notifier rules.

One singleton row, edited from the deal board. There is deliberately no
per-user rule model, no schedule and no channel abstraction: the previous
generation of this app carried Alert, Notification, Subscription, throttles and
a digest scheduler to serve four users, one saved favourite and one alert.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.core.models import NotifierSettings
from apps.core.services.fair_price import MIN_PEERS


class NotifierSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotifierSettings
        fields = (
            "enabled",
            "min_discount_pct",
            "min_peers",
            "price_min",
            "price_max",
            "model_ids",
            "telegram_chat_id",
            "updated_at",
        )
        read_only_fields = ("updated_at",)

    def validate_min_discount_pct(self, value):
        # A "discount" outside this range is not a threshold anyone means. 100%
        # would be a free car; 0 would page you for every listing on the site.
        if not 0 < value < 100:
            raise serializers.ValidationError("must be between 0 and 100")
        return value

    def validate_min_peers(self, value):
        # Below the fair-price engine's own floor the baseline being compared
        # against is not one this app is willing to quote, so a notification
        # built on it would assert more confidence than the number carries.
        if value < MIN_PEERS:
            raise serializers.ValidationError(
                f"must be at least {MIN_PEERS} — the fair-price engine's peer minimum"
            )
        return value

    def validate_model_ids(self, value):
        if not isinstance(value, list) or any(not isinstance(v, int) for v in value):
            raise serializers.ValidationError("must be a list of model ids")
        return value

    def validate(self, attrs):
        lo = attrs.get("price_min", getattr(self.instance, "price_min", None))
        hi = attrs.get("price_max", getattr(self.instance, "price_max", None))
        if lo is not None and hi is not None and lo > hi:
            raise serializers.ValidationError(
                {"price_min": "must not exceed price_max"}
            )
        return attrs


@extend_schema(
    tags=["Notifier"],
    request=NotifierSettingsSerializer,
    responses={200: NotifierSettingsSerializer},
    description="Get or update the deal-notifier thresholds and scope.",
)
@api_view(["GET", "PATCH"])
def notifier_settings(request):
    cfg = NotifierSettings.load()
    if request.method == "GET":
        return Response(NotifierSettingsSerializer(cfg).data)

    serializer = NotifierSettingsSerializer(cfg, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    serializer.save()
    return Response(serializer.data)
