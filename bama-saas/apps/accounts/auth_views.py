"""Cookie-based auth, verification, recovery, and account lifecycle."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .auth_cookies import clear_auth_cookies, set_auth_cookies
from .entitlements import effective_plan, plan_limits
from .models import Favorite, ProAccessRequest, Subscription
from .serializers import (
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    ProfileSerializer,
    RegisterSerializer,
    SubscriptionSerializer,
    UserSerializer,
)
from .services.email_auth import (
    check_password_reset_token,
    make_verification_token,
    read_verification_token,
    send_deletion_recovery_email,
    send_password_reset_email,
    send_verification_email,
)

User = get_user_model()
DELETE_SALT = "bama-account-delete"


def _token_pair_for(user) -> tuple[str, str]:
    refresh = RefreshToken.for_user(user)
    refresh["sv"] = user.session_version
    return str(refresh.access_token), str(refresh)


def _me_payload(user) -> dict:
    sub = user.subscriptions.order_by("-started_at").first()
    limits = plan_limits(user)
    return {
        "user": UserSerializer(user).data,
        "subscription": SubscriptionSerializer(sub).data if sub else None,
        "plan": effective_plan(user),
        "limits": {
            "favorites": limits.favorites,
            "watchlists": limits.watchlists,
            "saved_searches": limits.saved_searches,
            "alerts": limits.alerts,
            "valuations_per_day": limits.valuations_per_day,
            "model_comparison": limits.model_comparison,
            "csv_export": limits.csv_export,
            "full_research": limits.full_research,
        },
        "verified": bool(user.email_verified_at),
    }


class CookieLoginView(TokenObtainPairView):
    """POST /api/auth/login/ — JWT in HTTP-only cookies (+ JSON body for legacy)."""

    @extend_schema(tags=["auth"])
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code != 200:
            return response
        access = response.data.get("access")
        refresh = response.data.get("refresh")
        if access and refresh:
            set_auth_cookies(response, access, refresh)
        email = request.data.get("email")
        if email:
            try:
                user = User.objects.get(email=User.objects.normalize_email(email))
                response.data = {**response.data, **_me_payload(user)}
            except User.DoesNotExist:
                pass
        return response


class CookieRegisterView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(tags=["auth"], request=RegisterSerializer)
    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        send_verification_email(user)
        access, refresh = _token_pair_for(user)
        resp = Response(_me_payload(user), status=status.HTTP_201_CREATED)
        set_auth_cookies(resp, access, refresh)
        return resp


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["auth"], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return Response(_me_payload(request.user))

    @extend_schema(tags=["auth"], request=ProfileSerializer)
    def patch(self, request):
        ser = ProfileSerializer(request.user, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(_me_payload(request.user))


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["auth"])
    def post(self, request):
        raw = request.COOKIES.get(settings.AUTH_REFRESH_COOKIE) or request.data.get("refresh")
        if raw:
            try:
                RefreshToken(raw).blacklist()
            except TokenError:
                pass
        resp = Response({"detail": "Logged out."})
        clear_auth_cookies(resp)
        return resp


class LogoutAllView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["auth"])
    def post(self, request):
        user = request.user
        user.session_version = (user.session_version or 0) + 1
        user.save(update_fields=["session_version"])
        resp = Response({"detail": "All sessions invalidated."})
        clear_auth_cookies(resp)
        return resp


class CookieRefreshView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(tags=["auth"])
    def post(self, request):
        raw = request.COOKIES.get(settings.AUTH_REFRESH_COOKIE) or request.data.get("refresh")
        if not raw:
            return Response({"detail": "No refresh token."}, status=401)
        try:
            token = RefreshToken(raw)
            user = User.objects.get(pk=token["user_id"])
            if token.get("sv", 0) != user.session_version:
                raise TokenError("Session revoked")
            if settings.SIMPLE_JWT.get("ROTATE_REFRESH_TOKENS"):
                token.blacklist()
                access, refresh = _token_pair_for(user)
            else:
                access, refresh = str(token.access_token), str(token)
        except (TokenError, User.DoesNotExist):
            resp = Response({"detail": "Invalid refresh token."}, status=401)
            clear_auth_cookies(resp)
            return resp
        resp = Response({"access": access, "refresh": refresh})
        set_auth_cookies(resp, access, refresh)
        return resp


class VerifyEmailView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(tags=["auth"])
    def post(self, request):
        token = request.data.get("token", "")
        uid = read_verification_token(token)
        if not uid:
            return Response({"detail": "Invalid or expired token."}, status=400)
        try:
            user = User.objects.get(pk=uid)
        except User.DoesNotExist:
            return Response({"detail": "User not found."}, status=404)
        if not user.email_verified_at:
            user.email_verified_at = timezone.now()
            user.save(update_fields=["email_verified_at"])
        return Response({"detail": "Email verified.", "verified": True})


class ResendVerificationView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["auth"])
    def post(self, request):
        user = request.user
        if user.email_verified_at:
            return Response({"detail": "Already verified."})
        send_verification_email(user)
        return Response({"detail": "Verification email sent."})


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(tags=["auth"], request=PasswordResetRequestSerializer)
    def post(self, request):
        ser = PasswordResetRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        email = User.objects.normalize_email(ser.validated_data["email"])
        try:
            user = User.objects.get(email=email)
            send_password_reset_email(user)
        except User.DoesNotExist:
            pass
        return Response({"detail": "If that email exists, a reset link was sent."})


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(tags=["auth"], request=PasswordResetConfirmSerializer)
    def post(self, request):
        ser = PasswordResetConfirmSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            uid = force_str(urlsafe_base64_decode(ser.validated_data["uid"]))
            user = User.objects.get(pk=uid)
        except Exception:
            return Response({"detail": "Invalid reset link."}, status=400)
        if not check_password_reset_token(user, ser.validated_data["token"]):
            return Response({"detail": "Invalid or expired token."}, status=400)
        user.set_password(ser.validated_data["password"])
        user.session_version = (user.session_version or 0) + 1
        user.save(update_fields=["password", "session_version"])
        return Response({"detail": "Password updated."})


class DeleteAccountView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["auth"])
    def post(self, request):
        user = request.user
        user.is_active = False
        user.deletion_requested_at = timezone.now()
        user.session_version = (user.session_version or 0) + 1
        user.save(update_fields=["is_active", "deletion_requested_at", "session_version"])
        token = signing.dumps({"uid": str(user.pk)}, salt=DELETE_SALT)
        send_deletion_recovery_email(user, token)
        resp = Response({"detail": "Account deactivated. You have 30 days to restore."})
        clear_auth_cookies(resp)
        return resp


class RestoreAccountView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(tags=["auth"])
    def post(self, request):
        token = request.data.get("token", "")
        try:
            payload = signing.loads(token, salt=DELETE_SALT, max_age=60 * 60 * 24 * 30)
            user = User.objects.get(pk=payload["uid"])
        except (signing.BadSignature, User.DoesNotExist, KeyError):
            return Response({"detail": "Invalid or expired recovery link."}, status=400)
        user.is_active = True
        user.deletion_requested_at = None
        user.save(update_fields=["is_active", "deletion_requested_at"])
        access, refresh = _token_pair_for(user)
        resp = Response(_me_payload(user))
        set_auth_cookies(resp, access, refresh)
        return resp


class AccountUsageView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["auth"])
    def get(self, request):
        user = request.user
        limits = plan_limits(user)
        return Response({
            "favorites": {"used": user.favorites.count(), "limit": limits.favorites},
            "watchlists": {"used": user.watchlists.count(), "limit": limits.watchlists},
            "saved_searches": {"used": user.saved_searches.count(), "limit": limits.saved_searches},
            "alerts": {"used": user.alerts.filter(enabled=True).count(), "limit": limits.alerts},
            "plan": effective_plan(user),
            "verified": bool(user.email_verified_at),
        })


class ProRequestView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["auth"])
    def get(self, request):
        qs = ProAccessRequest.objects.filter(user=request.user).order_by("-created_at")[:5]
        return Response([
            {
                "id": str(r.id),
                "status": r.status,
                "message": r.message,
                "created_at": r.created_at,
                "granted_expires_at": r.granted_expires_at,
            }
            for r in qs
        ])

    @extend_schema(tags=["auth"])
    def post(self, request):
        user = request.user
        if not user.email_verified_at:
            return Response({"detail": "Verify your email first."}, status=403)
        if ProAccessRequest.objects.filter(user=user, status=ProAccessRequest.Status.PENDING).exists():
            return Response({"detail": "You already have a pending request."}, status=400)
        if effective_plan(user) == Subscription.PlanType.PRO:
            return Response({"detail": "Already on Pro."}, status=400)
        req = ProAccessRequest.objects.create(
            user=user, message=(request.data.get("message") or "")[:2000]
        )
        return Response({"id": str(req.id), "status": req.status}, status=201)
