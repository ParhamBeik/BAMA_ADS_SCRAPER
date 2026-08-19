"""Saved ads.

One operator, one saved list, no accounts. ``POST {"code": "..."}`` saves an ad,
``DELETE /api/favorites/<code>/`` unsaves it, and the list carries each ad's most
recent price drop so the saved screen can answer the only question it is really
asked: did anything I am watching get cheaper.
"""

from datetime import datetime

from rest_framework import serializers, status, viewsets
from rest_framework.response import Response

from apps.core.models import PriceDropEvent

from .models import Favorite


class FavoriteSerializer(serializers.ModelSerializer):
    code = serializers.CharField(source="ad_id")
    ad_title = serializers.CharField(source="ad.title", read_only=True)
    ad_price = serializers.IntegerField(source="ad.current_price", read_only=True)
    previous_price = serializers.SerializerMethodField()
    price_changed_at = serializers.SerializerMethodField()

    class Meta:
        model = Favorite
        fields = [
            "code", "ad_title", "ad_price",
            "previous_price", "price_changed_at", "created_at",
        ]
        read_only_fields = ["created_at"]

    def _latest_drop(self, obj):
        return (
            PriceDropEvent.objects.filter(ad_id=obj.ad_id)
            .order_by("-observed_at")
            .values("old_price", "observed_at")
            .first()
        )

    def get_previous_price(self, obj) -> int | None:
        drop = self._latest_drop(obj)
        return drop["old_price"] if drop else None

    def get_price_changed_at(self, obj) -> datetime | None:
        drop = self._latest_drop(obj)
        return drop["observed_at"] if drop else None


class FavoriteViewSet(viewsets.ModelViewSet):
    """Saved ads. POST {code}; idempotent if already saved."""

    serializer_class = FavoriteSerializer
    lookup_field = "ad__code"
    lookup_url_kwarg = "code"
    http_method_names = ["get", "post", "delete", "head", "options"]
    queryset = Favorite.objects.select_related("ad")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        favorite, _ = Favorite.objects.get_or_create(
            ad_id=serializer.validated_data["ad_id"]
        )
        return Response(
            self.get_serializer(favorite).data, status=status.HTTP_201_CREATED
        )

    def destroy(self, request, *args, **kwargs):
        self.get_object().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
