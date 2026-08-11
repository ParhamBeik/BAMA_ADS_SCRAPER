"""Development settings."""

import os

from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Phase 2: read endpoints are open locally so analytics can be exercised before
# auth lands. prod.py keeps the IsAuthenticated default and Phase 3 re-adds
# subscription gating on the pro/admin endpoints.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.AllowAny",),
}

# Local email goes to Mailpit when the compose stack is running.
if not os.environ.get("EMAIL_BACKEND"):  # noqa: F405
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"  # noqa: F405
    EMAIL_HOST = "localhost"  # noqa: F405
    EMAIL_PORT = 1025  # noqa: F405
    EMAIL_USE_TLS = False  # noqa: F405
