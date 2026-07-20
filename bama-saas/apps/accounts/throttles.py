"""Subscription-aware throttling.

Two layers compose on pro/enterprise endpoints:
- ``SubscriptionThrottle``: burst rate (requests/min) scaled by plan.
- ``MonthlyQuotaThrottle``: hard monthly request cap from ``monthly_api_limit``
  (returns 429 once exhausted).
"""

from __future__ import annotations

from django.db.models import F
from rest_framework.throttling import BaseThrottle, UserRateThrottle

from .models import Subscription
from .permissions import active_subscription

# Burst rate per plan (requests/minute). Free tier is deliberately tight.
_PLAN_RATES = {
    Subscription.PlanType.FREE: "30/min",
    Subscription.PlanType.PRO: "300/min",
    Subscription.PlanType.ENTERPRISE: "2000/min",
}


class SubscriptionThrottle(UserRateThrottle):
    """Per-user burst throttle whose rate comes from the active subscription."""

    scope = "subscription"

    def get_rate(self):
        # UserRateThrottle.__init__ calls get_rate() before allow_request() has
        # had a chance to set the plan-specific rate. Fall back to the free-tier
        # rate so instantiation succeeds; allow_request() overrides self.rate
        # for authenticated users on every request.
        return _PLAN_RATES[Subscription.PlanType.FREE]

    def allow_request(self, request, view):
        user = request.user
        if user and user.is_authenticated:
            sub = active_subscription(user)
            plan = sub.plan_type if sub else Subscription.PlanType.FREE
            self.rate = _PLAN_RATES.get(plan, _PLAN_RATES[Subscription.PlanType.FREE])
            self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)


class MonthlyQuotaThrottle(BaseThrottle):
    """Reject (429) once the subscription's monthly_api_limit is reached.

    Atomically increments ``api_usage_count``; if the row was already at the
    limit the conditional UPDATE affects 0 rows and the request is throttled.
    """

    scope = "monthly_quota"

    def allow_request(self, request, view):
        sub = active_subscription(request.user)
        if sub is None or sub.monthly_api_limit is None:
            return True  # unlimited (or not authenticated — let auth/permission handle)
        updated = (
            Subscription.objects
            .filter(pk=sub.pk, api_usage_count__lt=sub.monthly_api_limit)
            .update(api_usage_count=F("api_usage_count") + 1)
        )
        return updated == 1

    def wait(self):
        return None  # monthly window — no meaningful retry-after
