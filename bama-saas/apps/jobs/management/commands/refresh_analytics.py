"""Precompute per-market price statistics for the dashboard landing.

Aggregates publish-complete, priced ads by (brand, model, variant, year) into
``PriceStatistics`` (time_window="all"). The trimmed true-mean is served on
demand by the analytics endpoints; this table is the fast glance for landing
pages and market lists.

Usage:
    python manage.py refresh_analytics [--min-count 3]
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Avg, Count, Max, Min, StdDev
from django.utils import timezone

from apps.core.models import PriceStatistics
from apps.core.models import Ad


class Command(BaseCommand):
    help = "Refresh precomputed price statistics per (brand, model, variant, year)."

    def add_arguments(self, parser):
        parser.add_argument("--min-count", type=int, default=3,
                            help="Skip market slices with fewer than this many ads.")

    @transaction.atomic
    def handle(self, *args, **options):
        min_count = options["min_count"]
        base = (
            Ad.objects.filter(current_price__gt=0, publish_at__isnull=False)
            .values("brand_id", "model_id", "variant_id", "year")
            .annotate(
                count=Count("code"),  # Ad PK is `code`, not `id`
                mean=Avg("current_price"),
                std_dev=StdDev("current_price"),
                min_price=Min("current_price"),
                max_price=Max("current_price"),
            )
            .filter(count__gte=min_count)
        )

        # Full refresh of the "all" window: drop and rebuild.
        PriceStatistics.objects.filter(time_window="all").delete()
        now = timezone.now()
        objs = [
            PriceStatistics(
                brand_id=g["brand_id"],
                model_id=g["model_id"],
                variant_id=g["variant_id"],
                year=g["year"],
                time_window="all",
                mean=g["mean"],
                median=None,  # trimmed median is served on demand via true-mean
                std_dev=g["std_dev"],
                min_price=g["min_price"],
                max_price=g["max_price"],
                count=g["count"],
                calculated_at=now,
            )
            for g in base
        ]
        PriceStatistics.objects.bulk_create(objs, batch_size=500)

        self.stdout.write(self.style.SUCCESS(
            f"Refreshed {len(objs)} market statistics (min_count={min_count})."
        ))
