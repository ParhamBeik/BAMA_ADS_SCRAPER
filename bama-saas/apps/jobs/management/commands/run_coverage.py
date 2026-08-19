"""Run one rolling-coverage crawl chunk, recorded as a JobRun.

Entry point for deploy/worker/run_coverage.sh. Previously that script called
`manage.py crawl_gaps` directly, so coverage sweeps -- the job ad-removal
detection depends on completing over time -- never appeared in
/api/admin/jobs/overview, only in raw container logs.
"""
from __future__ import annotations

import sys

from django.core.management.base import BaseCommand

from apps.jobs.services.pipeline import _exec_cmd_step


class Command(BaseCommand):
    help = "Run one rolling coverage chunk (crawl_gaps), recorded as a JobRun."

    def add_arguments(self, parser):
        parser.add_argument("--since-hours", type=float, default=24.0)

    def handle(self, *args, **options):
        result = _exec_cmd_step("coverage", "crawl_gaps", since_hours=options["since_hours"])
        line = (
            self.style.SUCCESS(f"coverage: ok ({result.duration_s:.1f}s) {result.detail}")
            if result.ok
            else self.style.ERROR(f"coverage: FAIL ({result.duration_s:.1f}s) {result.detail}")
        )
        self.stdout.write(line)
        if not result.ok:
            sys.exit(1)
