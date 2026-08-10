"""Recompute cohort outlier flags.

Runs after ingestion rather than during it: judging a price against its peers
needs the peers, and they are not all in hand until the fetch finishes.

    manage.py flag_cohort_outliers                # every cohort
    manage.py flag_cohort_outliers --model-id 42  # one model
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.jobs.services.verify_cohort import flag_cohort_outliers


class Command(BaseCommand):
    help = "Flag listings priced far from their cohort (median/MAD)."

    def add_arguments(self, parser):
        parser.add_argument("--model-id", type=int, default=None)

    def handle(self, *args, **options):
        result = flag_cohort_outliers(model_id=options["model_id"])
        self.stdout.write(
            self.style.SUCCESS(
                f"{result['cohorts']} cohort(s), {result['scanned']} ad(s) scanned: "
                f"{result['flagged']} flagged, {result['cleared']} cleared"
            )
        )
