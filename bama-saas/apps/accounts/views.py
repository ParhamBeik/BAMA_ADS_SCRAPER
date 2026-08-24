"""Auth for the SPA and for API clients, plus the saved ads owned by a user.

The browser holds an HttpOnly Django session cookie and stores no token: script
cannot read the cookie, so an XSS bug cannot walk away with the login. JWT lives
alongside it (``/api/auth/token/``) for non-browser clients, which have nowhere
to keep a cookie — the SPA never touches those endpoints.

``MeView`` doubles as the CSRF bootstrap — the SPA calls it once on load, which
sets the csrftoken cookie regardless of auth outcome, then reads the body to
decide between the app shell and the login screen.
"""

from __future__ import annotations

from datetime import datetime

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.db import IntegrityError
from django.utils import timezone
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
    return {"email": user.email, "is_staff": user.is_staff}


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
    """Open signup. Every new account is a regular user.

    Staff is granted only by an existing admin (or ``createsuperuser`` inside
    the container). The first-signup bootstrap was a one-shot window; it closed
    once the operator account existed.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
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


class EmailAvailableView(APIView):
    """Is this address free? Used by the signup form so the answer arrives
    before the user has typed a password and pressed submit.

    Throttled on the register scope: it is an unauthenticated read of "does this
    account exist", and left open it would enumerate every user.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    def get(self, request):
        email = (request.query_params.get("email") or "").strip().lower()
        if not email:
            return Response({"detail": "email is required."},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({"available": not User.objects.filter(email__iexact=email).exists()})


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class LogoutEverywhereView(APIView):
    """Drop every session this user holds, on every device.

    Django keys sessions by an opaque id with no user column, so the only way to
    find them is to decode each unexpired one. That is affordable here precisely
    because this is a small single-operator deployment, and the alternative —
    "change your password and hope" — is not a revocation.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        uid = str(request.user.pk)
        killed = 0
        for row in Session.objects.filter(expire_date__gte=timezone.now()).iterator():
            if row.get_decoded().get("_auth_user_id") == uid:
                row.delete()
                killed += 1
        logout(request)
        return Response({"sessions_ended": killed})


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
