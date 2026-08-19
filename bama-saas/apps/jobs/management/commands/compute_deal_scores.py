"""Refresh per-ad deal scores (DealScoreCache).

Idempotent: a full refresh drops every DealScoreCache row and rebuilds; a
per-model refresh (``--model``) drops only that model's ads' rows. See
``apps.core.services.deal_score`` for the scoring formula.

Usage:
    python manage.py compute_deal_scores [--model <pk>]
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.core.services.deal_score import compute_deal_scores


class Command(BaseCommand):
    help = "Refresh per-ad deal scores (discount below the cohort's fair value)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--model", type=int, default=None,
            help="Restrict the refresh to a single model pk (default: all models).",
        )

    def handle(self, *args, **options):
        result = compute_deal_scores(model_id=options["model"])
        self.stdout.write(self.style.SUCCESS(
            f"Scored {result['scored']} ad(s) "
            f"(min_peers={result['min_peers']}, model_id={result['model_id']}, "
            f"outliers_flagged={result['outliers_flagged']}, "
            f"outliers_cleared={result['outliers_cleared']})."
        ))
