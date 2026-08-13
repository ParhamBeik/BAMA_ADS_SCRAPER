"""Development settings."""

import os

from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Seeded superuser for local compose / `ensure_dev_admin`. This is the whole
# account system now: Django admin is the only thing that authenticates.
DEV_ADMIN_EMAIL = os.environ.get("DEV_ADMIN_EMAIL", "admin@bama.local")
DEV_ADMIN_PASSWORD = os.environ.get("DEV_ADMIN_PASSWORD", "LocalOps-2026")
