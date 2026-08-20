"""Django settings. One file; ``DJANGO_DEBUG=1`` selects the local profile.

The default is the hardened profile, so a deployed process that forgets to set
anything gets HTTPS redirects, throttling and login-required — a missing env var
must never fail open.
"""

import os
import sys
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def env_list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


# Tests run the permissive profile; a deployed process must opt in explicitly,
# so the default is the hardened one. (pytest-django imports settings long after
# pytest itself, so the module check is reliable — and a production process has
# no reason to have pytest imported.)
DEBUG = os.environ.get("DJANGO_DEBUG") == "1" or "pytest" in sys.modules

SECRET_KEY = os.environ.get(
    "SECRET_KEY", "django-insecure-dev-key-replace-me-in-production"
)
ALLOWED_HOSTS = ["*"] if DEBUG else env_list("ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
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
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

# Exactly ONE database. The fallback is port 5433, NOT the conventional 5432: a
# native PostgreSQL install commonly owns 5432 and may have a same-named
# database on it, so defaulting there means a host-run `manage.py` silently
# reads and writes a stale copy while the containers use the real one. Pointing
# at the compose port makes a stopped stack fail loudly instead.
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
    {"NAME": f"django.contrib.auth.password_validation.{name}"}
    for name in ("UserAttributeSimilarityValidator", "MinimumLengthValidator",
                 "CommonPasswordValidator", "NumericPasswordValidator")
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Seed logins, created/updated on every startup by `manage.py ensure_seed_users`.
DEV_ADMIN_EMAIL = os.environ.get("DEV_ADMIN_EMAIL", "admin@bama.local" if DEBUG else "")
DEV_ADMIN_PASSWORD = os.environ.get("DEV_ADMIN_PASSWORD", "LocalOps-2026" if DEBUG else "")
DEMO_USER_EMAIL = os.environ.get("DEMO_USER_EMAIL", "demo@bama.local" if DEBUG else "")
DEMO_USER_PASSWORD = os.environ.get("DEMO_USER_PASSWORD", "LocalOps-2026-demo" if DEBUG else "")

# ---------------------------------------------------------------------------
# DRF
# ---------------------------------------------------------------------------
#
# Session auth protects the deployed SPA. Development keeps the catalog
# permissive for local tooling; a deployed instance requires a login, and
# operator endpoints add their own IsAdminUser at the view boundary.
#
# Read endpoints were entirely unthrottled while several of them aggregate tens
# of thousands of rows per call — a single scraper could saturate the database.
# Rates are off under DEBUG or a fast test run trips its own limiter.

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("rest_framework.authentication.SessionAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.AllowAny" if DEBUG
        else "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    # Auth endpoints get scoped throttles even in dev: they are reachable
    # without a session and need a brute-force guard.
    "DEFAULT_THROTTLE_RATES": {
        "login": os.environ.get("THROTTLE_LOGIN", "10/min"),
        "register": os.environ.get("THROTTLE_REGISTER", "5/min"),
    },
}

if not DEBUG:
    REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    )
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"].update({
        "anon": os.environ.get("THROTTLE_ANON", "60/min"),
        "user": os.environ.get("THROTTLE_USER", "600/min"),
    })

    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:5174,http://localhost:8080,http://127.0.0.1:8080",
)
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS", "http://localhost:5173,http://localhost:5174"
)

# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------

BAMA_MAX_ADS = int(os.environ.get("BAMA_MAX_ADS", "50000"))
# 1.5s, not 0.8s: bama.ir answered 503 to 55 runs and refused the connection to
# 54 more over 39 days. A slower crawl that completes is worth more than a fast
# one whose coverage cannot be proven.
BAMA_PAGE_PAUSE = float(os.environ.get("BAMA_PAGE_PAUSE", "1.5"))
BAMA_REQUEST_TIMEOUT = int(os.environ.get("BAMA_REQUEST_TIMEOUT", "20"))
BAMA_COOKIE = os.environ.get("BAMA_COOKIE", "")

# Per-tick fetch size. Intentionally small: the worker runs every ~15 minutes and
# only needs the newest pages. Deep coverage comes from the rolling chunk below.
BAMA_WORKER_FETCH_ADS = int(os.environ.get("BAMA_WORKER_FETCH_ADS", "500"))
# How far down the feed each rolling coverage tick walks, so full coverage
# accumulates across many short runs instead of relying on one ~936-page sweep.
BAMA_COVERAGE_CHUNK_PAGES = int(os.environ.get("BAMA_COVERAGE_CHUNK_PAGES", "120"))

# Episodes that started before this date have untrustworthy end dates and are
# excluded from survival analysis. Set it to the date rolling coverage went live.
BAMA_EPISODE_CLEAN_START = os.environ.get("BAMA_EPISODE_CLEAN_START", "2026-08-14")

# Telegram bot token for the deal notifier. The chat id lives in NotifierSettings
# (editable from the deal board); the token is a secret and stays in the env.
# Empty disables sending — the notifier logs and moves on rather than failing.
BAMA_TELEGRAM_TOKEN = os.environ.get("BAMA_TELEGRAM_TOKEN", "")

# Console handler only: the worker's stdout is the log, which is
# environment-agnostic across host and Docker.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"worker": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "worker"}},
    "loggers": {"bama": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}
