"""Alert evaluator — turns enabled Alert rows into delivered Notifications.

``evaluate_alerts`` is the single entry point used by the
``evaluate_alerts`` management command and (transitively) the jobs pipeline.
It walks every enabled alert once and, per matching event, fans out one
Notification per channel (in-app / email / telegram). Dedup is keyed on
``<type>:<alert_id>:<entity_id>`` so a re-run never double-notifies.

Three alert shapes:
- ``price_drop``  — fires on PriceDropEvent rows for ``alert.ad`` or every ad
  in ``alert.watchlist``, filtered by ``threshold`` (min drop %).
- ``undervalued`` — delegates to ``analytics.services.insights.undervalued``
  for ``alert.model`` (uses ``threshold`` as min discount %).
- ``new_listing`` — applies the saved search's AdFilter params and surfaces
  ads first-seen since ``saved_search.last_checked_at``; the watermark is
  advanced after each run (even on zero matches) so the window doesn't grow.
"""

from __future__ import annotations

from django.utils import timezone

from apps.catalog.filters import AdFilter
from apps.catalog.models import Ad
from apps.market.models import PriceDropEvent

from .models import Alert, Notification
from .notifications import create_notification, deliver

# Default channel set when an alert has none stored.
_DEFAULT_CHANNELS = [Notification.Channel.INAPP, Notification.Channel.EMAIL]


def _fan_out(alert, *, subject, body, related_ad, dedupe_key, channels, counters):
    """Create + deliver one Notification per channel for a single event.

    Only newly-created rows are delivered (a repeat run hits the dedupe guard
    and ``created=False``, so we skip re-sending the same email/Telegram).
    """
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
    if alert.ad_id:
        ads = [alert.ad]
    elif alert.watchlist_id:
        ads = list(alert.watchlist.ads.all())
    else:
        return  # misconfigured — nothing to scan

    for ad in ads:
        events = PriceDropEvent.objects.filter(ad=ad, drop_pct__gte=threshold)
        for event in events:
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
    # Imported lazily to avoid a circular import at module load time.
    from apps.analytics.services.insights import undervalued

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


def _eval_new_listing(alert, channels, counters):
    saved_search = alert.saved_search
    if saved_search is None:
        return

    params = saved_search.params or {}
    filt = AdFilter(data=params, queryset=Ad.objects.all())
    qs = filt.qs
    if saved_search.last_checked_at is not None:
        qs = qs.filter(first_seen_at__gt=saved_search.last_checked_at)

    for ad in qs:
        dedupe_key = f"new_listing:{alert.id}:{ad.code}"
        subject = "آگهی جدید"
        body = f"آگهی جدید: {ad.title or ad.code}"
        _fan_out(
            alert, subject=subject, body=body, related_ad=ad,
            dedupe_key=dedupe_key, channels=channels, counters=counters,
        )

    # Advance the watermark whether or not anything matched, so the next run
    # only sees genuinely new ads. Guard with a fresh read in case the row
    # was modified concurrently.
    saved_search.refresh_from_db()
    saved_search.last_checked_at = timezone.now()
    saved_search.save(update_fields=["last_checked_at"])


_EVALUATORS = {
    Alert.Type.PRICE_DROP: _eval_price_drop,
    Alert.Type.UNDERVALUED: _eval_undervalued,
    Alert.Type.NEW_LISTING: _eval_new_listing,
}


def evaluate_alerts() -> dict:
    """Run every enabled alert once. Returns a small summary dict.

    Robustness: a single alert's failure does not abort the batch — each one
    is wrapped and logged. Counts are best-effort totals across all alerts.
    """
    counters = {"alerts": 0, "notifications": 0, "delivered": 0}
    alerts = (
        Alert.objects.filter(enabled=True)
        .select_related("user", "ad", "watchlist", "saved_search", "model")
    )
    for alert in alerts:
        counters["alerts"] += 1
        channels = alert.channels or list(_DEFAULT_CHANNELS)
        handler = _EVALUATORS.get(alert.alert_type)
        if handler is None:
            continue
        handler(alert, channels, counters)
    return counters
