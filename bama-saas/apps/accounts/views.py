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

import logging

from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.sessions.models import Session
from django.db import IntegrityError
from django.db.models import Subquery
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.digest import DIGEST_CAP, _digest_row, _digest_trends
from apps.accounts.models import AlertDelivery, AlertRule, Favorite, User, Watchlist
from apps.accounts.serializers import (
    _LATEST_DROP,
    AlertDeliverySerializer,
    AlertRuleSerializer,
    FavoriteSerializer,
    LoginSerializer,
    PasswordChangeSerializer,
    RegisterSerializer,
    WatchlistSerializer,
)
from apps.core.models import Brand

log = logging.getLogger("bama.accounts")


def _user_payload(user) -> dict:
    return {"email": user.email, "is_staff": user.is_staff}


@method_decorator(ensure_csrf_cookie, name="dispatch")
class MeView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        """Who is this? ``{"user": null}`` is a valid answer, not an error.

        This used to 401 for anonymous visitors, which is the wrong shape twice
        over. "Nobody is signed in" is the *expected* answer on the first load
        of a public page, not a failure — and the browser logs every 4xx as a
        console error, so the app printed one on every cold visit and
        Lighthouse's `errors-in-console` audit was right to fail it.

        The envelope keeps `id`/`email` at the top level too, so the existing
        client, which reads the payload directly, does not break on deploy.
        """
        if not request.user.is_authenticated:
            return Response({"user": None, "authenticated": False})
        payload = _user_payload(request.user)
        return Response({**payload, "user": payload, "authenticated": True})


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


def _blacklist_tokens(user) -> int | None:
    """Blacklist outstanding JWT refresh tokens. ``None`` means the table failed.

    The blacklist app is optional at import time and must not be the reason a
    user cannot change a password or end sessions — but a failure used to be
    ``pass``, so the caller was told the tokens were dead while they stayed
    valid until expiry. Logged, and the caller sees ``null``.
    """
    try:
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken,
        )

        revoked = 0
        for token in OutstandingToken.objects.filter(user=user):
            _, created = BlacklistedToken.objects.get_or_create(token=token)
            revoked += int(created)
        return revoked
    except Exception:  # noqa: BLE001 — reported, not swallowed
        log.exception("token revocation failed for user %s", user.pk)
        return None


