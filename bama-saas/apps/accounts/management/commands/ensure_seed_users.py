"""Create or update the admin and demo logins. Safe to run in prod.

Runs on every container start. ``createsuperuser --noinput`` only *creates*: on
a redeploy with a changed password it fails silently on the existing user and
the old password keeps working. get_or_create + set_password fixes that.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


def upsert(email: str, password: str, *, staff: bool, demo: bool) -> str:
    email = User.objects.normalize_email(email)
    user, created = User.objects.get_or_create(
        email=email, defaults={"is_staff": staff, "is_superuser": staff, "is_demo": demo},
    )
    user.is_staff = user.is_superuser = staff
    user.is_demo = demo
    user.is_active = True
    user.set_password(password)
    user.save()
    return "created" if created else "updated"


class Command(BaseCommand):
    help = "Ensure the admin and demo users exist with env-configured passwords."

    def handle(self, *args, **options):
        admin_email = (settings.DEV_ADMIN_EMAIL or "").strip()
        admin_password = settings.DEV_ADMIN_PASSWORD or ""
        if not admin_email or not admin_password:
            raise CommandError("DEV_ADMIN_EMAIL and DEV_ADMIN_PASSWORD must be set.")
        action = upsert(admin_email, admin_password, staff=True, demo=False)
        self.stdout.write(self.style.SUCCESS(f"{action} admin user {admin_email}"))

        demo_email = (settings.DEMO_USER_EMAIL or "").strip()
        demo_password = settings.DEMO_USER_PASSWORD or ""
        if not demo_email or not demo_password:
            self.stdout.write("DEMO_USER_* not set — skipping demo user.")
            return
        if User.objects.normalize_email(demo_email) == User.objects.normalize_email(admin_email):
            raise CommandError("DEMO_USER_EMAIL must differ from DEV_ADMIN_EMAIL.")
        action = upsert(demo_email, demo_password, staff=False, demo=True)
        self.stdout.write(self.style.SUCCESS(f"{action} demo user {demo_email}"))
