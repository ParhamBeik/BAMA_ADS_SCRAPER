"""Create or update the admin and demo logins. Safe to run in prod.

Entry point for docker-compose.prod.yml's django startup command, replacing
`createsuperuser --noinput || true`. That command only *creates*: on a redeploy
where DEV_ADMIN_PASSWORD changed, createsuperuser fails silently on the
already-existing user and the old password keeps working. get_or_create +
set_password on every run fixes that — same idempotent shape as the
DEBUG-only ensure_dev_admin, minus the DEBUG guard.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


def _upsert(email: str, password: str, *, is_staff: bool, is_superuser: bool, is_demo: bool) -> str:
    email = User.objects.normalize_email(email)
    user, created = User.objects.get_or_create(
        email=email,
        defaults={"is_staff": is_staff, "is_superuser": is_superuser, "is_demo": is_demo},
    )
    user.is_staff = is_staff
    user.is_superuser = is_superuser
    user.is_demo = is_demo
    user.is_active = True
    user.set_password(password)
    user.save()
    return "created" if created else "updated"


class Command(BaseCommand):
    help = "Ensure the admin and normal demo users exist with env-configured passwords."

    def handle(self, *args, **options):
        admin_email = (getattr(settings, "DEV_ADMIN_EMAIL", "") or "").strip()
        admin_password = getattr(settings, "DEV_ADMIN_PASSWORD", "") or ""
        if not admin_email or not admin_password:
            raise CommandError("DEV_ADMIN_EMAIL and DEV_ADMIN_PASSWORD must be set.")
        action = _upsert(
            admin_email, admin_password, is_staff=True, is_superuser=True, is_demo=False
        )
        self.stdout.write(self.style.SUCCESS(f"{action} admin user {admin_email}"))

        demo_email = (getattr(settings, "DEMO_USER_EMAIL", "") or "").strip()
        demo_password = getattr(settings, "DEMO_USER_PASSWORD", "") or ""
        if not demo_email or not demo_password:
            self.stdout.write("DEMO_USER_EMAIL/DEMO_USER_PASSWORD not set — skipping demo user.")
            return
        if User.objects.normalize_email(demo_email) == User.objects.normalize_email(admin_email):
            raise CommandError("DEMO_USER_EMAIL must differ from DEV_ADMIN_EMAIL.")
        action = _upsert(
            demo_email, demo_password, is_staff=False, is_superuser=False, is_demo=True
        )
        self.stdout.write(self.style.SUCCESS(f"{action} demo user {demo_email}"))
