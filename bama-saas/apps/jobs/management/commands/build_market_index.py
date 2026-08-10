"""Rebuild the matched-cohort price index for every scope.

Runs after ``daily_snapshot`` in the worker pipeline, because it reads exactly
the rows that command writes. Cheap: no network, one query per scope over an
already-aggregated table.

Per-brand and per-model series are only built where there is something to
measure — a scope needs ``--min-cohorts`` distinct cohorts, otherwise its
"index" would be one or two cars pretending to be a market.

Usage:
    python manage.py build_market_index [--min-cohorts 5] [--scope market|brand|model]
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.core.models import DailyInventorySnapshot, MarketIndex
from apps.core.services.index import build_index


class Command(BaseCommand):
    help = "Rebuild the matched-cohort market price index (market/brand/model)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--min-cohorts", type=int, default=5,
            help="Skip a brand/model scope with fewer distinct cohorts (default: 5).",
        )
        parser.add_argument(
            "--scope", choices=["market", "brand", "model", "all"], default="all",
            help="Build only one scope level (default: all).",
        )

    def handle(self, *args, **options):
        want = options["scope"]
        min_cohorts = options["min_cohorts"]
        total_rows = 0
        total_scopes = 0

        if want in ("market", "all"):
            rows = build_index(MarketIndex.Scope.MARKET, None)
            total_rows += rows
            total_scopes += 1
            self.stdout.write(f"market: {rows} point(s)")

        if want in ("brand", "all"):
            slugs = self._eligible(
                "model__brand__slug", min_cohorts
            )
            for slug in slugs:
                total_rows += build_index(MarketIndex.Scope.BRAND, slug)
                total_scopes += 1
            self.stdout.write(f"brand: {len(slugs)} scope(s)")

        if want in ("model", "all"):
            model_ids = self._eligible("model_id", min_cohorts)
            for model_id in model_ids:
                total_rows += build_index(MarketIndex.Scope.MODEL, str(model_id))
                total_scopes += 1
            self.stdout.write(f"model: {len(model_ids)} scope(s)")

        self.stdout.write(self.style.SUCCESS(
            f"Built {total_scopes} index scope(s), {total_rows} point(s) total."
        ))

    @staticmethod
    def _eligible(field: str, min_cohorts: int) -> list:
        """Scope keys with enough distinct cohorts to be worth indexing.

        Counted on the most recent snapshot date only. Each row there is exactly
        one live cohort, so a plain row count is the cohort count — no distinct
        tuple counting needed, and it reflects the scope's *current* breadth
        rather than everything it ever had.
        """
        latest = (
            DailyInventorySnapshot.objects.order_by("-date")
            .values_list("date", flat=True)
            .first()
        )
        if latest is None:
            return []
        rows = (
            DailyInventorySnapshot.objects.filter(
                date=latest, median_price__isnull=False, model_id__isnull=False
            )
            .exclude(**{f"{field}__isnull": True})
            .values(field)
            .annotate(n=Count("id"))
            .filter(n__gte=min_cohorts)
            .values_list(field, flat=True)
        )
        return list(rows)
