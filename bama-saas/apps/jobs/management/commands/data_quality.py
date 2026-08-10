"""Record today's data shape and report drift; exit non-zero on an alarm.

Mirrors ``crawl_health``: the exit code is the interface, so the worker loop or
CI can gate on it without parsing output.

    python manage.py data_quality
    python manage.py data_quality --json
"""

from __future__ import annotations

import json
import sys

from django.core.management.base import BaseCommand

from apps.jobs.services.drift import run_drift_check


class Command(BaseCommand):
    help = "Snapshot data-quality metrics and alarm on distribution drift."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        snapshot, alarms = run_drift_check()

        if options["as_json"]:
            self.stdout.write(json.dumps({
                "date": str(snapshot.date),
                "ok": not alarms,
                "active_ads": snapshot.active_ads,
                "alarms": alarms,
                "flag_counts": snapshot.flag_counts,
                "cohort_flag_counts": snapshot.cohort_flag_counts,
            }, indent=2))
        else:
            self.stdout.write(
                f"{snapshot.date}: {snapshot.active_ads} active ad(s), "
                f"{snapshot.unconfirmed_brands} unconfirmed brand(s), "
                f"{snapshot.unconfirmed_models} unconfirmed model(s)"
            )
            for flag, n in sorted(snapshot.flag_counts.items(), key=lambda kv: -kv[1]):
                self.stdout.write(f"  flag {flag}: {n}")
            for flag, n in sorted(snapshot.cohort_flag_counts.items(), key=lambda kv: -kv[1]):
                self.stdout.write(f"  cohort {flag}: {n}")
            if not alarms:
                self.stdout.write(self.style.SUCCESS("no drift detected"))
            for a in alarms:
                self.stdout.write(self.style.ERROR(
                    f"[DRIFT] {a['metric']}: {a['value']} vs baseline {a['baseline']} "
                    f"({a['deviation']}x spread)"
                ))

        if alarms:
            # A report of a bad state, not a crash — same reasoning as crawl_health.
            sys.exit(1)
