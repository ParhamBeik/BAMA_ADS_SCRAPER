"""Shared settings for the Bama SaaS Django project."""

from pathlib import Path
import os

import dj_database_url


BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-dev-key-replace-me-in-production-with-a-long-random-string",
)
DEBUG = False
ALLOWED_HOSTS: list[str] = []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "drf_spectacular",
    "django_filters",
    "corsheaders",
    "apps.accounts",
    "apps.core",
    "apps.jobs",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# This project has exactly ONE database: the Postgres that docker-compose runs,
# published on host port 5433 (5432 inside the container network).
#
# The fallback below is deliberately 5433, NOT the conventional 5432. A native
# PostgreSQL install commonly owns 5432 and may well have a same-named database
# sitting on it; defaulting there means a host-run `manage.py` silently reads
# and writes a different, stale copy of the data while the containers keep using
# the real one. Pointing the fallback at the compose port makes host tools and
# containers agree, and makes a stopped stack fail loudly (connection refused)
# instead of quietly succeeding against the wrong database.
#
# In Docker this is never consulted: compose sets DATABASE_URL explicitly.
DATABASES = {
    "default": dj_database_url.config(
        default=os.environ.get(
            "DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/bama_saas"
        ),
        conn_max_age=600,
        conn_health_checks=True,
    )
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
#
# No authentication classes and no permission gate: this is a single-operator
# tool that runs on one machine behind Docker Compose, never exposed publicly.
# Session auth stays only so the Django admin's browsable API keeps working.

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.AllowAny",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Login gets its own scoped throttle regardless of environment, since it's
    # the one endpoint reachable without a session — a brute-force guard, not
    # a capacity control like the anon/user rates below (those are prod-only).
    "DEFAULT_THROTTLE_RATES": {"login": os.environ.get("THROTTLE_LOGIN", "10/min")},
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Bama SaaS API",
    "DESCRIPTION": "Local market-intelligence REST API for the Iranian used-car "
                   "market (catalog, market analytics, price history, saved ads).",
    "VERSION": "2.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:5173,http://localhost:5174,http://localhost:8080,http://127.0.0.1:8080",
    ).split(",")
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CSRF_TRUSTED_ORIGINS",
        "http://localhost:5173,http://localhost:5174",
    ).split(",")
    if origin.strip()
]

# ---------------------------------------------------------------------------
# Bama scraper / application configuration (read from env)
# ---------------------------------------------------------------------------

BAMA_MAX_ADS = int(os.environ.get("BAMA_MAX_ADS", "50000"))
# 1.5s rather than 0.8s. bama.ir answered 503 to 55 runs and refused the
# connection to 54 more over 39 days; a slower crawl that completes is worth
# more than a fast one whose coverage cannot be proven.
BAMA_PAGE_PAUSE = float(os.environ.get("BAMA_PAGE_PAUSE", "1.5"))
BAMA_REQUEST_TIMEOUT = int(os.environ.get("BAMA_REQUEST_TIMEOUT", "20"))
BAMA_COOKIE = os.environ.get("BAMA_COOKIE", "")
# Per-tick fetch size for the background worker. Intentionally small: the worker
# runs every ~15 minutes and only needs the newest pages (where new/changed ads
# land). Deep coverage comes from the rolling backfill chunk, not from this.
BAMA_WORKER_FETCH_ADS = int(os.environ.get("BAMA_WORKER_FETCH_ADS", "500"))

# Deep-coverage chunk. Each rolling tick walks this many pages further down the
# feed, so full coverage accumulates across many short runs instead of relying
# on one uninterrupted ~936-page sweep (which succeeded 11 times in 28 attempts).
BAMA_COVERAGE_CHUNK_PAGES = int(os.environ.get("BAMA_COVERAGE_CHUNK_PAGES", "120"))

# Episodes that started before this date have untrustworthy end dates and are
# excluded from survival analysis.
#
# Removal used to be detectable only on days a full sweep happened to finish
# (11 of 28 attempts), so listing episodes ended in lumps — 17 distinct days out
# of 39, up to 6,873 endings on one of them, and nothing at all for a week at a
# time. A Kaplan-Meier curve fitted to that reads the sweep schedule, not the
# market: every cohort of every model returned a median of exactly 21.02 days.
#
# The rows are kept for provenance; they are simply not evidence about how long
# cars take to sell. Set this to the date rolling coverage went live.
BAMA_EPISODE_CLEAN_START = os.environ.get("BAMA_EPISODE_CLEAN_START", "2026-08-14")

# Telegram bot token for the deal notifier. The chat id lives in NotifierSettings
# (editable from the deal board); the token is a secret and stays in the env.
# Empty disables sending — the notifier logs and moves on rather than failing.
BAMA_TELEGRAM_TOKEN = os.environ.get("BAMA_TELEGRAM_TOKEN", "")

# ---------------------------------------------------------------------------
# Logging — console handler for the bama.* loggers used by the worker pipeline.
# The cron runner redirects stdout/stderr to logs/cron.log, so a console handler
# is environment-agnostic (no FileHandler dir-creation pitfalls across host/Docker).
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "worker": {
            "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "worker",
        },
    },
    "loggers": {
        "bama": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
