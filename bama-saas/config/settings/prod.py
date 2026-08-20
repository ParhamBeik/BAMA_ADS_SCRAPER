"""Production settings."""

import os

from .base import *  # noqa: F401,F403

DEBUG = False
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]

# Consumed by `ensure_seed_users` (see docker-compose.prod.yml's django command).
DEV_ADMIN_EMAIL = os.environ.get("DEV_ADMIN_EMAIL", "")
DEV_ADMIN_PASSWORD = os.environ.get("DEV_ADMIN_PASSWORD", "")
DEMO_USER_EMAIL = os.environ.get("DEMO_USER_EMAIL", "")
DEMO_USER_PASSWORD = os.environ.get("DEMO_USER_PASSWORD", "")

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# Read endpoints are unauthenticated-readable in places and were entirely
# unthrottled, while several of them (markets, rankings, insights) aggregate
# tens of thousands of rows per call. A single scraper could trivially saturate
# the database. Rates live here rather than in base.py deliberately: dev and the
# test suite must stay unthrottled, or a fast test run trips its own limiter.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        **REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],  # noqa: F405 — keeps "login" from base.py
        "anon": os.environ.get("THROTTLE_ANON", "60/min"),
        "user": os.environ.get("THROTTLE_USER", "600/min"),
    },
    # A deployed instance requires a logged-in Django session. Operator endpoints
    # add an explicit IsAdminUser permission at the view boundary.
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
}
