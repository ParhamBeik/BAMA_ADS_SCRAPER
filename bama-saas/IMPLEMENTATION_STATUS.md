# Bama SaaS Implementation Status

Backend stack: **Django 5.2 + DRF + SimpleJWT + PostgreSQL** (Python ≥ 3.11).
PostgreSQL-only (GIN indexes, `django.contrib.postgres`) — SQLite is not
supported. The previous FastAPI + SQLAlchemy + Alembic implementation has been
fully replaced.

## Phase status

### Phase 1 — Foundation & infrastructure ✅
- [x] `docker-compose.yml`: `postgres:16-alpine`, `django` service (runs
  `migrate` then `runserver`, mounts `../bama-scraper/data` read-only at
  `/data`), and a `dev`-profile `pgadmin` service.
- [x] Split settings: `config/settings/{base,dev,prod}.py` (env-driven
  `DATABASE_URL` via `dj-database-url`, JWT, CORS, Bama fetch settings).
- [x] `Dockerfile` (Python 3.12-slim), `requirements.txt`, `manage.py`.
- [x] Normalized schema as Django models across `apps/{accounts,catalog,
  history,market,analytics}` with Django migrations (no Alembic).

### Phase 2 — Auth / users / subscriptions ✅
- [x] Email-based custom `User` (UUID pk, `accounts.User`).
- [x] SimpleJWT auth (access 15 m / refresh 7 d, rotating + blacklisted),
  mounted at `/api/auth/{register,login,refresh,me}/`.
- [x] `Subscription` model (free/pro/enterprise, status, `monthly_api_limit`,
  `api_usage_count`); free-tier `Subscription` auto-created on registration.
- [x] `SubscriptionThrottle` + `MonthlyQuotaThrottle` in
  `apps/accounts/throttles.py`.

### Phase 3 — Data pipeline ✅
- [x] `import_scraped` — bulk-loads `BAMA ADS/**/ads.json` (publish-complete
  only); idempotent for `Ad` / `AdVersion` / `PriceObservation`.
- [x] `import_history` — replays the scraper's `history.db` to build
  price-through-time in global observed-time order.
- [x] `refresh_analytics [--min-count]` — rebuilds `PriceStatistics` per
  (brand, model, variant, year) for `time_window="all"`.
- [x] Shared ingestion pipeline (`apps/jobs/services/ingest.py`): snapshot
  upsert → immutable version (semantic-hash dedup) → per-run observation →
  change-only price → content change events.

### Phase 4 — Live fetcher ✅
- [x] `fetch_live [--max-ads --page-pause --request-timeout]` streams ads
  from bama.ir straight into Postgres via the same `extract_ad →
  parse_publish_time → ingest_ad` pipeline.
- [x] `apps/jobs/services/fetcher.py` mirrors the scraper's HTTP helpers
  (`create_session`, `warmup`, `fetch_page`, `iter_ads`, headers, `SEARCH_URL`,
  banner filter) inline; `KeyboardInterrupt` flushes the `FetchRun` as
  `SUCCEEDED` (partial state already persisted).

### Phase 5 — Analytics ✅
- [x] `analytics/services/truemean.py` — outlier-trimmed (z-score or
  percentile) mean per peer group.
- [x] `analytics/services/bollinger.py` — Bollinger-style spectrum over the
  change-only price series.
- [x] `analytics/services/insights.py` — `liquidity`, `market_depth`,
  `undervalued`, `depreciation` (numpy OLS price-vs-mileage).
- [x] `PriceStatistics` (precomputed) + `AnalyticsCache` models.

### Phase 6 — API endpoints ✅
- [x] Health: `GET /api/health/`, `GET /api/db/health/`.
- [x] Auth: `/api/auth/{register,login,refresh,me}/`.
- [x] Catalog: `/api/brands/`, `/api/brands/<slug>/models/`,
  `/api/models/<pk>/variants/`, `/api/ads/` (django-filter: brand/model/
  variant/city, year/price/mileage ranges, transmission, publish/last-seen
  windows; ordering on price/year/mileage/publish_at/last_seen_at),
  `/api/ads/<code>/`.
- [x] Market analytics: `/api/markets/`, `/api/markets/<id>/{true-mean,
  bollinger,price-trends}/`, `/api/ads/<code>/price-history/`.
- [x] Insights: `/api/insights/<id>/<kind>/` where
  kind ∈ `liquidity|market-depth|undervalued|depreciation`.
- [x] History/provenance: `/api/{changes,observations,fetch-runs}/` and
  per-ad `/api/ads/<code>/{versions,changes,timeline}/`.

### Phase 7 — Testing ✅ / in progress
- [x] `pytest` + `pytest-django` wired in (`pyproject.toml`).
- [x] `tests/test_parsing.py` — pure-Python, no DB; covers the ported
  `apps/parsing` package.
- [x] `tests/test_analytics.py` — analytics services against PostgreSQL.
- [x] `tests/test_importer.py` — ingestion pipeline.
- [ ] Broader DB-backed coverage (per-view filter/pagination, full
  fetch→ingest→API e2e) — incremental.

### Phase 8 — Documentation ✅
- [x] README rewritten for the Django stack (local + Docker quick-start,
  commands, API groups, seeding, tests).
- [x] AGENTS.md updated with current architecture and a dated change-log entry
  for the rewrite.
- [x] IMPLEMENTATION_STATUS.md (this file) reflects reality.

## Known issues / deferred

- **Per-endpoint subscription throttle wiring is pending.**
  `SubscriptionThrottle` and `MonthlyQuotaThrottle` exist in
  `apps/accounts/throttles.py` but are not attached to any view. Today the only
  gating is the global permission class (`AllowAny` in dev, `IsAuthenticated`
  in prod).
- **Frontend is deferred.** The project is backend-first; `frontend/` is an
  empty placeholder.
- **`docker compose` v2 plugin required.** Use `docker compose …` (Compose v2),
  not the legacy `docker-compose` Python binary.
- Celery/Redis are stubbed (commented out in `docker-compose.yml`); live fetch
  is currently a synchronous management command.

## Verification commands

```bash
# Local
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver          # http://localhost:8000/api/ and /admin/

# Docker
docker compose up --build           # add --profile dev for pgadmin on :5050

# Seed + analytics
python manage.py import_scraped
python manage.py import_history
python manage.py refresh_analytics --min-count 3

# Live data
python manage.py fetch_live --max-ads 1000

# Tests
pytest
```

---

**Last updated:** 2026-07-16
**Phases complete:** 1–6, 8
**Phase 7 (testing):** in progress — parsing tests green, DB-backed suites present.
