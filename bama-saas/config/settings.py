"""Django settings. One file; ``DJANGO_DEBUG=1`` selects the local profile.

The default is the hardened profile, so a deployed process that forgets to set
anything gets HTTPS redirects, throttling and login-required — a missing env var
must never fail open.
"""

import os
from datetime import timedelta
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def env_list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


DEBUG = os.environ.get("DJANGO_DEBUG") == "1"

DEV_SECRET_KEY = "django-insecure-dev-key-replace-me-in-production"
SECRET_KEY = os.environ.get("SECRET_KEY", DEV_SECRET_KEY)
if not DEBUG and SECRET_KEY == DEV_SECRET_KEY:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured("SECRET_KEY must be set when DJANGO_DEBUG is off")
API_PUBLIC_READS = os.environ.get("API_PUBLIC_READS") == "1"
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
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
    "apps.accounts",
    "apps.core",
    "apps.jobs",
    "apps.ml",
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

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
#
# Redis holds two things: the proxied listing photos (bama.ir's CDN blocks us
# periodically, and a card grid of broken images is the failure users actually
# see) and the deal board's computed window. Both are pure derived data — losing
# the whole cache costs one recomputation and some re-fetching, never a fact.
#
# LocMem when REDIS_URL is empty, so pytest and a Redis-less host still run.
# django.core.cache.backends.redis is built in; no django-redis dependency.

REDIS_URL = os.environ.get("REDIS_URL", "" if DEBUG else "redis://redis:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    } if REDIS_URL else {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "bama-fallback",
    }
}

# How long a proxied photo stays cached. Bama's image URLs are content-addressed
# (a GUID per upload), so a stale entry is not a risk — the URL changes when the
# picture does.
IMAGE_CACHE_SECONDS = int(os.environ.get("IMAGE_CACHE_SECONDS", 60 * 60 * 24 * 30))
# Listing photos run ~40-120KB. Anything past this is not a car photo and must
# not be pulled into the cache.
IMAGE_MAX_BYTES = int(os.environ.get("IMAGE_MAX_BYTES", 2 * 1024 * 1024))

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
    # Session first: the browser SPA is the primary client and its cookie is
    # HttpOnly, so script cannot read it. JWT is second and exists for API
    # clients with nowhere to keep a cookie — the SPA never sends a bearer token.
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.AllowAny" if API_PUBLIC_READS
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

# ---------------------------------------------------------------------------
# Sessions (the browser) and JWT (everything else)
# ---------------------------------------------------------------------------
#
# django.contrib.auth.login() cycles the session key on every login, so session
# fixation is already covered. What was missing is expiry and an explicit
# SameSite: a session that never ages out is a permanent credential.

SESSION_COOKIE_HTTPONLY = True
# Lax, not Strict: Strict drops the cookie on any cross-site navigation, so
# following a link to a listing would land the user on the login screen.
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# The SPA must read this one to echo it back as X-CSRFToken, so it cannot be
# HttpOnly. That is the standard double-submit trade and is why the *session*
# cookie being HttpOnly is what matters.
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_AGE = int(os.environ.get("SESSION_COOKIE_AGE", 60 * 60 * 24 * 14))
# Slides the expiry on activity, so the age above is an idle timeout rather than
# a hard logout mid-session.
SESSION_SAVE_EVERY_REQUEST = True

SIMPLE_JWT = {
    # Short access token, because a bearer token cannot be revoked before it
    # expires; the refresh token is the thing with a kill switch.
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    # Rotate and blacklist: a stolen refresh token is usable once, and the moment
    # the real client refreshes, the thief's copy is dead.
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
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

# Per-tick fetch ceiling — a backstop, NOT the intended stop. The delta run is
# meant to end on `max_stale_pages` consecutive pages carrying nothing new, so
# that a busy feed is followed as deep as it is still moving and a quiet one
# costs almost nothing.
#
# At 500 it was not a backstop, it was the routine stop: over 7 days production
# ended 365 delta runs on `max_ads` at exactly 17 pages against 228 on
# `stale_pages` at ~8.8. That is the saturation rule being pre-empted 62% of the
# time — the crawl walking away from a feed that was still yielding new ads.
#
# 1500 (50 pages) sits well past where a quiet feed saturates while bounding a
# busy tick to ~2.5 min, comfortably inside the ~15 min cadence. Raise it if
# runs still stop on `max_ads` often; lower it if bama.ir starts refusing.
BAMA_WORKER_FETCH_ADS = int(os.environ.get("BAMA_WORKER_FETCH_ADS", "1500"))
# How far down the feed each rolling coverage tick walks, so full coverage
# accumulates across many short runs instead of relying on one ~936-page sweep.
BAMA_COVERAGE_CHUNK_PAGES = int(os.environ.get("BAMA_COVERAGE_CHUNK_PAGES", "120"))

# Episodes that started before this date have untrustworthy end dates and are
# excluded from survival analysis. Set it to the date rolling coverage went live.
BAMA_EPISODE_CLEAN_START = os.environ.get("BAMA_EPISODE_CLEAN_START", "2026-08-14")

# Where trained model artifacts are written and read. The `ml` service mounts
# this volume read-write and every other service mounts it read-only: exactly one
# process may write a model, and a web worker that could overwrite an artifact
# is a web worker that can change what every reader is told without a deploy.
ML_ARTIFACT_DIR = Path(os.environ.get("ML_ARTIFACT_DIR", BASE_DIR / "data" / "ml"))

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
