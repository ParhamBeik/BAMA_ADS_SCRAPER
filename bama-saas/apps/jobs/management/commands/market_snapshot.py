"""Build today's (or ``--date``) whole-market rollup into MarketSnapshot.

Idempotent via ``update_or_create(date=...)``: re-running for a date just
refreshes that row. Pulls the active priced publish-complete set and aggregates
in Python (median has no ORM aggregate), plus counts of new (first-seen today)
and removed (removed today) ads and a ``{brand_slug: ad_count}`` breakdown.

Usage:
    python manage.py market_snapshot [--date YYYY-MM-DD]
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.models import Ad, DailyInventorySnapshot, MarketSnapshot
from apps.core.services.quality import verified


class Command(BaseCommand):
    help = "Build the whole-market daily rollup (MarketSnapshot) for a date."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date", type=str, default=None,
            help="Snapshot date as YYYY-MM-DD (default: today).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        raw = options["date"]
        if raw:
            try:
                date = timezone.datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                raise CommandError("--date must be YYYY-MM-DD")
        else:
            date = timezone.now().date()

        today = timezone.now().date()
        if date > today:
            raise CommandError("--date cannot be in the future")

        if date == today:
            active_count, prices, brands = self._from_live_ads()
            new_count = verified(Ad.objects).filter(first_seen_at__date=date).count()
        else:
            # `Ad` is a *current*-snapshot table: its status column says what is
            # live now, not what was live on a past date. Reading it while
            # backfilling stamped today's active_count onto every historical row.
            # DailyInventorySnapshot is per-date by construction (and
            # backfill_snapshots reconstructs it from sightings), so it is the
            # only honest source for a day that has already passed.
            active_count, prices, brands = self._from_snapshots(date)
            if active_count is None:
                raise CommandError(
                    f"No DailyInventorySnapshot rows for {date}; run "
                    f"`backfill_snapshots` first. Refusing to write today's "
                    f"numbers under a past date."
                )
            new_count = 0  # not reconstructable per past day; see backfill_snapshots

        removed_count = verified(Ad.objects).filter(removed_at__date=date).count()

        snapshot, _ = MarketSnapshot.objects.update_or_create(
            date=date,
            defaults={
                "active_count": active_count,
                "new_count": new_count,
                "removed_count": removed_count,
                "median_price": int(statistics.median(prices)) if prices else None,
                "mean_price": int(statistics.mean(prices)) if prices else None,
                "min_price": min(prices) if prices else None,
                "max_price": max(prices) if prices else None,
                "brand_breakdown": dict(brands),
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"MarketSnapshot for {date}: {snapshot.active_count} active, "
            f"{snapshot.new_count} new, {snapshot.removed_count} removed "
            f"({'live' if date == today else 'reconstructed'})."
        ))

    @staticmethod
    def _from_live_ads():
        """Today: read the current Ad table directly."""
        rows = list(
            verified(Ad.objects)
            .filter(
                status=Ad.Status.ACTIVE,
                current_price__gt=0,
                publish_at__isnull=False,
            ).values("current_price", "brand__slug")
        )
        brands: dict = defaultdict(int)
        for r in rows:
            if r["brand__slug"]:
                brands[r["brand__slug"]] += 1
        return len(rows), [r["current_price"] for r in rows], brands

    @staticmethod
    def _from_snapshots(date):
        """A past date: rebuild from that day's per-cohort snapshot rows.

        Each cohort contributes its median once per ad it held, so the resulting
        distribution is the cohort-median distribution weighted by cohort size —
        an approximation of the true per-ad spread, but one that is *as of the
        right day*, which reading `Ad` is not.
        """
        rows = list(
            DailyInventorySnapshot.objects.filter(
                date=date, median_price__isnull=False
            ).values("ad_count", "median_price", "model__brand__slug")
        )
        if not rows:
            return None, [], {}
        prices: list[int] = []
        brands: dict = defaultdict(int)
        active_count = 0
        for r in rows:
            n = r["ad_count"] or 0
            active_count += n
            prices.extend([r["median_price"]] * n)
            if r["model__brand__slug"]:
                brands[r["model__brand__slug"]] += n
        return active_count, prices, brands