class LogoutEverywhereView(APIView):
    """Drop every session this user holds, on every device.

    Django keys sessions by an opaque id with no user column, so the only way to
    find them is to decode each unexpired one. That is affordable here precisely
    because this is a small single-operator deployment.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        uid = str(request.user.pk)
        killed = 0
        for row in Session.objects.filter(expire_date__gte=timezone.now()).iterator():
            if row.get_decoded().get("_auth_user_id") == uid:
                row.delete()
                killed += 1
        tokens_revoked = _blacklist_tokens(request.user)
        logout(request)
        return Response({"sessions_ended": killed, "tokens_revoked": tokens_revoked})


class PasswordChangeView(APIView):
    """Change the signed-in user's password.

    Keeps *this* session (``update_session_auth_hash``). Other browser sessions
    die on the next request because Django stores a password-derived hash in
    the session. JWT refresh tokens are blacklisted here because that hash does
    not apply to them. There is no email reset: this host has no mail backend.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password"

    def post(self, request):
        serializer = PasswordChangeSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(serializer.validated_data["current_password"]):
            return Response(
                {"current_password": ["Current password is incorrect."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        update_session_auth_hash(request, user)
        tokens_revoked = _blacklist_tokens(user)
        return Response({"ok": True, "tokens_revoked": tokens_revoked})


class FavoriteViewSet(viewsets.ModelViewSet):
    """Saved ads. POST {code}; idempotent for the current user."""

    permission_classes = [IsAuthenticated]
    serializer_class = FavoriteSerializer
    lookup_field = "ad__code"
    lookup_url_kwarg = "code"
    http_method_names = ["get", "post", "delete", "head", "options"]
    queryset = (
        Favorite.objects.select_related("ad")
        .annotate(
            previous_price=Subquery(_LATEST_DROP.values("old_price")[:1]),
            price_changed_at=Subquery(_LATEST_DROP.values("observed_at")[:1]),
        )
        .order_by("-created_at")
    )

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        favorite, _ = Favorite.objects.get_or_create(
            user=request.user, ad_id=serializer.validated_data["ad_id"]
        )
        # Re-read through the annotated queryset: the serializer reads the two
        # drop columns as attributes, and a freshly `get_or_create`d instance
        # carries neither. One code path for both, rather than a serializer that
        # has to cope with an un-annotated row.
        favorite = self.get_queryset().get(pk=favorite.pk)
        return Response(self.get_serializer(favorite).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        self.get_object().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Watchlists and alerts
# ---------------------------------------------------------------------------
#
# Every viewset here is scoped to `request.user` in `get_queryset` and assigns
# the owner in `perform_create`. Neither is optional: without the first, an id
# in the URL reads somebody else's row, and without the second a client can post
# a `user` field and write into another account. The favourites viewset above is
# the pattern; these follow it exactly rather than inventing a second one.



class _OwnedViewSet(viewsets.ModelViewSet):
    """Rows belonging to the signed-in user, and only those."""

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class WatchlistViewSet(_OwnedViewSet):
    """Cars this user is following. POST a scope; unique per user."""

    serializer_class = WatchlistSerializer
    http_method_names = ["get", "post", "delete", "head", "options"]
    queryset = Watchlist.objects.select_related("model", "variant", "model__brand")

    def create(self, request, *args, **kwargs):
        """Idempotent, like favourites.

        The unique constraint is on the *derived* `scope_key`, so a duplicate
        cannot be caught by looking at the posted fields — the same car can
        arrive as `{model: 42}` twice and as `{model: 42, variant: null}` once.
        Build the instance, let it derive its key, then look for that.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        candidate = Watchlist(user=request.user, **serializer.validated_data)
        existing = Watchlist.objects.filter(
            user=request.user, scope_key=candidate.build_scope_key()
        ).first()
        if existing is not None:
            return Response(self.get_serializer(existing).data, status=status.HTTP_200_OK)
        candidate.save()
        return Response(self.get_serializer(candidate).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def digest(self, request):
        """Followed scopes with their current trend, one round trip.

        The list endpoint is paginated identity. This is the Saved-screen
        answer: "what happened to the cars I follow."
        """
        qs = self.get_queryset()
        total = qs.count()
        rows = list(qs[:DIGEST_CAP])
        slugs = {watch.brand_slug for watch in rows if watch.brand_slug}
        brand_names = (
            dict(Brand.objects.filter(slug__in=slugs).values_list("slug", "name_fa"))
            if slugs else {}
        )
        trends = _digest_trends(rows)
        return Response({
            "count": total,
            "truncated": total > DIGEST_CAP,
            "results": [_digest_row(w, trends[w.id], brand_names) for w in rows],
        })


class AlertRuleViewSet(_OwnedViewSet):
    """What this user wants to be told about."""

    serializer_class = AlertRuleSerializer
    queryset = AlertRule.objects.select_related("model", "variant", "model__brand")


class AlertViewSet(viewsets.ReadOnlyModelViewSet):
    """The user's alert feed, plus one action to mark it read.

    Read-only apart from that: alerts are written by the worker
    (`jobs.alerts`), never by a client, so there is no create or update here to
    get the ownership check wrong on.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = AlertDeliverySerializer
    queryset = AlertDelivery.objects.select_related("ad", "ad__city", "rule")

    def get_queryset(self):
        qs = super().get_queryset().filter(user=self.request.user)
        if self.request.query_params.get("unread") == "true":
            qs = qs.filter(read_at__isnull=True)
        return qs

    @action(detail=False, methods=["post"], url_path="mark-read")
    def mark_read(self, request):
        """Mark the whole feed read, or the codes given.

        `update()` rather than a loop: this fires on opening the feed, and a
        save per row would make the cost of reading proportional to how long the
        user has been away.
        """
        qs = self.get_queryset().filter(read_at__isnull=True)
        codes = request.data.get("codes")
        if codes:
            qs = qs.filter(ad_id__in=codes)
        return Response({"marked": qs.update(read_at=timezone.now())})

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        """Just the number, for the header badge.

        Its own route so the badge does not have to fetch and discard a page of
        alerts on every screen the user visits.
        """
        return Response({
            "unread": AlertDelivery.objects.filter(
                user=request.user, read_at__isnull=True
            ).count()
        })
