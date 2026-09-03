"""Send the deals worth interrupting for to Telegram.

Reads the deal board rather than recomputing anything: a notifier that scored
ads itself would be a second implementation of the number the site shows.

Two rules keep it quiet enough to stay switched on: a listing is announced once
ever (``NotifiedAd``), and nothing is sent below the configured discount *and*
peer count. Telegram is reached with ``requests``, already a crawler dependency
— no bot library, no queue, no retry daemon. A missed message is recoverable by
the deal board still showing the car.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

from apps.core.models import DealScoreCache, NotifiedAd, NotifierSettings
from apps.core.quality import exclude_unclear_price, verified_by_ad
from apps.jobs.parsing import absolute_ad_url

log = logging.getLogger("bama.notify")

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
REQUEST_TIMEOUT = 10

# One tick's worth. A backlog this large means something changed structurally
# (fresh install, threshold lowered); dumping hundreds of messages into a chat
# is worse than truncating and picking the rest up next tick.
MAX_PER_RUN = 10


def toman(value: int | None) -> str:
    """Same thresholds as ``ui.tsx:toman``, so a message and the board agree.

    This previously divided by 10_000_000 and labelled the result "M", making
    every alert understate the price tenfold — a 2.2B car read as "220M".
    """
    value = value or 0
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{round(value / 1_000_000):,}M"
    return f"{value:,}"


def matching_deals(
    *,
    min_discount_pct: float,
    min_peers: int,
    price_min: int | None = None,
    price_max: int | None = None,
    mileage_max: int | None = None,
    model_ids: list[int] | None = None,
    brand_slug: str = "",
    variant_id: int | None = None,
    year_jalali: int | None = None,
    exclude_review: bool = False,
    already_sent=None,
    limit: int = MAX_PER_RUN,
):
    """Scored listings clearing every bar, best discount first.

    The one matcher, used by both the operator's singleton and every per-user
    `AlertRule`. Two implementations of "is this worth interrupting for" would
    let the same listing qualify on one path and not the other, which is exactly
    the class of disagreement the deal board's own single-population rule exists
    to prevent.

    ``already_sent`` is a queryset of ad codes this recipient has had, passed in
    rather than assumed, because "already sent" is global for the singleton
    (`NotifiedAd`) and per-user for a rule (`AlertDelivery`).
    """
    qs = (
        verified_by_ad(DealScoreCache.objects.select_related("ad"))
        .filter(discount_pct__gte=min_discount_pct)
    )
    # Gated here as well as at build time: the cache is rebuilt on a schedule,
    # and ordering by -discount_pct puts whatever slipped through straight into
    # the first message the user ever receives.
    qs = exclude_unclear_price(qs, prefix="ad__")
    if already_sent is not None:
        qs = qs.exclude(ad_id__in=already_sent)
    if exclude_review:
        # The same rule the board's `top` band applies. A repainted car reading
        # 16% under its cohort is what a repainted car costs, and delivering it
        # as a find is how an alert feed teaches someone to ignore it.
        qs = qs.filter(needs_review=False)
    if price_min is not None:
        qs = qs.filter(ad__current_price__gte=price_min)
    if price_max is not None:
        qs = qs.filter(ad__current_price__lte=price_max)
    if mileage_max is not None:
        qs = qs.filter(ad__mileage__lte=mileage_max)
    if model_ids:
        qs = qs.filter(ad__model_id__in=model_ids)
    if brand_slug:
        qs = qs.filter(ad__model__brand__slug=brand_slug)
    if variant_id:
        qs = qs.filter(ad__variant_id=variant_id)
    if year_jalali:
        qs = qs.filter(ad__year_jalali=year_jalali)

    # peer_count lives in the components JSON, so this one bar is applied in
    # Python — a JSON cast per row for an already-short list is not worth it.
    out = []
    for row in qs.order_by("-discount_pct")[: limit * 5]:
        if (row.components or {}).get("peer_count", 0) >= min_peers:
            out.append(row)
        if len(out) >= limit:
            break
    return out


def _candidates(cfg: NotifierSettings, limit: int = MAX_PER_RUN):
    """The operator singleton's candidates, through the shared matcher."""
    return matching_deals(
        min_discount_pct=cfg.min_discount_pct,
        min_peers=cfg.min_peers,
        price_min=cfg.price_min,
        price_max=cfg.price_max,
        model_ids=cfg.model_ids,
        # Global rather than per-recipient: this is the one-chat operator feed.
        already_sent=NotifiedAd.objects.values("ad_id"),
        limit=limit,
    )


def format_message(row: DealScoreCache) -> str:
    """One listing as a Telegram HTML message."""
    ad = row.ad
    components = row.components or {}
    fair = components.get("fair_value") or row.peer_median or 0
    url = absolute_ad_url(ad.url or ad.canonical_path)
    lines = [
        f"<b>{row.discount_pct:.0f}% below fair value</b>",
        f"{ad.title or ''} — {ad.year_jalali or '?'}",
        f"Asking {toman(ad.current_price)} toman (fair ~{toman(fair)})",
        f"{(ad.mileage or 0):,} km · {components.get('peer_count', '?')} peers "
        f"· {components.get('confidence', '?')} confidence",
    ]
    if url:
        lines.append(url)
    return "\n".join(lines)


