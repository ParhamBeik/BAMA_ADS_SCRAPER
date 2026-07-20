"""Run every enabled Alert and deliver the resulting Notifications.

Thin wrapper around ``apps.accounts.alerts.evaluate_alerts`` so the job can
be scheduled by the worker pipeline or run by hand for debugging.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.accounts.alerts import evaluate_alerts


class Command(BaseCommand):
    help = "Evaluate enabled alerts and deliver resulting notifications."

    def handle(self, *args, **options):
        summary = evaluate_alerts()
        self.stdout.write(self.style.SUCCESS(
            f"Alerts evaluated: {summary['alerts']} | "
            f"notifications: {summary['notifications']} | "
            f"delivered: {summary['delivered']}."
        ))
