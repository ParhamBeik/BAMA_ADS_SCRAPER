"""Development settings."""

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
