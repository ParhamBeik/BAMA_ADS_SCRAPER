"""Permissions for subscription-gated endpoints."""

from __future__ import annotations

from rest_framework.permissions import BasePermission

from .models import Subscription


def active_subscription(user) -> Subscription | None:
    """Return the user's active subscription, or None."""
    if not (user and user.is_authenticated):
        return None
    return user.subscriptions.filter(status=Subscription.Status.ACTIVE).first()


class IsActiveSubscription(BasePermission):
    """Require an active subscription (any tier). Use on pro/enterprise endpoints."""

    message = "An active subscription is required for this resource."

    def has_permission(self, request, view):
        return active_subscription(request.user) is not None


class IsStaff(BasePermission):
    """Admin-only endpoints (live fetch, audit runs)."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_staff)
