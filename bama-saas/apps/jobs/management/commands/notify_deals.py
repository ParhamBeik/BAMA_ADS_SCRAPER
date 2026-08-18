"""Send qualifying deals to Telegram. Runs on the hot tick, after deal scores."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.core.services.notify import notify_deals


class Command(BaseCommand):
    help = "Send new deals that clear the notifier thresholds to Telegram."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be sent without sending or recording it.",
        )

    def handle(self, *args, **opts):
        result = notify_deals(dry_run=opts["dry_run"])
        if not result["enabled"]:
            self.stdout.write("Notifier disabled; nothing sent.")
            return
        self.stdout.write(self.style.SUCCESS(
            f"{result['candidates']} candidate(s), {result['sent']} sent"
            + (" (dry run)" if result["dry_run"] else "")
        ))
