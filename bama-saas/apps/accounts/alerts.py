"""Alert evaluator — turns enabled Alert rows into delivered Notifications.

``evaluate_alerts`` is the single entry point used by the
``evaluate_alerts`` management command and (transitively) the jobs pipeline.
It walks every enabled alert once and, per matching event, fans out one
Notification per channel (in-app / email / telegram). Dedup is keyed on
``<type>:<alert_id>:<entity_id>`` so a re-run never double-notifies.

Two alert shapes:
- ``price_drop``  — fires on PriceDropEvent rows for ``alert.ad``,
  filtered by ``threshold`` (min drop %).
- ``undervalued`` — delegates to ``insights.undervalued`` for ``alert.model``.
"""

from __future__ import annotations

from apps.core.models import Ad
from apps.core.models import PriceDropEvent

from .models import Alert, Notification
from .notifications import create_notification, deliver

_DEFAULT_CHANNELS = [Notification.Channel.INAPP, Notification.Channel.EMAIL]

_CHANNEL_ALIASES = {
    "in_app": Notification.Channel.INAPP,
}


def _normalize_channels(channels) -> list[str]:
    """Map known aliases onto Notification.Channel values; drop unknowns."""
    allowed = {c.value for c in Notification.Channel}
    out: list[str] = []
    for raw in channels or []:
        ch = _CHANNEL_ALIASES.get(raw, raw)
        if ch in allowed and ch not in out:
            out.append(ch)
    return out or list(_DEFAULT_CHANNELS)


def _fan_out(alert, *, subject, body, related_ad, dedupe_key, channels, counters):
    """Create + deliver one Notification per channel for a single event."""
    for channel in channels:
        notification, created = create_notification(
            alert.user,
            channel=channel,
            subject=subject,
            body=body,
            alert=alert,
            related_ad=related_ad,
            dedupe_key=dedupe_key,
        )
        counters["notifications"] += 1
        if created:
            deliver(notification)
            counters["delivered"] += 1


def _eval_price_drop(alert, channels, counters):
    threshold = alert.threshold or 0
    if not alert.ad_id:
        return
    events = PriceDropEvent.objects.filter(ad=alert.ad, drop_pct__gte=threshold)
    for event in events:
        ad = alert.ad
        dedupe_key = f"price_drop:{alert.id}:{event.id}"
        subject = "کاهش قیمت آگهی"
        body = (
            f"قیمت کاهش یافت: {ad.title or ad.code} "
            f"{event.drop_pct:.1f}% (از {event.old_price} به {event.new_price})"
        )
        _fan_out(
            alert, subject=subject, body=body, related_ad=ad,
            dedupe_key=dedupe_key, channels=channels, counters=counters,
        )


def _eval_undervalued(alert, channels, counters):
    if not alert.model_id:
        return
    from apps.core.services.insights import undervalued

    min_discount = alert.threshold if alert.threshold is not None else 10.0
    result = undervalued(alert.model_id, min_discount_pct=min_discount)
    for listing in result.get("listings", []):
        code = listing["code"]
        dedupe_key = f"undervalued:{alert.id}:{code}"
        subject = "آگهی زیر قیمت بازار"
        body = (
            f"پیشنهاد زیر قیمت: {listing.get('title', code)} "
            f"{listing['discount_pct']}% ارزان‌تر از میانه"
        )
        try:
            ad = Ad.objects.get(code=code)
        except Ad.DoesNotExist:
            ad = None
        _fan_out(
            alert, subject=subject, body=body, related_ad=ad,
            dedupe_key=dedupe_key, channels=channels, counters=counters,
        )


_EVALUATORS = {
    Alert.Type.PRICE_DROP: _eval_price_drop,
    Alert.Type.UNDERVALUED: _eval_undervalued,
}


def evaluate_alerts() -> dict:
    """Run every enabled alert once. Returns a small summary dict."""
    counters = {"alerts": 0, "notifications": 0, "delivered": 0}
    alerts = (
        Alert.objects.filter(enabled=True)
        .select_related("user", "ad", "model")
    )
    for alert in alerts:
        counters["alerts"] += 1
        channels = _normalize_channels(alert.channels)
        handler = _EVALUATORS.get(alert.alert_type)
        if handler is None:
            continue
        handler(alert, channels, counters)
    return counters
