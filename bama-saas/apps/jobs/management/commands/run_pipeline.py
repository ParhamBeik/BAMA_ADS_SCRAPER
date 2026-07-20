"""Run the full worker data pipeline (the scheduled 5-minute tick).

Orchestrates, in order: live fetch → mark stale ads REMOVED → refresh today's
inventory snapshot → rebuild analytics. Each step is a call to an existing
command; failures are isolated and logged. The fetch is retried with exponential
backoff.

This is the entry point the cron-guarded runner (deploy/worker/run_pipeline.sh)
invokes. Exits non-zero if any step failed, so cron / monitoring can flag a bad
tick.

Flags exist for testability: ``--skip-fetch`` runs only the cheap local steps
(no network), and ``--steps`` runs an explicit subset.
"""

from __future__ import annotations

import sys

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.jobs.services.pipeline import STEP_ORDER, run_pipeline


class Command(BaseCommand):
    help = "Run the scheduled data pipeline (fetch + maintain + snapshot + analytics)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-fetch",
            action="store_true",
            help="Skip the live fetch — run only local maintenance steps (no network).",
        )
        parser.add_argument(
            "--max-ads",
            type=int,
            default=settings.BAMA_WORKER_FETCH_ADS,
            help="Per-tick fetch size (default: BAMA_WORKER_FETCH_ADS). "
                 "Intentionally small — the tick runs every ~5 minutes.",
        )
        parser.add_argument(
            "--steps",
            type=str,
            default="",
            help="Comma-separated subset to run, e.g. 'mark_inactive,daily_snapshot'. "
                 f"Choices: {','.join(STEP_ORDER)}.",
        )
        parser.add_argument(
            "--fetch-attempts", type=int, default=3,
            help="Retry attempts for the network fetch (default: 3).",
        )
        parser.add_argument(
            "--fetch-retry-delay", type=float, default=5.0,
            help="Base seconds between fetch retries, doubled each attempt (default: 5).",
        )

    def handle(self, *args, **options):
        steps = None
        if options["steps"]:
            steps = {s.strip() for s in options["steps"].split(",") if s.strip()}
            unknown = steps - set(STEP_ORDER)
            if unknown:
                self.stderr.write(self.style.ERROR(
                    f"Unknown step(s): {', '.join(sorted(unknown))}. "
                    f"Choices: {', '.join(STEP_ORDER)}"
                ))
                sys.exit(2)

        report = run_pipeline(
            fetch=not options["skip_fetch"],
            fetch_max_ads=options["max_ads"],
            steps=steps,
            fetch_attempts=options["fetch_attempts"],
            fetch_retry_delay=options["fetch_retry_delay"],
        )

        for s in report.steps:
            line = self.style.SUCCESS(f"  {s.name}: ok ({s.duration_s:.1f}s)") if s.ok \
                else self.style.ERROR(f"  {s.name}: FAIL ({s.duration_s:.1f}s) {s.detail}")
            self.stdout.write(line)

        self.stdout.write(report.summary())
        if not report.ok:
            sys.exit(1)
