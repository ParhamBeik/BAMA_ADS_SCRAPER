"""Notification delivery primitives (in-app, email, Telegram).

These helpers are intentionally tiny and defensive: the alert evaluator calls
them in a loop, so one bad send (bad email, dead Telegram endpoint) must never
abort the whole run. Every transport is wrapped so failures become a FAILED
status on the Notification row, not a raised exception.

- in-app notifications are row-only: there is nothing to "deliver"; we just
  flip the status to SENT so the inbox query (status != PENDING) works.
- email uses Django's mail backend (console in dev, SMTP in prod).
- Telegram is opt-in via ``TELEGRAM_BOT_TOKEN`` + ``user.telegram_chat_id``;
  absence of either is treated as a silent no-op (not an error).
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import Notification

logger = logging.getLogger(__name__)

# Cap stored error text so a verbose exception can't blow up the column.
_ERROR_MAX = 500


def send_email(user, subject: str, body: str) -> bool:
    """Send a transactional email to the user. True on success.

    We set fail_silently=False so that SMTP errors raise exceptions and
    can be caught and correctly marked as FAILED in the database.
    """
    if not user.email:
        return False
    try:
        sent = send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
        return bool(sent)
    except Exception:  # pragma: no cover - defensive
        logger.exception("email send failed for user=%s", getattr(user, "pk", None))
        return False


def send_telegram(user, text: str) -> bool:
    """Push a Telegram message. No-op (True) when not configured for the user.

    Returns False only when we actually tried and the API/network rejected us.
    """
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(user, "telegram_chat_id", "") or ""
    if not token or not chat_id:
        return True  # not configured for this user — not an error
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        return resp.ok
    except requests.RequestException:
        logger.warning("telegram send failed for user=%s", getattr(user, "pk", None))
        return False


def create_notification(
    user,
    *,
    channel: str,
    subject: str,
    body: str,
    alert=None,
    related_ad=None,
    dedupe_key: str = "",
) -> tuple[Notification, bool]:
    """Create a Notification row, deduping on (dedupe_key, channel, user).

    With a non-empty ``dedupe_key`` the same logical event never produces two
    notifications on the same channel — ``get_or_create`` returns the existing
    row with ``created=False`` and the caller skips delivery. Returns the row
    and whether it was newly created.
    """
    defaults = {
        "alert": alert,
        "related_ad": related_ad,
        "subject": subject,
        "body": body,
    }
    if dedupe_key:
        return Notification.objects.get_or_create(
            dedupe_key=dedupe_key,
            channel=channel,
            user=user,
            defaults=defaults,
        )
    return Notification.objects.create(user=user, channel=channel, **defaults), True


def deliver(notification: Notification) -> None:
    """Dispatch a notification by channel and stamp the outcome.

    Never raises: any transport error is captured into ``notification.error``
    (truncated) and the row is marked FAILED. In-app rows have no transport,
    so they go straight to SENT.
    """
    try:
        if notification.channel == Notification.Channel.EMAIL:
            ok = send_email(notification.user, notification.subject, notification.body)
        elif notification.channel == Notification.Channel.TELEGRAM:
            ok = send_telegram(notification.user, notification.body)
        else:  # inapp — nothing to push
            ok = True

        if ok:
            notification.status = Notification.Status.SENT
            notification.sent_at = timezone.now()
            notification.error = ""
        else:
            notification.status = Notification.Status.FAILED
    except Exception as exc:  # pragma: no cover - defensive
        notification.status = Notification.Status.FAILED
        notification.error = str(exc)[:_ERROR_MAX]
    else:
        if notification.status == Notification.Status.FAILED:
            notification.error = "transport returned failure"[:_ERROR_MAX]

    notification.save(update_fields=["status", "sent_at", "error"])
