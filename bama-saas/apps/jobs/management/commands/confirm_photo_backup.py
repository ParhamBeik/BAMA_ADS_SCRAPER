"""Mark locally verified Mac copies without accepting a claim over HTTP."""

import json
import sys

from django.core.management.base import BaseCommand, CommandError

from apps.jobs.local_backup import confirm_manifest


class Command(BaseCommand):
    help = "Read SHA256 and byte-size pairs from stdin, verified by the Mac pull job"

    def handle(self, *args, **options):
        try:
            result = confirm_manifest(sys.stdin)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result))
