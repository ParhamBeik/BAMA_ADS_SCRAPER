"""Mark ads not seen within ``STALE_AFTER_DAYS`` as REMOVED.

Run by the worker pipeline (and on demand). Idempotent: only flips ACTIVE →
REMOVED and stamps ``removed_at`` with the ad's own ``last_seen_at`` (the best
estimate of when it disappeared from the feed). Re-seeing a removed ad via
``ingest_ad`` flips it back to ACTIVE and clears ``removed_at``.

Returns the number of newly removed ads.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone

from apps.catalog.models import Ad


class Command(BaseCommand):
    help = "Mark ads unseen for STALE_AFTER_DAYS as REMOVED."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override STALE_AFTER_DAYS for this run.",
        )

    def handle(self, *args, **options):
        days = options["days"] if options["days"] is not None else settings.STALE_AFTER_DAYS
        cutoff = timezone.now() - timedelta(days=days)
        qs = Ad.objects.filter(status=Ad.Status.ACTIVE, last_seen_at__lt=cutoff)
        # Per-row removed_at = last_seen_at via F-expression (single UPDATE).
        count = qs.update(status=Ad.Status.REMOVED, removed_at=F("last_seen_at"))
        self.stdout.write(self.style.SUCCESS(
            f"Marked {count} ad(s) REMOVED (last_seen < {cutoff:%Y-%m-%d %H:%M} UTC, "
            f"stale_after={days}d)."
        ))
