"""Engagement views: favorites, alerts, and a read-only notification inbox.

Auth register/me live in ``auth_views``. Write viewsets carry
``SubscriptionThrottle`` + ``MonthlyQuotaThrottle``.
"""

from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Alert, Favorite, Notification
from .serializers import AlertSerializer, FavoriteSerializer, NotificationSerializer
from .throttles import MonthlyQuotaThrottle, SubscriptionThrottle
from .entitlements import plan_limits, require_verified
from rest_framework.exceptions import PermissionDenied

_PREMIUM_THROTTLES = [SubscriptionThrottle, MonthlyQuotaThrottle]


class FavoriteViewSet(viewsets.ModelViewSet):
    """Favorites. POST {code}; idempotent if already favorited."""

    serializer_class = FavoriteSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = _PREMIUM_THROTTLES
    lookup_field = "ad__code"
    lookup_url_kwarg = "code"
    http_method_names = ["get", "post", "delete", "head", "options"]
    queryset = Favorite.objects.none()

    def get_queryset(self):
        return Favorite.objects.filter(user=self.request.user).select_related("ad")

    def perform_create(self, serializer):
        require_verified(self.request.user)
        limits = plan_limits(self.request.user)
        if Favorite.objects.filter(user=self.request.user).count() >= limits.favorites:
            raise PermissionDenied(f"Favorite limit reached ({limits.favorites}).")
        serializer.save(user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        favorite = self.get_object()
        favorite.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AlertViewSet(viewsets.ModelViewSet):
    """Alerts. Shape (ad/model) validated in the serializer."""

    serializer_class = AlertSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = _PREMIUM_THROTTLES
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    queryset = Alert.objects.none()

    def get_queryset(self):
        return Alert.objects.filter(user=self.request.user).select_related("ad", "model")

    def perform_create(self, serializer):
        require_verified(self.request.user)
        limits = plan_limits(self.request.user)
        if Alert.objects.filter(user=self.request.user, enabled=True).count() >= limits.alerts:
            raise PermissionDenied(f"Alert limit reached ({limits.alerts}).")
        serializer.save(user=self.request.user)


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only in-app inbox (paged, newest first)."""

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    queryset = Notification.objects.none()

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)
