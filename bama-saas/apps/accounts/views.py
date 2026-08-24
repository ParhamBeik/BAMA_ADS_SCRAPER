"""Session auth for the SPA, plus the saved ads owned by the logged-in user.

There is no token to store: the browser holds the Django sessionid cookie.
``MeView`` doubles as the CSRF bootstrap — the SPA calls it once on load, which
sets the csrftoken cookie regardless of auth outcome, then reads the body to
decide between the app shell and the login screen.
"""

from __future__ import annotations

from datetime import datetime

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import serializers, status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import Favorite, User
from apps.core.models import PriceDropEvent


def _user_payload(user) -> dict:
    return {"email": user.email, "is_staff": user.is_staff, "is_demo": user.is_demo}


@method_decorator(ensure_csrf_cookie, name="dispatch")
class MeView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        if not request.user.is_authenticated:
            return Response({"detail": "Not authenticated."},
                            status=status.HTTP_401_UNAUTHORIZED)
        return Response(_user_payload(request.user))


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False, write_only=True)

    def validate_email(self, value):
        email = value.strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return email

    def validate_password(self, value):
        validate_password(value)
        return value


class LoginView(APIView):
    """Throttled independently of the general API limits (THROTTLE_RATES["login"]):
    this is the one endpoint an attacker can hit without already holding a session."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(request, username=serializer.validated_data["email"],
                            password=serializer.validated_data["password"])
        if user is None or not user.is_active:
            return Response({"detail": "Invalid email or password."},
                            status=status.HTTP_401_UNAUTHORIZED)
        login(request, user)
        return Response(_user_payload(user))


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    email=serializer.validated_data["email"],
                    password=serializer.validated_data["password"],
                )
        except IntegrityError as exc:
            raise serializers.ValidationError(
                {"email": "An account with this email already exists."}
            ) from exc
        login(request, user)
        return Response(_user_payload(user), status=status.HTTP_201_CREATED)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class FavoriteSerializer(serializers.ModelSerializer):
    code = serializers.CharField(source="ad_id")
    ad_title = serializers.CharField(source="ad.title", read_only=True)
    ad_price = serializers.IntegerField(source="ad.current_price", read_only=True)
    previous_price = serializers.SerializerMethodField()
    price_changed_at = serializers.SerializerMethodField()

    class Meta:
        model = Favorite
        fields = ["code", "ad_title", "ad_price", "previous_price",
                  "price_changed_at", "created_at"]
        read_only_fields = ["created_at"]

    def _latest_drop(self, obj):
        return (
            PriceDropEvent.objects.filter(ad_id=obj.ad_id)
            .order_by("-observed_at").values("old_price", "observed_at").first()
        )

    def get_previous_price(self, obj) -> int | None:
        drop = self._latest_drop(obj)
        return drop["old_price"] if drop else None

    def get_price_changed_at(self, obj) -> datetime | None:
        drop = self._latest_drop(obj)
        return drop["observed_at"] if drop else None


class FavoriteViewSet(viewsets.ModelViewSet):
    """Saved ads. POST {code}; idempotent for the current user."""

    permission_classes = [IsAuthenticated]
    serializer_class = FavoriteSerializer
    lookup_field = "ad__code"
    lookup_url_kwarg = "code"
    http_method_names = ["get", "post", "delete", "head", "options"]
    queryset = Favorite.objects.select_related("ad").order_by("-created_at")

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        favorite, _ = Favorite.objects.get_or_create(
            user=request.user, ad_id=serializer.validated_data["ad_id"]
        )
        return Response(self.get_serializer(favorite).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        self.get_object().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
