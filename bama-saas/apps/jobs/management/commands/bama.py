"""``manage.py bama <what>`` — the one entry point for every scheduled job.

``<what>`` is either a cadence (hot / warm / coverage / maintenance / full) or a
single job name (fetch, snapshot, prune, health, …). Exits non-zero if anything
failed, so cron and the worker loop can gate on it.

    manage.py bama hot
    manage.py bama coverage --since-hours 24
    manage.py bama fetch --mode full --max-ads 50000
    manage.py bama health --json
    manage.py bama probe_depth      # where does bama.ir actually stop?
"""

from __future__ import annotations

import json
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.jobs import pipeline


class Command(BaseCommand):
    help = "Run a scheduled cadence (hot/warm/coverage/maintenance/full) or one job."

    def add_arguments(self, parser):
        parser.add_argument(
            "what",
            help=f"cadence ({'/'.join(pipeline.CADENCES)}) or job "
                 f"({'/'.join(pipeline.JOBS)})",
        )
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--skip-fetch", action="store_true",
                            help="Local maintenance only — no network.")
        # Fetch / coverage options.
        parser.add_argument("--mode", choices=["delta", "full", "backfill"], default=None)
        parser.add_argument("--max-ads", type=int, default=None,
                            help=f"Per-tick fetch size (default {settings.BAMA_WORKER_FETCH_ADS}).")
        parser.add_argument("--start-page", type=int, default=None)
        parser.add_argument("--end-page", type=int, default=None)
        parser.add_argument("--page-pause", type=float, default=None)
        parser.add_argument("--request-timeout", type=int, default=None)
        parser.add_argument("--since-hours", type=float, default=None)
        parser.add_argument("--max-pages", type=int, default=None)
        # Job-specific options.
        parser.add_argument("--model", type=int, default=None,
                            help="Restrict a deal-score rebuild to one model pk.")
        parser.add_argument("--days", type=int, default=None,
                            help="prune: retention in days. mark_inactive: wall-clock override.")
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **options):
        what = options["what"]
        given = {k: v for k, v in options.items() if v is not None}

        def opts_for(job: str) -> dict:
            """Only the flags this job actually accepts, and only if given."""
            wanted = {
                "fetch": ("mode", "max_ads", "start_page", "end_page",
                          "page_pause", "request_timeout"),
                "coverage": ("since_hours", "max_pages", "page_pause",
                             "request_timeout", "dry_run"),
                "deal_scores": ("model",),
                "prune": ("days", "dry_run"),
                "mark_inactive": ("days",),
                "episodes": ("limit",),
                "notify": ("dry_run",),
                "alerts": ("dry_run",),
            }.get(job, ())
            picked = {k: given[k] for k in wanted if k in given and given[k] is not False}
            if job == "fetch":
                picked.setdefault("max_ads", settings.BAMA_WORKER_FETCH_ADS)
                picked.setdefault("mode", "delta")
            return picked

        if what in pipeline.CADENCES:
            report = pipeline.run(
                cadence=what,
                skip_fetch=options["skip_fetch"],
                job_opts={job: opts_for(job) for job in pipeline.JOBS},
            )
            for step in report.steps:
                style = self.style.SUCCESS if step.ok else self.style.ERROR
                self.stdout.write(style(
                    f"  {step.name}: {'ok' if step.ok else 'FAIL'} "
                    f"({step.duration_s:.1f}s) {step.detail}"
                ))
            self.stdout.write(report.summary())
            if not report.ok:
                sys.exit(1)
            return

        if what not in pipeline.JOBS:
            raise CommandError(
                f"unknown target {what!r}. Cadences: {', '.join(pipeline.CADENCES)}. "
                f"Jobs: {', '.join(pipeline.JOBS)}."
            )

        # A single job. `health` and `reap_orphans` are reports/housekeeping and
        # run bare; everything else is recorded as a JobRun like a scheduled tick.
        if what in ("health", "reap_orphans", "probe_depth"):
            result = pipeline.JOBS[what]()
            if options["as_json"]:
                self.stdout.write(json.dumps(result, indent=2, default=str))
            elif what == "health":
                for check in result["checks"]:
                    style = self.style.SUCCESS if check["ok"] else self.style.ERROR
                    self.stdout.write(style(
                        f"[{'OK  ' if check['ok'] else 'FAIL'}] {check['name']}: {check['detail']}"
                    ))
            elif what == "probe_depth":
                self.stdout.write(result["detail"])
            else:
                self.stdout.write(str(result))
            # A report of a bad state, not a crash — but the exit code is the
            # point, so cron and CI can gate on it without parsing output.
            if result.get("ok") is False:
                sys.exit(1)
            return

        result = pipeline.run_step(what, **opts_for(what))
        style = self.style.SUCCESS if result.ok else self.style.ERROR
        self.stdout.write(style(
            f"{result.name}: {'ok' if result.ok else 'FAIL'} "
            f"({result.duration_s:.1f}s) {result.detail}"
        ))
        if not result.ok:
            sys.exit(1)
