"""Print the same read-only data quality report shown to staff."""

import json

from django.core.management.base import BaseCommand

from apps.core.audit import report
from apps.core.models import Ad


class Command(BaseCommand):
    help = "Read-only Bama database quality report as JSON"

    def add_arguments(self, parser):
        parser.add_argument("--cleanup-preview", action="store_true",
                            help="Compare current rows with a simulated verified catalog")

    def handle(self, *args, **options):
        result = report()
        if options["cleanup_preview"]:
            eligible = Ad.objects.filter(admission_state=Ad.Admission.READY)
            result["cleanup_preview"] = {
                "mode": "read_only_no_deletion",
                "before_active": result["population"]["active"],
                "after_active_catalog": eligible.filter(status=Ad.Status.ACTIVE).count(),
                "excluded_from_catalog": Ad.objects.exclude(
                    admission_state=Ad.Admission.READY).count(),
                "history_rows_preserved": result["population"]["ads"],
            }
        self.stdout.write(json.dumps(result, ensure_ascii=False, default=str, indent=2))
