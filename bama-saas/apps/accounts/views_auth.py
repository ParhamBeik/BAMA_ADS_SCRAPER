"""Session login for the SPA.

The API has no registration and no password reset — there are exactly two
accounts, seeded by ``ensure_seed_users`` from env vars. This module is only
the three moves a browser session needs: get a CSRF cookie, exchange
credentials for a session, and ask "am I logged in".
"""

from __future__ import annotations

from django.contrib.auth import authenticate, login, logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView


def _user_payload(user) -> dict:
    return {
        "email": user.email,
        "is_staff": user.is_staff,
        "is_demo": user.is_demo,
    }


@method_decorator(ensure_csrf_cookie, name="dispatch")
class MeView(APIView):
    """Also the CSRF bootstrap: the SPA calls this once on load, which sets
    the csrftoken cookie regardless of auth outcome, then reads the body to
    decide whether to render the app shell or the login screen."""

    permission_classes = [AllowAny]

    def get(self, request):
        if not request.user.is_authenticated:
            return Response({"detail": "Not authenticated."}, status=status.HTTP_401_UNAUTHORIZED)
        return Response(_user_payload(request.user))


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)


class LoginView(APIView):
    """POST {email, password}. Throttled independently of the general API
    rate limits (see THROTTLE_RATES["login"] in settings) — this is the one
    endpoint an attacker can hit without already holding a session."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request,
            username=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )
        if user is None or not user.is_active:
            return Response(
                {"detail": "Invalid email or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        login(request, user)
        return Response(_user_payload(user))


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)
