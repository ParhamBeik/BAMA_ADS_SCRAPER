"""Serializers for registration and the current-user / subscription payloads."""

from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Subscription

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True,
                                     style={"input_type": "password"})

    class Meta:
        model = User
        fields = ("email", "password", "full_name")

    def validate_email(self, value):
        return User.objects.normalize_email(value)

    def create(self, validated_data):
        user = User(email=validated_data["email"], full_name=validated_data.get("full_name", ""))
        user.set_password(validated_data["password"])
        user.save()
        # Every new user starts on the free tier with an active subscription.
        Subscription.objects.create(user=user, plan_type=Subscription.PlanType.FREE)
        return user


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "full_name", "is_staff", "date_joined")
        read_only_fields = fields


class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subscription
        fields = ("id", "plan_type", "status", "monthly_api_limit",
                  "api_usage_count", "started_at", "expires_at")
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Phase 5 — engagement (favorites, watchlists, saved searches, alerts, inbox)
# ---------------------------------------------------------------------------

from apps.catalog.models import Ad  # noqa: E402
from .models import Alert, Favorite, Notification, SavedSearch, Watchlist  # noqa: E402


class FavoriteSerializer(serializers.ModelSerializer):
    """A favorited ad. ``code`` is the ad's PK (catalog.Ad.code)."""

    code = serializers.SlugRelatedField(
        slug_field="code", source="ad", queryset=Ad.objects.all()
    )

    class Meta:
        model = Favorite
        fields = ("id", "code", "created_at")
        read_only_fields = ("id", "created_at")

    def create(self, validated_data):
        user = self.context["request"].user
        ad = validated_data["ad"]
        favorite, _ = Favorite.objects.get_or_create(user=user, ad=ad)
        return favorite


class WatchlistSerializer(serializers.ModelSerializer):
    # ``ads`` is rendered as a list of codes; membership is mutated via the
    # /watchlists/<id>/ads/ action rather than this serializer.
    ads = serializers.SlugRelatedField(
        slug_field="code", many=True, read_only=True
    )

    class Meta:
        model = Watchlist
        fields = ("id", "name", "created_at", "ads")
        read_only_fields = ("id", "created_at")


class WatchlistAdSerializer(serializers.Serializer):
    """Input for POST /watchlists/<id>/ads/ — just an ad code."""

    code = serializers.CharField(required=True)

    def validate_code(self, value):
        from apps.catalog.models import Ad
        try:
            Ad.objects.get(code=value)
        except Ad.DoesNotExist as exc:
            raise serializers.ValidationError(
                f"Ad with code '{value}' does not exist."
            ) from exc
        return value


class SavedSearchSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavedSearch
        fields = ("id", "name", "params", "notify", "last_checked_at", "created_at")
        read_only_fields = ("id", "last_checked_at", "created_at")


class AlertSerializer(serializers.ModelSerializer):
    """Create/update an alert. Shape is validated per alert_type."""

    class Meta:
        model = Alert
        fields = (
            "id", "alert_type", "saved_search", "ad", "watchlist", "model",
            "threshold", "channels", "enabled", "created_at",
        )
        read_only_fields = ("id", "created_at")

    def validate(self, attrs):
        # On PATCH only some fields are present; merge persisted values for
        # the type/shape check so toggling ``enabled`` doesn't re-require all
        # of the shape fields.
        if self.instance:
            data = {
                "alert_type": self.instance.alert_type,
                "ad": self.instance.ad,
                "watchlist": self.instance.watchlist,
                "model": self.instance.model,
                "saved_search": self.instance.saved_search,
            }
            data.update(attrs)
        else:
            data = attrs

        atype = data.get("alert_type")
        if atype == Alert.Type.PRICE_DROP and not (data.get("ad") or data.get("watchlist")):
            raise serializers.ValidationError(
                "price_drop alerts require an ad or a watchlist."
            )
        elif atype == Alert.Type.UNDERVALUED and not data.get("model"):
            raise serializers.ValidationError(
                "undervalued alerts require a model."
            )
        elif atype == Alert.Type.NEW_LISTING and not data.get("saved_search"):
            raise serializers.ValidationError(
                "new_listing alerts require a saved_search."
            )
        return attrs


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = (
            "id", "alert", "channel", "status", "subject", "body",
            "related_ad", "dedupe_key", "created_at", "sent_at", "error",
        )
        read_only_fields = fields