def send_telegram(text: str, chat_id: str) -> bool:
    token = settings.BAMA_TELEGRAM_TOKEN
    if not token or not chat_id:
        log.warning("notify: telegram not configured (token/chat_id missing)")
        return False
    try:
        response = requests.post(
            TELEGRAM_URL.format(token=token),
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": False},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        # A failed send must not mark the ad notified, and must not take the
        # pipeline down: the deal is still on the board either way.
        log.warning("notify: telegram send failed: %s", exc)
        return False


def format_alert(row: DealScoreCache) -> str:
    """Same message shape as the operator's, so both channels read alike."""
    return format_message(row)


def format_delivery_alert(delivery) -> str:
    """A retryable alert from its immutable delivery snapshot."""
    ad = delivery.ad
    lines = [
        f"<b>{(delivery.discount_pct or 0):.0f}% below fair value</b>",
        f"{ad.title or ''} — {ad.year_jalali or '?'}",
        f"Asking {toman(ad.current_price)} toman (fair ~{toman(delivery.peer_median)})",
    ]
    if url := absolute_ad_url(ad.url or ad.canonical_path):
        lines.append(url)
    return "\n".join(lines)


# One user's rules can only put this many cars in their feed per tick. A rule
# written too loosely — 2% off anything — would otherwise deliver hundreds on
# its first run and the feed would be useless from the moment it was created.
MAX_PER_USER_PER_RUN = 12


def deliver_alerts(*, dry_run: bool = False) -> dict:
    """Fill every user's alert feed from the deal board.

    Reads the board rather than re-scoring anything, for the same reason the
    operator notifier does: a second implementation of "is this a deal" is a
    second answer to it.

    In-app delivery is the product; Telegram is optional and per rule. A failed
    send therefore marks `telegram_sent=False` and leaves the row — the user
    still has the alert, which is the part that matters. The operator singleton
    does the opposite (no row unless the send succeeded) because there the
    message *is* the delivery.
    """
    from apps.accounts.models import AlertDelivery, AlertRule

    rules = list(AlertRule.objects.filter(enabled=True).select_related("user"))
    if not rules:
        return {"rules": 0, "delivered": 0, "telegram_sent": 0}

    delivered = 0
    seen_by_user: dict = defaultdict(set)
    for user_id, ad_id in AlertDelivery.objects.filter(
        user_id__in={rule.user_id for rule in rules}
    ).values_list("user_id", "ad_id"):
        seen_by_user[user_id].add(ad_id)
    # Per user, so two of one user's rules matching the same car deliver it
    # once. Loaded per user rather than per rule for the same reason.
    per_user: dict = defaultdict(int)
    for rule in rules:
        if per_user[rule.user_id] >= MAX_PER_USER_PER_RUN:
            continue
        rows = matching_deals(
            min_discount_pct=rule.min_discount_pct,
            min_peers=rule.min_peers,
            price_min=rule.price_min,
            price_max=rule.price_max,
            mileage_max=rule.mileage_max,
            model_ids=[rule.model_id] if rule.model_id else None,
            brand_slug=rule.brand_slug,
            variant_id=rule.variant_id,
            year_jalali=rule.year_jalali,
            exclude_review=rule.exclude_review,
            already_sent=seen_by_user[rule.user_id],
            limit=MAX_PER_USER_PER_RUN - per_user[rule.user_id],
        )
        for row in rows:
            if dry_run:
                delivered += 1
                continue
            # get_or_create, not create: two rules of the same user can select
            # the same ad inside one tick, before either is in `seen`.
            entry, created = AlertDelivery.objects.get_or_create(
                user_id=rule.user_id, ad_id=row.ad_id,
                defaults={"rule": rule, "discount_pct": row.discount_pct,
                          "peer_median": row.peer_median},
            )
            if not created:
                continue
            delivered += 1
            per_user[rule.user_id] += 1
            seen_by_user[rule.user_id].add(row.ad_id)

    return {"rules": len(rules), "delivered": delivered, "dry_run": dry_run}


def send_alerts(*, dry_run: bool = False, max_send: int = MAX_PER_RUN) -> dict:
    """Retry unsent per-user Telegram alerts without rebuilding the feed."""
    from apps.accounts.models import AlertDelivery

    since = timezone.now() - timedelta(hours=24)
    pending_qs = AlertDelivery.objects.filter(
        telegram_sent=False,
        rule__telegram_chat_id__gt="",
        created_at__gte=since,
    ).select_related("ad", "rule").order_by("-created_at")
    pending_count = pending_qs.count()
    sent = 0
    for delivery in pending_qs[:max_send]:
        if dry_run:
            sent += 1
        elif send_telegram(format_delivery_alert(delivery), delivery.rule.telegram_chat_id):
            delivery.telegram_sent = True
            delivery.save(update_fields=["telegram_sent"])
            sent += 1
    return {"pending": pending_count, "sent": sent, "dry_run": dry_run}


def notify_deals(*, dry_run: bool = False) -> dict:
    cfg = NotifierSettings.load()
    if not cfg.enabled:
        return {"enabled": False, "sent": 0, "candidates": 0}

    rows = _candidates(cfg)
    sent = 0
    for row in rows:
        if dry_run:
            continue
        if send_telegram(format_message(row), cfg.telegram_chat_id):
            # Recorded only on a confirmed send, so a Telegram outage retries
            # next tick instead of swallowing the listing forever.
            NotifiedAd.objects.get_or_create(
                ad_id=row.ad_id, defaults={"discount_pct": row.discount_pct}
            )
            sent += 1

    return {"enabled": True, "candidates": len(rows), "sent": sent, "dry_run": dry_run}
