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
