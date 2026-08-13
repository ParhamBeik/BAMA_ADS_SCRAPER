"""Shared settings for the Bama SaaS Django project."""

from datetime import timedelta
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
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
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
# Django REST Framework + JWT
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "apps.accounts.authentication.CookieJWTAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
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
    "DESCRIPTION": "Market-intelligence REST API for the Iranian used-car market "
                   "(catalog, market analytics, price history, alerts, favorites).",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    # JWT bearer auth in the docs UI.
    "SECURITY": [{"jwtAuth": []}],
    "COMPONENT_SECURITY_SCHEMES": {
        "jwtAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
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

# Cookie auth (Phase 1 frontend rebuild).
AUTH_ACCESS_COOKIE = os.environ.get("AUTH_ACCESS_COOKIE", "bama_access")
AUTH_REFRESH_COOKIE = os.environ.get("AUTH_REFRESH_COOKIE", "bama_refresh")
AUTH_COOKIE_SECURE = os.environ.get("AUTH_COOKIE_SECURE", "false").lower() == "true"
AUTH_COOKIE_SAMESITE = os.environ.get("AUTH_COOKIE_SAMESITE", "Lax")
AUTH_COOKIE_DOMAIN = os.environ.get("AUTH_COOKIE_DOMAIN", "") or None
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5174")
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
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")

# Per-tick fetch size for the background worker. Intentionally small: the worker
# runs every ~5 minutes and only needs the newest pages (where new/changed ads
# land). A full sweep uses `python manage.py fetch_live` (BAMA_MAX_ADS) instead.
BAMA_WORKER_FETCH_ADS = int(os.environ.get("BAMA_WORKER_FETCH_ADS", "500"))

# Default to the project's own data dir; in Docker this is mounted read-only at /data.
BAMA_SCRAPED_DATA_ROOT = os.environ.get(
    "BAMA_SCRAPED_DATA_ROOT", str(BASE_DIR / "data")
)
BAMA_HISTORY_DB_PATH = os.environ.get(
    "BAMA_HISTORY_DB_PATH", str(BASE_DIR / "data" / "bama.db")
)

# ---------------------------------------------------------------------------
# Notification delivery (Phase 5). Email defaults to the console backend so dev
# never opens a real SMTP socket; set EMAIL_BACKEND/EMAIL_HOST in prod. Telegram
# is purely opt-in via TELEGRAM_BOT_TOKEN (empty = Telegram silently skipped).
# ---------------------------------------------------------------------------
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "true").lower() == "true"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@bama.local")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

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
