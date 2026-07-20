"""Daily/weekly digest email for users with favorites, watchlists, or saved searches.

MVP digest: for each qualifying user, count the new PriceDropEvents on their
favorited + watchlisted ads within the window (1 or 7 days), plus their
favorite count, and send ONE email + ONE in-app Notification. The dedupe key
``digest:<kind>:<user_id>:<date_iso>`` makes it safe to re-run within the
same day — the user gets at most one digest per kind per day.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import (
    Alert,
    Favorite,
    Notification,
    SavedSearch,
    User,
    Watchlist,
)
from apps.accounts.notifications import create_notification, deliver
from apps.core.models import PriceDropEvent
from django.core.management.base import BaseCommand

_WINDOW = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}


class Command(BaseCommand):
    help = "Send a daily/weekly digest of price drops to engaged users."

    def add_arguments(self, parser):
        parser.add_argument(
            "--kind",
            choices=["daily", "weekly"],
            default="daily",
            help="Window: daily (1d) or weekly (7d). Default: daily.",
        )

    def handle(self, *args, **options):
        kind = options["kind"]
        cutoff = timezone.now() - _WINDOW[kind]
        today_iso = timezone.now().date().isoformat()

        # Active users with any engagement signal (favorites, watchlists,
        # saved searches, or alerts). This is the digest audience.
        engaged_ids = set(
            Favorite.objects.values_list("user_id", flat=True)
        ) | set(
            Watchlist.objects.values_list("user_id", flat=True)
        ) | set(
            SavedSearch.objects.values_list("user_id", flat=True)
        ) | set(
            Alert.objects.values_list("user_id", flat=True)
        )

        sent = 0
        users = User.objects.filter(id__in=engaged_ids, is_active=True)
        for user in users:
            # Tracked ads = favorites ∪ all watchlist memberships.
            fav_codes = set(
                Favorite.objects.filter(user=user).values_list("ad__code", flat=True)
            )
            wl_codes = set(
                Watchlist.objects.filter(user=user)
                .values_list("ads__code", flat=True)
            )
            tracked_codes = fav_codes | wl_codes

            new_drops = (
                PriceDropEvent.objects.filter(
                    ad__code__in=tracked_codes, observed_at__gte=cutoff,
                ).count()
                if tracked_codes
                else 0
            )
            fav_count = len(fav_codes)

            # Nothing to report → skip (don't spam an empty digest).
            if new_drops == 0 and fav_count == 0:
                continue

            subject = f"خلاصه {kind} شما — Bama"
            body = (
                f"سلام،\n"
                f"تعداد آگهی‌های نشان‌شده: {fav_count}\n"
                f"کاهش قیمت‌های جدید روی آگهی‌های شما: {new_drops}\n"
            )
            dedupe_key = f"digest:{kind}:{user.id}:{today_iso}"

            for channel in (Notification.Channel.EMAIL, Notification.Channel.INAPP):
                notification, created = create_notification(
                    user,
                    channel=channel,
                    subject=subject,
                    body=body,
                    dedupe_key=dedupe_key,
                )
                if created:
                    deliver(notification)
                    if channel == Notification.Channel.EMAIL:
                        sent += 1

        self.stdout.write(self.style.SUCCESS(
            f"Digest '{kind}' sent to {sent} user(s)."
        ))
