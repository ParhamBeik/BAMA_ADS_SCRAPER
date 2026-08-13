"""Fail FetchRun/JobRun rows left RUNNING after a worker or process restart."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.jobs.services.orphans import reap_orphan_runs


class Command(BaseCommand):
    help = "Mark orphan RUNNING fetch/job rows as failed (interrupted)."

    def handle(self, *args, **options):
        counts = reap_orphan_runs()
        self.stdout.write(
            f"reaped fetch_runs={counts['fetch_runs']} job_runs={counts['job_runs']}"
        )
