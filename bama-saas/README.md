# Bama SaaS Backend

Django 5.2 / DRF / PostgreSQL service for Bama car-listing data: a normalized
catalog, append-only provenance, change-only price-through-time, and derived
market analytics (true-mean, Bollinger, liquidity, depreciation). The schema
uses PostgreSQL-specific features (`django.contrib.postgres`, GIN indexes), so
**SQLite is not supported**.

## Stack

Django 5.2, djangorestframework, djangorestframework-simplejwt (JWT auth +
token blacklist), django-filter, django-cors-headers, dj-database-url,
`psycopg[binary]` (PostgreSQL), numpy, jdatetime, requests, gunicorn. Requires
**Python ≥ 3.11**.

## Layout

```
bama-saas/
├── config/                 project package (settings.base/dev/prod, urls, wsgi, asgi)
├── apps/
│   ├── accounts/           email-based User, Subscription; SimpleJWT auth
│   ├── catalog/            Brand→Model→Variant, City, Dealer, Ad (current snapshot)
│   ├── history/            FetchRun, AdVersion, AdObservation, AdChangeEvent, AuditRun
│   ├── market/             PriceObservation (change-only price-through-time)
│   ├── analytics/          PriceStatistics, AnalyticsCache; insight services
│   ├── jobs/               management commands + ingestion services (no models)
│   └── parsing/            zero-Django pure-Python Bama payload rules (ported)
├── tests/                  pytest-django: test_parsing, test_analytics, test_importer
├── manage.py
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Local setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL='postgresql://postgres:postgres@localhost:5432/bama_saas'  # optional; this is the default
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open <http://localhost:8000/api/> for the API and <http://localhost:8000/admin/>
for the Django admin. `manage.py` defaults to `config.settings.dev`, which sets
`DEFAULT_PERMISSION_CLASSES = [AllowAny]` so read endpoints are open locally;
`config.settings.prod` keeps the `IsAuthenticated` default.

## Docker

Requires the Compose v2 plugin (`docker compose …`). From `bama-saas/`:

```bash
docker compose up --build          # add --profile dev to also start pgadmin on :5050
```

Compose starts `postgres:16-alpine` and a `django` service that runs
`migrate --noinput` then `runserver`. It mounts the sibling scraper data
read-only at `/data` (`../bama-scraper/data:/data:ro`) and sets
`BAMA_SCRAPED_DATA_ROOT=/data`.

## Management commands

All commands run via `python manage.py <command>`.

| Command | Description |
| --- | --- |
| `migrate` | Apply Django migrations. |
| `createsuperuser` | Create an admin (email-based) user. |
| `runserver` | Dev server on `:8000`. |
| `import_scraped [--root --limit --batch-size]` | Bulk-load publish-complete ads from `BAMA ADS/**/ads.json` under `BAMA_SCRAPED_DATA_ROOT` (~1,560 files / ~49.6k ads). Idempotent for Ad / AdVersion / PriceObservation. |
| `import_history [--db --limit --batch-size]` | Replay the scraper's `history.db` (`BAMA_HISTORY_DB_PATH`) into the append-only schema to build price-through-time. |
| `refresh_analytics [--min-count]` | Rebuild `PriceStatistics` per (brand, model, variant, year) for `time_window="all"`. |
| `fetch_live [--max-ads --page-pause --request-timeout]` | Stream live ads from bama.ir straight into Postgres via the same ingest pipeline as `import_scraped`. Defaults come from `BAMA_MAX_ADS` / `BAMA_PAGE_PAUSE` / `BAMA_REQUEST_TIMEOUT`. |

### Seeding data

If the sibling `bama-scraper/data/` exists:

```bash
python manage.py import_scraped                  # current snapshot from scraped JSON
python manage.py import_history                  # price-through-time from scraper's SQLite
python manage.py refresh_analytics --min-count 3 # precompute landing-page stats
```

For fresh data, run `python manage.py fetch_live --max-ads 1000` instead.

## API surface

All routes are mounted under `/api/` (see `config/urls.py`).

**Health**
- `GET /api/health/`, `GET /api/db/health/`

**Auth** (`/api/auth/`) — SimpleJWT (access 15 m / refresh 7 d, rotating + blacklisted). Registration auto-creates a free `Subscription`.
- `POST /api/auth/register/`, `POST /api/auth/login/`, `POST /api/auth/refresh/`, `GET /api/auth/me/`

**Catalog** (`/api/`)
- `GET /api/brands/`, `GET /api/brands/<slug>/`
- `GET /api/brands/<slug>/models/`
- `GET /api/models/<pk>/variants/`
- `GET /api/ads/` — filterable list (`?brand=<slug>&model=<id>&variant=<id>&year_min=&year_max=&price_min=&price_max=&mileage_max=&transmission=&publish_from=&last_seen_from=`); only publish-complete, priced ads. Supports `?ordering=` on `current_price, year, mileage, publish_at, last_seen_at`.
- `GET /api/ads/<code>/` — any ad by code (no publish-complete restriction).

**Market analytics** (`/api/`) — keyed by `<int:model_id>` because Bama model names are Persian; the brand is implied.
- `GET /api/markets/` — per-model summary (top-N by ad count): `?limit=` (≤500).
- `GET /api/markets/<model_id>/true-mean/?variant=&year=&method=zscore|percentile&z=2.0`
- `GET /api/markets/<model_id>/bollinger/?variant=&window=20&sigma=2.0`
- `GET /api/markets/<model_id>/price-trends/?variant=&bucket=day|week|month`
- `GET /api/ads/<code>/price-history/` — single ad's change-only price series.

**Insights** (`/api/`) — kind ∈ `liquidity | market-depth | undervalued | depreciation`:
- `GET /api/insights/<model_id>/<kind>/?variant=&year=`

**History / provenance** (`/api/`)
- Cross-ad: `GET /api/changes/`, `GET /api/observations/`, `GET /api/fetch-runs/` (plus `/<id>/` detail).
- Per-ad: `GET /api/ads/<code>/versions/`, `GET /api/ads/<code>/changes/`, `GET /api/ads/<code>/timeline/` (merged observations + change events, newest first).

### Example calls

```bash
# Markets landing
curl -s http://localhost:8000/api/markets/?limit=20 | jq

# True-mean for model 7 (z-score trim)
curl -s 'http://localhost:8000/api/markets/7/true-mean/?year=1401&method=zscore&z=2.0' | jq

# Undervalued listings for model 7, year 1401
curl -s 'http://localhost:8000/api/insights/7/undervalued/?year=1401' | jq

# Login (dev allows anonymous reads; login needed for /me and prod)
curl -s -X POST http://localhost:8000/api/auth/login/ \
  -H 'Content-Type: application/json' \
  -d '{"email":"me@example.com","password":"secret"}' | jq
```

## Auth & throttling

- `dev.py`: read endpoints are `AllowAny`; `prod.py` keeps the global `IsAuthenticated` default.
- `apps/accounts/throttles.py` ships `SubscriptionThrottle` (burst/min scaled by plan) and `MonthlyQuotaThrottle` (hard monthly cap from `Subscription.monthly_api_limit`, atomic increment). **These are not yet wired to specific views** — per-endpoint tier gating is pending.

## Tests

```bash
pytest
```

`tests/test_parsing.py` is pure-Python (no DB); `tests/test_analytics.py` and
`tests/test_importer.py` exercise the analytics services and the import pipeline
against PostgreSQL.
