"""Request and response shapes for the accounts API.

Split out of ``views.py``, which held these alongside the views and the
watchlist digest and was the only app in this project without a
``serializers.py`` — ``apps.core`` has had one all along.

The auth serializers are moved verbatim. Validation on a login, a registration
or a password change is security logic, and a refactor is not the place to
change what it accepts.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db.models import OuterRef
from rest_framework import serializers

from apps.accounts.models import AlertDelivery, AlertRule, Favorite, Watchlist
from apps.core import images
from apps.core.models import PriceDropEvent
from apps.core.pricing import MIN_PEERS

User = get_user_model()


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

class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False)
    new_password = serializers.CharField(trim_whitespace=False)

    def validate_new_password(self, value):
        validate_password(value, user=self.context["request"].user)
        return value

    def validate(self, attrs):
        if attrs["current_password"] == attrs["new_password"]:
            raise serializers.ValidationError(
                {"new_password": "must be different from the current password"}
            )
        return attrs

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
                  "brand_name", "min_discount_pct", "min_residual_pct",
                  "min_peers", "price_min", "price_max", "mileage_max",
                  "exclude_review", "telegram_chat_id", "created_at"]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {"min_residual_pct": {"allow_null": True, "required": False}}

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

    def validate_min_residual_pct(self, value):
        if value is None:
            return value
        if not 0 < value < 100:
            raise serializers.ValidationError("must be between 0 and 100")
        return value

    def validate(self, attrs):
        lo = attrs.get("price_min", getattr(self.instance, "price_min", None))
        hi = attrs.get("price_max", getattr(self.instance, "price_max", None))
        if lo is not None and hi is not None and lo > hi:
            raise serializers.ValidationError({"price_min": "must not exceed price_max"})
        return attrs

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
                  "peer_median", "residual_pct", "rule_name", "created_at",
                  "read_at"]
        read_only_fields = fields

    def get_image_url(self, obj) -> str:
        return images.ad_image_paths(obj.ad)[0]

    def get_bama_url(self, obj) -> str:
        return obj.ad.bama_url
