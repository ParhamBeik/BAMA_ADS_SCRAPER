"""Slow-cadence upkeep: full deal-score rebuild, provenance pruning, health report.

Entry point for deploy/worker/run_maintenance.sh. Previously that script chained
three bare `manage.py` calls in shell, so none of them appeared in
/api/admin/jobs/overview -- only in raw container logs. Each step now records
its own JobRun via the same record_job/_exec_cmd_step machinery run_pipeline
uses, and (matching pipeline.py's "steps are independent" design) a failed step
does not stop the others from running.
"""
from __future__ import annotations

import sys

from django.core.management.base import BaseCommand

from apps.jobs.services.pipeline import StepResult, _deal_scores_step, _exec_cmd_step


class Command(BaseCommand):
    help = "Run maintenance: full deal-score rebuild, prune_history, crawl_health."

    def add_arguments(self, parser):
        parser.add_argument("--prune-days", type=int, default=30)

    def handle(self, *args, **options):
        deal_scores_result = _deal_scores_step(incremental=False)
        prune_result = _exec_cmd_step("prune_history", "prune_history", days=options["prune_days"])
        try:
            health_result = _exec_cmd_step("crawl_health", "crawl_health")
        except SystemExit:
            # crawl_health calls sys.exit(1) by design on a failed check (a
            # report, not a crash -- see its own docstring); _exec_cmd_step only
            # catches Exception, so left alone this would kill the command and
            # make maintenance look like it never ran. record_job already
            # marked the JobRun row FAILED before this executes.
            health_result = StepResult("crawl_health", False, "crawl_health reported a failed check", 0.0)

        for r in (deal_scores_result, prune_result, health_result):
            line = (
                self.style.SUCCESS(f"{r.name}: ok ({r.duration_s:.1f}s) {r.detail}")
                if r.ok
                else self.style.ERROR(f"{r.name}: FAIL ({r.duration_s:.1f}s) {r.detail}")
            )
            self.stdout.write(line)

        # crawl_health deliberately excluded from the exit code: it is a report,
        # not a step, and a red crawler must not make maintenance look as though
        # it failed to run (see deploy/worker/run_maintenance.sh, which this
        # replaces, and crawl_health's own docstring).
        if not (deal_scores_result.ok and prune_result.ok):
            sys.exit(1)
