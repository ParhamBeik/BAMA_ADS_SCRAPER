"""Snapshot today's inventory per (model, variant, year) into DailyInventorySnapshot.

Idempotent for today: drops today's rows then rebuilds, so re-running every
worker tick just refreshes today's snapshot. Pulls one publish-complete, priced,
ACTIVE queryset and aggregates in Python — median has no built-in ORM aggregate,
and at ~50k rows the single pull is cheap. Powers inventory-count and
median-price-over-time charts plus day-over-day growth/decline.

These rows are also the input to the matched-cohort market index
(``apps/core/services/index.py``), which is why the cohort key must match the
one every other consumer uses: ``year_jalali``, never the raw mixed-calendar
``year``. Grouping on ``year`` split each real cohort into a Jalali half and a
Gregorian half, so both halves carried a wrong median and no cohort could be
matched across days.

Returns the number of snapshot rows written.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.core.models import DailyInventorySnapshot
from apps.core.models import Ad
from apps.core.services.quality import verified


class Command(BaseCommand):
    help = "Snapshot today's inventory (count + price stats) per market slice."

    def add_arguments(self, parser):
        parser.add_argument(
            "--min-count", type=int, default=1,
            help="Skip market slices with fewer than this many ads.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        today = timezone.now().date()
        DailyInventorySnapshot.objects.filter(date=today).delete()

        rows = (
            verified(Ad.objects)
            .filter(
                status=Ad.Status.ACTIVE,
                current_price__gt=0,
                publish_at__isnull=False,
                year_jalali__isnull=False,
            )
            .values(
                "model_id", "variant_id", "year_jalali", "current_price",
                "first_seen_at",
            )
        )

        groups: dict = defaultdict(lambda: {"prices": [], "new": 0})
        for r in rows:
            key = (r["model_id"], r["variant_id"], r["year_jalali"])
            price = r["current_price"]
            if price:
                groups[key]["prices"].append(price)
            first_seen = r["first_seen_at"]
            if first_seen and first_seen.date() == today:
                groups[key]["new"] += 1

        min_count = options["min_count"]
        objs = [
            DailyInventorySnapshot(
                model_id=model_id, variant_id=variant_id,
                year_jalali=year_jalali, date=today,
                ad_count=len(prices), new_count=g["new"],
                median_price=int(statistics.median(prices)),
                mean_price=int(statistics.mean(prices)),
                min_price=min(prices), max_price=max(prices),
            )
            for (model_id, variant_id, year_jalali), g in groups.items()
            if len((prices := g["prices"])) >= min_count
        ]
        if objs:
            DailyInventorySnapshot.objects.bulk_create(objs, batch_size=500)

        ad_total = sum(o.ad_count for o in objs)
        self.stdout.write(self.style.SUCCESS(
            f"Snapshotted {len(objs)} market slice(s) for {today} ({ad_total} ads, "
            f"{sum(o.new_count for o in objs)} new today)."
        ))
