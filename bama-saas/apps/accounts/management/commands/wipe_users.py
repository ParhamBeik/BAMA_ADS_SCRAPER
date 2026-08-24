"""Delete every account. One-time reset before the first real signup.

Deliberately a separate command rather than part of any startup path: this is
irreversible, and the thing it replaces (`ensure_seed_users`, which ran on every
container start) is exactly the sort of automatic account management this
codebase should not have.

Favorites cascade with their owner. Nothing in the catalog references a user, so
crawl data and analytics are untouched.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


class Command(BaseCommand):
    help = "Delete all user accounts (and their saved ads). Requires --yes."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true",
                            help="Required. Confirms the deletion is intended.")

    def handle(self, *args, **options):
        total = User.objects.count()
        if not options["yes"]:
            raise CommandError(
                f"This would delete {total} account(s) and every saved ad they own. "
                f"Re-run with --yes if that is what you want."
            )
        _, per_model = User.objects.all().delete()
        self.stdout.write(self.style.SUCCESS(
            f"deleted {total} account(s): {per_model or 'nothing else'}"
        ))
        self.stdout.write(
            "Signup stays open for regular users. Staff is restored with "
            "createsuperuser inside the container, not by signing up again."
        )
