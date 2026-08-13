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
BAMA_PAGE_PAUSE = float(os.environ.get("BAMA_PAGE_PAUSE", "0.8"))
BAMA_REQUEST_TIMEOUT = int(os.environ.get("BAMA_REQUEST_TIMEOUT", "20"))
BAMA_COOKIE = os.environ.get("BAMA_COOKIE", "")
# Per-tick fetch size for the background worker. Intentionally small: the worker
# runs every ~5 minutes and only needs the newest pages (where new/changed ads
# land). A full sweep uses `python manage.py fetch_live` (BAMA_MAX_ADS) instead.
BAMA_WORKER_FETCH_ADS = int(os.environ.get("BAMA_WORKER_FETCH_ADS", "500"))

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
