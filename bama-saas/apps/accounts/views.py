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

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.db import IntegrityError
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import AlertDelivery, AlertRule, Favorite, User, Watchlist
from apps.core import images
from apps.core.models import PriceDropEvent
from apps.core.pricing import MIN_PEERS
from apps.jobs.parsing import absolute_ad_url


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
    """One saved ad, plus its most recent price cut.

    ``previous_price`` and ``price_changed_at`` are read off annotations the
    viewset attaches (see ``_LATEST_DROP``), not looked up per row. They used to
    be two ``SerializerMethodField``s that each ran their own query for the same
    drop, so rendering a page of saved cars cost two queries per row on top of
    the one that fetched them.
    """

    code = serializers.CharField(source="ad_id")
    ad_title = serializers.CharField(source="ad.title", read_only=True)
    ad_price = serializers.IntegerField(source="ad.current_price", read_only=True)
    previous_price = serializers.IntegerField(read_only=True)
    price_changed_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Favorite
        fields = ["code", "ad_title", "ad_price", "previous_price",
                  "price_changed_at", "created_at"]
        read_only_fields = ["created_at"]


# The ad's newest price cut, as a correlated subquery. Served by
# `PriceDropEvent`'s own (ad, -observed_at) index, so it is one index seek per
# row inside the single list query rather than a round trip per row.
_LATEST_DROP = PriceDropEvent.objects.filter(ad_id=OuterRef("ad_id")).order_by("-observed_at")


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


class ScopeSerializerMixin(serializers.Serializer):
    """The four scope fields, plus the labels a client needs to render them.

    `scope_key` is read-only and derived in `Model.save()`. Exposing it is
    deliberate: the frontend uses it to tell whether the scope currently on
    screen is already being watched, and re-deriving that comparison in
    TypeScript is how the two definitions drift.
    """

    model_name = serializers.CharField(source="model.name_fa", read_only=True, default="")
    variant_name = serializers.CharField(source="variant.name_fa", read_only=True,
                                         default="")
    brand_name = serializers.CharField(source="model.brand.name_fa", read_only=True,
                                       default="")
    scope_key = serializers.CharField(read_only=True)


class WatchlistSerializer(ScopeSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Watchlist
        fields = ["id", "brand_slug", "model", "variant", "year_jalali",
                  "scope_key", "model_name", "variant_name", "brand_name",
                  "created_at"]
        read_only_fields = ["id", "created_at"]


class AlertRuleSerializer(ScopeSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = AlertRule
        fields = ["id", "name", "enabled", "brand_slug", "model", "variant",
                  "year_jalali", "scope_key", "model_name", "variant_name",
                  "brand_name", "min_discount_pct", "min_peers", "price_min",
                  "price_max", "mileage_max", "exclude_review",
                  "telegram_chat_id", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_min_discount_pct(self, value):
        # 100% would be a free car; 0 would deliver every listing on the site.
        if not 0 < value < 100:
            raise serializers.ValidationError("must be between 0 and 100")
        return value

    def validate_min_peers(self, value):
        # The same floor the operator singleton enforces, and for the same
        # reason: below `MIN_PEERS` the median this is measured against is not
        # one the app will quote, let alone interrupt somebody with.
        if value < MIN_PEERS:
            raise serializers.ValidationError(
                f"must be at least {MIN_PEERS} — the fair-price engine's peer minimum"
            )
        return value

    def validate(self, attrs):
        lo = attrs.get("price_min", getattr(self.instance, "price_min", None))
        hi = attrs.get("price_max", getattr(self.instance, "price_max", None))
        if lo is not None and hi is not None and lo > hi:
            raise serializers.ValidationError({"price_min": "must not exceed price_max"})
        return attrs


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


class AlertRuleViewSet(_OwnedViewSet):
    """What this user wants to be told about."""

    serializer_class = AlertRuleSerializer
    queryset = AlertRule.objects.select_related("model", "variant", "model__brand")


class AlertDeliverySerializer(serializers.ModelSerializer):
    """One alert, carrying what was true when it fired.

    `discount_pct` and `peer_median` are the stored copies, not a join to
    `DealScoreCache`: that table is dropped and rebuilt on a schedule, so a feed
    that joined to it would blank out an alert the moment the listing stopped
    qualifying — which is the one moment the reader most needs to see what it
    said.
    """

    code = serializers.CharField(source="ad_id", read_only=True)
    title = serializers.CharField(source="ad.title", read_only=True)
    price = serializers.IntegerField(source="ad.current_price", read_only=True)
    year = serializers.IntegerField(source="ad.year_jalali", read_only=True)
    mileage = serializers.IntegerField(source="ad.mileage", read_only=True)
    city_name = serializers.CharField(source="ad.city.name_fa", read_only=True,
                                      default="")
    status = serializers.CharField(source="ad.status", read_only=True)
    image_url = serializers.SerializerMethodField()
    bama_url = serializers.SerializerMethodField()
    rule_name = serializers.CharField(source="rule.name", read_only=True, default="")

    class Meta:
        model = AlertDelivery
        fields = ["id", "code", "title", "price", "year", "mileage", "city_name",
                  "status", "image_url", "bama_url", "discount_pct",
                  "peer_median", "rule_name", "created_at", "read_at"]
        read_only_fields = fields

    def get_image_url(self, obj) -> str:
        return images.ad_image_paths(obj.ad)[0]

    def get_bama_url(self, obj) -> str:
        return absolute_ad_url(obj.ad.url or obj.ad.canonical_path)


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
