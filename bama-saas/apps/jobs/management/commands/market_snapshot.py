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

from apps.core.models import MarketSnapshot
from apps.core.models import Ad


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

        active = list(
            Ad.objects.filter(
                status=Ad.Status.ACTIVE,
                current_price__gt=0,
                publish_at__isnull=False,
            ).values("current_price", "first_seen_at", "brand_id", "brand__slug")
        )

        prices = [a["current_price"] for a in active]
        new_count = sum(
            1 for a in active
            if a["first_seen_at"] and a["first_seen_at"].date() == date
        )
        brands: dict = defaultdict(int)
        for a in active:
            slug = a["brand__slug"]
            if slug:
                brands[slug] += 1

        removed_count = Ad.objects.filter(
            removed_at__date=date
        ).count()

        snapshot, _ = MarketSnapshot.objects.update_or_create(
            date=date,
            defaults={
                "active_count": len(active),
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
            f"{snapshot.new_count} new, {snapshot.removed_count} removed."
        ))
