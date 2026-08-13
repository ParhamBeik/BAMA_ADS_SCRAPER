"""Create or update the local staff login. DEBUG only."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

User = get_user_model()


class Command(BaseCommand):
    help = "Ensure a verified staff user exists (DEBUG / local compose only)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("ensure_dev_admin refuses to run when DEBUG is False.")

        email = (getattr(settings, "DEV_ADMIN_EMAIL", "") or "").strip()
        password = getattr(settings, "DEV_ADMIN_PASSWORD", "") or ""
        if not email or not password:
            raise CommandError(
                "DEV_ADMIN_EMAIL and DEV_ADMIN_PASSWORD must be set."
            )

        email = User.objects.normalize_email(email)
        user, created = User.objects.get_or_create(
            email=email,
            defaults={"is_staff": True, "is_superuser": True},
        )
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.email_verified_at = user.email_verified_at or timezone.now()
        user.set_password(password)
        user.save()
        action = "created" if created else "updated"
        self.stdout.write(self.style.SUCCESS(f"{action} staff user {email}"))
