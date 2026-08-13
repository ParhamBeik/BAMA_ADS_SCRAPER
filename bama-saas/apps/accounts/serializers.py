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
        fields = (
            "id", "email", "full_name", "is_staff", "date_joined",
            "email_verified_at", "preferred_brands", "preferred_models",
            "onboarding_completed_at", "deletion_requested_at",
        )
        read_only_fields = fields


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("full_name", "preferred_brands", "preferred_models", "onboarding_completed_at", "telegram_chat_id")


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    password = serializers.CharField(min_length=8)


class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subscription
        fields = ("id", "plan_type", "status", "monthly_api_limit",
                  "api_usage_count", "started_at", "expires_at")
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Phase 5 — engagement (favorites, alerts, inbox)
# ---------------------------------------------------------------------------

from apps.core.models import Ad  # noqa: E402
from .models import Alert, Favorite, Notification  # noqa: E402


class FavoriteSerializer(serializers.ModelSerializer):
    """A favorited ad. ``code`` is the ad's PK (catalog.Ad.code)."""

    code = serializers.SlugRelatedField(
        slug_field="code", source="ad", queryset=Ad.objects.all()
    )
    ad_title = serializers.CharField(source="ad.title", read_only=True)
    ad_price = serializers.IntegerField(source="ad.current_price", read_only=True)

    class Meta:
        model = Favorite
        fields = ("id", "code", "ad_title", "ad_price", "created_at")
        read_only_fields = ("id", "ad_title", "ad_price", "created_at")

    def create(self, validated_data):
        user = self.context["request"].user
        ad = validated_data["ad"]
        favorite, _ = Favorite.objects.get_or_create(user=user, ad=ad)
        return favorite


class AlertSerializer(serializers.ModelSerializer):
    """Create/update an alert. Shape is validated per alert_type."""

    class Meta:
        model = Alert
        fields = (
            "id", "alert_type", "ad", "model",
            "threshold", "channels", "enabled", "delivery", "created_at",
        )
        read_only_fields = ("id", "created_at")

    def validate(self, attrs):
        if self.instance:
            data = {
                "alert_type": self.instance.alert_type,
                "ad": self.instance.ad,
                "model": self.instance.model,
            }
            data.update(attrs)
        else:
            data = attrs

        atype = data.get("alert_type")
        if atype == Alert.Type.PRICE_DROP and not data.get("ad"):
            raise serializers.ValidationError(
                "price_drop alerts require an ad."
            )
        elif atype == Alert.Type.UNDERVALUED and not data.get("model"):
            raise serializers.ValidationError(
                "undervalued alerts require a model."
            )

        if "channels" in attrs:
            allowed = {c.value for c in Notification.Channel}
            aliases = {"in_app": Notification.Channel.INAPP}
            normalized = []
            for raw in attrs["channels"] or []:
                ch = aliases.get(raw, raw)
                if ch not in allowed:
                    raise serializers.ValidationError(
                        {"channels": f"Invalid channel {raw!r}; expected one of {sorted(allowed)}."}
                    )
                if ch not in normalized:
                    normalized.append(ch)
            attrs["channels"] = normalized or [Notification.Channel.INAPP]
        return attrs


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = (
            "id", "alert", "channel", "status", "subject", "body",
            "related_ad", "dedupe_key", "created_at", "sent_at", "error",
        )
        read_only_fields = fields
