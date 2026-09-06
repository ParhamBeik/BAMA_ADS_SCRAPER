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

import html
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

# Below this, a band median is one or two cars and quoting it as "what these go
# for" would be inventing precision. Same bar the fair-value engine uses before
# it will quote any median, so the message and the site cannot disagree about
# when a number is sayable.
MIN_BAND_PEERS = 8


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


def title_of(ad) -> str:
    """An ad's title, safe to drop into a ``parse_mode=HTML`` message.

    Hardening, not a fix for something observed — measured against production on
    2026-09-04, 0 of 82,700 stored titles contain ``<``, ``>`` or ``&``, the
    operator notifier is disabled, and no message has ever been sent. Recorded
    that way so nobody re-derives an urgency this does not have.

    It is still the right shape. The title is scraped from bama.ir, so it is
    third-party text going into markup — the one place in this module where that
    happens — and the two failure modes are bad enough to be worth one call:
    Telegram answers 400 "can't parse entities" to an unrecognised ``<``, and
    since neither channel records a delivery it did not confirm, one such
    listing on the board would make the operator feed retry the same doomed
    message every tick. A seller who chose the title would also be choosing the
    markup, so ``<a href=...>`` would be their link inside our alert.
    """
    return html.escape(ad.title or "")


# English labels for the four condition bands, so a message reads without
# knowing the codebase's vocabulary. The Persian `body_status` is printed beside
# it because that is the string the listing page itself shows.
BAND_LABELS = {
    "clean": "no paintwork",
    "cosmetic": "minor marks",
    "painted": "repainted",
    "structural": "panel replaced",
}


def format_message(row: DealScoreCache) -> str:
    """One listing as a Telegram HTML message.

    Written to be acted on from the phone: the top line is the claim, the middle
    is the evidence for it, and the last line is the listing. The evidence block
    carries the *band* median as well as the cohort median because they answer
    different questions and the gap between them is usually the whole story — a
    repainted car 30% under its cohort is often only 5% under other repainted
    cars, and that is the difference between a find and a waste of an evening.
    """
    ad = row.ad
    components = row.components or {}
    fair = components.get("fair_value") or row.peer_median or 0
    band = components.get("condition_band")
    band_median = components.get("condition_band_median")
    band_peers = components.get("condition_band_peers") or 0

    lines = [
        f"<b>{row.discount_pct:.0f}% below fair value</b>",
        f"{title_of(ad)} — {ad.year_jalali or '?'}",
        # The whole clause is bold, not just the number: `2.20B toman` has to
        # stay one contiguous string, because the regression test for the 10x
        # magnitude bug asserts on exactly that substring.
        f"<b>Asking {toman(ad.current_price)} toman</b> (fair ~{toman(fair)})",
        "",
        f"Peers  {components.get('peer_count', '?')} cars · same model+variant+year",
        f"       median {toman(row.peer_median)} · {components.get('confidence', '?')} confidence",
    ]
    if band:
        label = BAND_LABELS.get(band, band)
        status = html.escape(components.get("body_status") or "")
        lines.append(f"Band   {status} ({label})".rstrip())
        # Only quote a band median that has enough peers to mean anything — the
        # same bar the board applies before it will quote any median at all.
        if band_median and band_peers >= MIN_BAND_PEERS:
            lines.append(f"       {band_peers} similar · median {toman(band_median)}")
        else:
            lines.append(f"       {band_peers} similar · too few to quote a median")
    if ad.mileage:
        lines.append(f"Km     {ad.mileage:,}")
    if url := absolute_ad_url(ad.url or ad.canonical_path):
        lines += ["", url]
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


def format_delivery_alert(delivery) -> str:
    """A retryable alert from its immutable delivery snapshot."""
    ad = delivery.ad
    lines = [
        f"<b>{(delivery.discount_pct or 0):.0f}% below fair value</b>",
        f"{title_of(ad)} — {ad.year_jalali or '?'}",
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


# ---------------------------------------------------------------------------
# Health alerts
# ---------------------------------------------------------------------------
#
# On 2026-09-06 removal detection had been dead for 14 hours, four ML models had
# been frozen for five days, and bama.ir had had a two-and-a-half hour outage.
# Every one of those was detectable from data this system already stored, and
# none of them reached a human, because the health report's only consumer was a
# log line that said `health=ok` next to `ok=False`.
#
# On state change only. A check that is red today and red tomorrow is one piece
# of news, and a monitor that repeats itself every half hour is a monitor that
# gets muted — which is the same as not having one.


def format_health_alert(newly_red: list, recovered: list[str]) -> str:
    """The state change, with the failing checks' own explanations."""
    lines: list[str] = []
    if newly_red:
        lines.append(f"<b>⚠️ {len(newly_red)} check(s) went red</b>")
        for check in newly_red:
            lines.append(f"\n<b>{html.escape(check['name'])}</b>")
            lines.append(html.escape(check["detail"]))
    if recovered:
        if lines:
            lines.append("")
        lines.append(f"<b>✅ recovered:</b> {html.escape(', '.join(recovered))}")
    return "\n".join(lines)


def send_health_alert(*, newly_red: list, recovered: list[str],
                      dry_run: bool = False) -> dict:
    """Tell the operator chat that crawl health changed state.

    Routed to the operator singleton's chat and gated on `enabled`, the same
    switch the deal feed uses: one place to go quiet, not two. Unlike a deal, a
    health alert is not recorded as delivered — there is nothing to de-duplicate
    against, because the transition itself only happens once.
    """
    if not (newly_red or recovered):
        return {"changed": 0, "sent": 0}
    cfg = NotifierSettings.load()
    if not cfg.enabled or not cfg.telegram_chat_id:
        return {"changed": len(newly_red) + len(recovered), "sent": 0,
                "enabled": False}
    if dry_run:
        return {"changed": len(newly_red) + len(recovered), "sent": 0, "dry_run": True}
    sent = send_telegram(format_health_alert(newly_red, recovered),
                         cfg.telegram_chat_id)
    return {"changed": len(newly_red) + len(recovered), "sent": int(sent)}


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
