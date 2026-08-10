"""Report crawl health; exit non-zero if anything is wrong.

Exit code is the point: the worker loop and any CI/cron wrapper can gate on it
without parsing output. See ``apps/jobs/services/health.py`` for what is checked
and why each check exists.

Usage:
    python manage.py crawl_health            # human-readable, exit 1 on failure
    python manage.py crawl_health --json     # machine-readable
"""

from __future__ import annotations

import json
import sys

from django.core.management.base import BaseCommand

from apps.jobs.services.health import run_checks


class Command(BaseCommand):
    help = "Check crawl health (sweeps, failures, rejects, coverage, ingest)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json", action="store_true", dest="as_json",
            help="Emit JSON instead of text.",
        )

    def handle(self, *args, **options):
        checks = run_checks()
        failed = [c for c in checks if not c.ok]

        if options["as_json"]:
            self.stdout.write(json.dumps({
                "ok": not failed,
                "checks": [
                    {"name": c.name, "ok": c.ok, "detail": c.detail, "data": c.data}
                    for c in checks
                ],
            }, indent=2))
        else:
            for c in checks:
                mark = "OK  " if c.ok else "FAIL"
                style = self.style.SUCCESS if c.ok else self.style.ERROR
                self.stdout.write(style(f"[{mark}] {c.name}: {c.detail}"))
            summary = f"{len(checks) - len(failed)}/{len(checks)} checks passed"
            self.stdout.write(
                self.style.SUCCESS(summary) if not failed
                else self.style.ERROR(summary)
            )

        if failed:
            # SystemExit rather than CommandError: this is a *report* of a bad
            # state, not a crash, and a stack trace would bury the findings.
            sys.exit(1)
