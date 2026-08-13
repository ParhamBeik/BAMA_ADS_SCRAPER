"""Prune aged AdObservation / PageCoverage / JobRun rows."""

from django.core.management.base import BaseCommand

from apps.jobs.services.prune import DEFAULT_DAYS, prune_history


class Command(BaseCommand):
    help = (
        "Delete AdObservation, PageCoverage, and JobRun rows older than --days "
        "(default 90). Keeps coverage for the last two completed sweeps."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        result = prune_history(days=options["days"], dry_run=options["dry_run"])
        prefix = "would delete" if result["dry_run"] else "deleted"
        self.stdout.write(self.style.SUCCESS(
            f"{prefix} observations={result['observations']} "
            f"page_coverage={result['page_coverage']} "
            f"job_runs={result['job_runs']} "
            f"(days={result['days']}, kept_sweeps={result['kept_sweep_runs']})"
        ))
