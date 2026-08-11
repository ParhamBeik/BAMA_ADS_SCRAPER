"""Registration, current-user, and engagement (Phase 5) views.

Auth: RegisterView / MeView. Engagement: favorites, watchlists, saved
searches, alerts, and a read-only notification inbox — all owner-scoped and
JWT-authenticated. Write viewsets carry ``SubscriptionThrottle`` +
``MonthlyQuotaThrottle`` so the free tier is rate-limited and the monthly
quota is enforced.
"""

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import CreateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Alert,
    Favorite,
    Notification,
    SavedSearch,
    Subscription,
    Watchlist,
)
from .serializers import (
    AlertSerializer,
    FavoriteSerializer,
    NotificationSerializer,
    RegisterSerializer,
    SavedSearchSerializer,
    SubscriptionSerializer,
    UserSerializer,
    WatchlistAdSerializer,
    WatchlistSerializer,
)
from .throttles import MonthlyQuotaThrottle, SubscriptionThrottle
from .entitlements import plan_limits, require_verified
from rest_framework.exceptions import PermissionDenied

User = get_user_model()

# Throttle stack applied to every premium write viewset. SubscriptionThrottle
# bounds the per-minute burst by plan; MonthlyQuotaThrottle rejects with 429
# once monthly_api_limit is reached (None = unlimited).
_PREMIUM_THROTTLES = [SubscriptionThrottle, MonthlyQuotaThrottle]


class RegisterView(CreateAPIView):
    """POST /api/auth/register/ — create a user + free-tier subscription."""

    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]


class MeView(APIView):
    """GET /api/auth/me/ — current user + active subscription.

    Returns a composite ``{user, subscription}`` shape rather than a single
    model, so it overrides ``get`` directly and declares its response schema.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["auth"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request, *args, **kwargs):
        user = request.user
        sub = user.subscriptions.order_by("-started_at").first()
        return Response({
            "user": UserSerializer(user).data,
            "subscription": SubscriptionSerializer(sub).data if sub else None,
        })


# ---------------------------------------------------------------------------
# Engagement (Phase 5)
# ---------------------------------------------------------------------------

class FavoriteViewSet(viewsets.ModelViewSet):
    """Favorites. POST {code}; idempotent if already favorited.

    Routes (mounted under /api/auth/favorites/):
    - GET    /favorites/             list user's favorites
    - POST   /favorites/             {code} — add
    - DELETE /favorites/<str:code>/  — remove
    """

    serializer_class = FavoriteSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = _PREMIUM_THROTTLES
    lookup_field = "ad__code"
    lookup_url_kwarg = "code"
    http_method_names = ["get", "post", "delete", "head", "options"]
    # Class-level queryset is for schema introspection only; get_queryset()
    # below scopes to the authenticated user at runtime.
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
        # lookup_field is ad__code; get_object resolves via get_queryset.
        favorite = self.get_object()
        favorite.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WatchlistViewSet(viewsets.ModelViewSet):
    """Watchlists + nested ad membership.

    Routes (mounted under /api/auth/watchlists/):
    - GET/POST        /watchlists/
    - GET/PATCH/DELETE /watchlists/<uuid:pk>/
    - GET/POST        /watchlists/<uuid:pk>/ads/   (POST body {code})
    - DELETE          /watchlists/<uuid:pk>/ads/<str:code>/
    """

    serializer_class = WatchlistSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = _PREMIUM_THROTTLES
    queryset = Watchlist.objects.none()

    def get_queryset(self):
        return Watchlist.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        require_verified(self.request.user)
        limits = plan_limits(self.request.user)
        if Watchlist.objects.filter(user=self.request.user).count() >= limits.watchlists:
            raise PermissionDenied(f"Watchlist limit reached ({limits.watchlists}).")
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["get", "post"])
    def ads(self, request, pk=None):
        watchlist = self.get_object()
        if request.method == "GET":
            codes = list(watchlist.ads.values_list("code", flat=True))
            return Response({"ads": codes})

        ser = WatchlistAdSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        from apps.core.models import Ad
        ad = get_object_or_404(Ad, code=ser.validated_data["code"])
        watchlist.ads.add(ad)
        return Response({"code": ad.code, "added": True}, status=status.HTTP_201_CREATED)

    @extend_schema(
        parameters=[OpenApiParameter("code", str, OpenApiParameter.PATH)],
        responses={204: None},
    )
    @action(
        detail=True, methods=["delete"],
        url_path=r"ads/(?P<code>[^/.]+)", url_name="ads-detail",
    )
    def remove_ad(self, request, pk=None, code=None):
        watchlist = self.get_object()
        from apps.core.models import Ad
        ad = get_object_or_404(Ad, code=code)
        watchlist.ads.remove(ad)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SavedSearchViewSet(viewsets.ModelViewSet):
    """Saved searches. POST {name, params, notify}."""

    serializer_class = SavedSearchSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = _PREMIUM_THROTTLES
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    queryset = SavedSearch.objects.none()

    def get_queryset(self):
        return SavedSearch.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        require_verified(self.request.user)
        limits = plan_limits(self.request.user)
        if SavedSearch.objects.filter(user=self.request.user).count() >= limits.saved_searches:
            raise PermissionDenied(f"Saved search limit reached ({limits.saved_searches}).")
        serializer.save(user=self.request.user)


class AlertViewSet(viewsets.ModelViewSet):
    """Alerts. Shape (ad/watchlist/model/saved_search) validated in the serializer."""

    serializer_class = AlertSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = _PREMIUM_THROTTLES
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    queryset = Alert.objects.none()

    def get_queryset(self):
        return Alert.objects.filter(user=self.request.user)

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
