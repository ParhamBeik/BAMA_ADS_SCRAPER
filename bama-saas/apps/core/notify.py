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

import requests
from django.conf import settings

from apps.core.models import DealScoreCache, NotifiedAd, NotifierSettings
from apps.core.quality import exclude_unclear_price, verified_by_ad

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


def _candidates(cfg: NotifierSettings, limit: int = MAX_PER_RUN):
    """Unsent listings clearing every configured bar, best discount first."""
    qs = (
        verified_by_ad(DealScoreCache.objects.select_related("ad"))
        .filter(discount_pct__gte=cfg.min_discount_pct)
        .exclude(ad__notified__isnull=False)
    )
    # Gated here as well as at build time: the cache is rebuilt on a schedule,
    # and ordering by -discount_pct puts whatever slipped through straight into
    # the first message the user ever receives.
    qs = exclude_unclear_price(qs, prefix="ad__")
    if cfg.price_min is not None:
        qs = qs.filter(ad__current_price__gte=cfg.price_min)
    if cfg.price_max is not None:
        qs = qs.filter(ad__current_price__lte=cfg.price_max)
    if cfg.model_ids:
        qs = qs.filter(ad__model_id__in=cfg.model_ids)

    # peer_count lives in the components JSON, so this one bar is applied in
    # Python — a JSON cast per row for an already-short list is not worth it.
    out = []
    for row in qs.order_by("-discount_pct")[: limit * 5]:
        if (row.components or {}).get("peer_count", 0) >= cfg.min_peers:
            out.append(row)
        if len(out) >= limit:
            break
    return out


def format_message(row: DealScoreCache) -> str:
    """One listing as a Telegram HTML message."""
    ad = row.ad
    components = row.components or {}
    fair = components.get("fair_value") or row.peer_median or 0
    url = ad.url or ""
    if url and not url.startswith("http"):
        url = f"https://bama.ir{url}"
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
