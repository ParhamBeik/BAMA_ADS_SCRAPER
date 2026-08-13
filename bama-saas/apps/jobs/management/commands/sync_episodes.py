"""Open and close listing episodes.

Runs after removal marking, because an episode ends when an ad stops being seen —
a conclusion no single observation can reach. Idempotent, and back-fills the
whole history on first run.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.jobs.services.episodes import sync_episodes


class Command(BaseCommand):
    help = "Sync listing episodes."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **options):
        report = sync_episodes(limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(
            f"episodes opened={report.opened} reopened={report.reopened} "
            f"closed={report.closed}"
        ))
