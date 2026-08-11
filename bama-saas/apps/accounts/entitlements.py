"""Plan caps and daily feature counters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied

from .models import FeatureUsageCounter, Subscription
from .permissions import active_subscription


@dataclass(frozen=True)
class PlanLimits:
    favorites: int
    watchlists: int
    saved_searches: int
    alerts: int
    valuations_per_day: int
    model_comparison: bool
    csv_export: bool
    full_research: bool


PLAN_LIMITS: dict[str, PlanLimits] = {
    Subscription.PlanType.FREE: PlanLimits(
        favorites=25,
        watchlists=3,
        saved_searches=3,
        alerts=2,
        valuations_per_day=5,
        model_comparison=False,
        csv_export=False,
        full_research=False,
    ),
    Subscription.PlanType.PRO: PlanLimits(
        favorites=500,
        watchlists=50,
        saved_searches=50,
        alerts=25,
        valuations_per_day=500,
        model_comparison=True,
        csv_export=True,
        full_research=True,
    ),
    Subscription.PlanType.ENTERPRISE: PlanLimits(
        favorites=500,
        watchlists=50,
        saved_searches=50,
        alerts=25,
        valuations_per_day=500,
        model_comparison=True,
        csv_export=True,
        full_research=True,
    ),
}


class EntitlementError(APIException):
    status_code = 429
    default_detail = "Feature limit reached."

    def __init__(self, feature: str, limit: int, used: int, resets_at):
        super().__init__(
            {
                "detail": f"{feature} limit reached ({used}/{limit}).",
                "feature": feature,
                "limit": limit,
                "used": used,
                "resets_at": resets_at.isoformat(),
            }
        )


class FeatureForbidden(PermissionDenied):
    default_detail = "This feature requires a higher plan."


def effective_plan(user) -> str:
    if user and user.is_authenticated and user.is_staff:
        return Subscription.PlanType.PRO
    sub = active_subscription(user)
    if sub is None:
        return Subscription.PlanType.FREE
    if sub.expires_at and sub.expires_at < timezone.now():
        return Subscription.PlanType.FREE
    return sub.plan_type


def plan_limits(user) -> PlanLimits:
    return PLAN_LIMITS[effective_plan(user)]


def require_verified(user) -> None:
    if not user.email_verified_at:
        raise PermissionDenied("Email verification is required for this action.")


def require_feature(user, attr: str) -> None:
    if not getattr(plan_limits(user), attr):
        raise FeatureForbidden(f"{attr} is not available on your plan.")


def _day_start():
    now = timezone.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _next_day():
    return _day_start() + timedelta(days=1)


def increment_daily(user, feature: str, *, amount: int = 1) -> int:
    """Atomically increment a daily counter; raise EntitlementError at cap."""
    require_verified(user)
    limits = plan_limits(user)
    cap_map = {
        "valuation": limits.valuations_per_day,
    }
    cap = cap_map.get(feature)
    if cap is None:
        raise ValueError(f"Unknown daily feature: {feature}")

    day = _day_start().date()
    counter, _ = FeatureUsageCounter.objects.get_or_create(
        user=user, feature=feature, day=day, defaults={"count": 0}
    )
    updated = (
        FeatureUsageCounter.objects.filter(
            pk=counter.pk, count__lte=cap - amount
        ).update(count=F("count") + amount)
    )
    if updated == 0:
        counter.refresh_from_db()
        raise EntitlementError(feature, cap, counter.count, _next_day())
    counter.refresh_from_db()
    return counter.count
