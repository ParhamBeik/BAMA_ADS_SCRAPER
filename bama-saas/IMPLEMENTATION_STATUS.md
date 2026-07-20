# Bama SaaS Implementation Status

Backend stack: **Django 5.2 + DRF + SimpleJWT + drf-spectacular + PostgreSQL**
(Python ≥ 3.11). PostgreSQL-only (GIN indexes, `django.contrib.postgres`) —
SQLite is not supported. The previous FastAPI + SQLAlchemy + Alembic
implementation has been fully replaced.

The work below is split into **Foundation** (pre-existing infrastructure,
shipped before the MVP-completion pass) and **MVP completion** (the four phases
delivered on top of it — database completion, analytics engine, premium
features, and the finished OpenAPI-documented REST surface).

## Foundation (pre-existing) ✅

- **Infra:** `docker-compose.yml` (`postgres:16-alpine` + `django` + `dev`-profile
  `pgadmin`); split settings `config/settings/{base,dev,prod}.py`; `Dockerfile`,
  `requirements.txt`, `manage.py`.
- **Auth/users:** email-based custom `User` (UUID pk); SimpleJWT (access 15 m /
  refresh 7 d, rotating + blacklisted) at `/api/auth/{register,login,refresh,me}/`;
  `Subscription` (free/pro/enterprise, `monthly_api_limit`, `api_usage_count`)
  auto-created on registration; `SubscriptionThrottle` + `MonthlyQuotaThrottle`.
- **Catalog:** normalized `Brand→Model→Variant→City→Dealer→Ad` (Ad PK is `code`;
  `raw_payload` JSONB + GIN index).
- **Provenance (append-only):** `FetchRun`, `AdVersion` (semantic-hash dedup),
  `AdObservation`, `AdChangeEvent`, `AuditRun`.
- **Price-through-time (change-only):** `PriceObservation` (per ad), plus
  `payment` / `prepayment` / `installments` / `price_type`.
- **Ingestion:** shared `apps/jobs/services/ingest.py` (snapshot upsert → immutable
  version → per-run observation → change-only price → content change events);
  `import_scraped`, `import_history`, `fetch_live`, `refresh_analytics`.
- **Baseline analytics:** `truemean` (z-score/percentile outlier trim), `bollinger`
  (SMA bands), `insights` (liquidity / market_depth / undervalued / depreciation);
  `PriceStatistics` (precomputed) + `AnalyticsCache`.

## MVP completion

### Phase 3 — Database completion ✅

Added the tables the product needs without disturbing the existing schema (no
duplicates of existing models). New migrations: `catalog/0003`,
`analytics/0002`, plus the accounts-engagement migration.

- **`catalog.Ad`** — `status` (`ACTIVE`/`REMOVED`) + `removed_at`; `mark_inactive_ads`
  flips ads not seen for N hours to `REMOVED` and stamps `removed_at`.
- **`analytics.DailyInventorySnapshot`** — per-day active/new/removed counts +
  price spread (written by `daily_snapshot`).
- **`analytics.MarketSnapshot`** — per-day market-wide rollup + brand breakdown
  (written by `market_snapshot`).
- **`analytics.DealScoreCache`** — per-ad deal score, discount %, peer median,
  components (one-to-one with `Ad`; written by `compute_deal_scores`).
- **`market.PriceDropEvent`** — every price decrease (amount + %, indexed); emitted
  inline by the ingestion pipeline on each detected price drop.
- **`accounts` engagement** — `Favorite` (unique user+ad), `Watchlist` (M2M ads),
  `SavedSearch` (params JSON + `notify` + `last_checked_at`), `Alert`
  (`price_drop`/`undervalued`/`new_listing`, polymorphic target, threshold,
  channels), `Notification` (channel + status + dedupe key); `User.telegram_chat_id`.

### Phase 4 — Analytics engine ✅

Reuses the existing `truemean`/`bollinger`/`insights`/`PriceStatistics` services;
adds two new ones and exposes everything over the API.

- **`analytics/services/deal_score.py`** — `compute_deal_scores(min_peers=3,
  model_id=None)`: scores each ad against its (brand,model,variant,year) peer
  group via `discount_pct * exp(-age_days/90)`, full-refreshed into
  `DealScoreCache`.
- **`analytics/services/metrics.py`** — rankings (brands/models/variants),
  regional pricing, dealer statistics, inventory trend, market overview,
  time-on-market, fast sellers, price drops. Medians computed in Python
  (no ORM median aggregate) to stay outlier-robust.
- **11 new endpoints** under `/api/analytics/` (see API section).
- **Pipeline integration:** `run_pipeline` now runs `mark_inactive →
  daily_snapshot → market_snapshot → deal_scores → refresh_analytics`.

### Phase 5 — Premium features ✅

- **Engagement CRUD** (owner-scoped, JWT-authenticated): favorites, watchlists
  (with nested ad membership), saved searches, alerts, read-only notification
  inbox — all mounted at `/api/<resource>/`.
- **Notification delivery** (`accounts/notifications.py`): `send_email`
  (console backend by default), `send_telegram` (silent skip when no token/
  chat_id), `create_notification` (dedupes on `dedupe_key`), `deliver` (never
  raises). Channels: in-app / email / telegram.
- **Alert evaluation** (`accounts/alerts.py` + `evaluate_alerts` command):
  `price_drop`, `undervalued`, `new_listing` handlers fan out notifications.
- **Digests:** `send_digest --kind daily|weekly` per active user.
- **Throttle wiring:** `SubscriptionThrottle` (burst by plan) +
  `MonthlyQuotaThrottle` (atomic `F()` increment, hard monthly cap) now attached
  to every engagement **write** viewset — the latent `get_rate()` crash is fixed.

### Phase 6 — API (OpenAPI-documented) ✅

- **drf-spectacular** added; `DEFAULT_SCHEMA_CLASS` set; `SPECTACULAR_SETTINGS`
  declares the JWT security scheme. **`manage.py spectacular` is warning- and
  error-free** (54 paths, 19 tag groups). Every function-based view carries
  `@extend_schema` *above* `@api_view` (the order matters — underneath, the
  `responses` override is lost).
- **Interactive docs:** `GET /api/schema/` (OpenAPI 3), `GET /api/docs/`
  (Swagger UI), `GET /api/redoc/`.
- **Admin job triggers** (IsStaff-gated, run in a daemon thread, return 202):
  `/api/admin/jobs/{fetch,import,refresh-analytics,deal-scores,evaluate-alerts}/`.
- All endpoint groups tagged for the docs UI: `auth`, `brands`, `models`, `ads`,
  `Markets`, `Charts`, `Market history`, `Price history`, `Analytics`, `History`,
  `changes`, `observations`, `fetch-runs`, `favorites`, `watchlists`,
  `saved-searches`, `alerts`, `notifications`, `Admin · Jobs`.

## Background worker ✅

Linux cron + `flock` (no Celery). `deploy/worker/install_cron.sh` installs three
auto-managed entries (idempotent, distinct markers):

| Cadence | Runner | Does |
| --- | --- | --- |
| `*/5 * * * *` | `run_pipeline.sh` | fetch + maintain + snapshots + deal scores + analytics |
| `*/30 * * * *` | `run_alerts.sh` | evaluate user alerts → notifications |
| `17 8 * * *` | `run_digest.sh` | per-user daily digest (weekly is a manual run) |

`run_pipeline` = `fetch_live` → `mark_inactive_ads` → `daily_snapshot` →
`market_snapshot` → `compute_deal_scores` → `refresh_analytics`.

## Testing ✅

`pytest` + `pytest-django`. **95 tests pass** on the live Homebrew Postgres DB
(`postgresql@18:5432`, db `bama_saas`, user `parham` — Docker is unavailable in
this environment, so verification is via Homebrew PG, not `docker compose`).

- `tests/test_parsing.py` — pure-Python, no DB.
- `tests/test_analytics.py` + `tests/test_analytics_engine.py` — analytics services
  + the Phase-4 engine (30 tests).
- `tests/test_importer.py` — ingestion pipeline.
- `tests/test_premium.py` — Phase-5 engagement + alerts + digest (15 tests).

## Known issues / deferred

- **Frontend is deferred.** The project is backend-first; `frontend/` is an empty
  placeholder.
- **`docker compose` v2 plugin required** (use `docker compose …`, not the legacy
  Python `docker-compose`). Not exercised in this environment — verified via
  Homebrew PG instead.
- **Celery/Redis still stubbed** (commented in `docker-compose.yml`). The worker
  is cron + daemon-thread admin jobs; a real queue is a future hardening step.
- **Email delivery** defaults to the console backend; set SMTP env vars
  (`EMAIL_HOST`/`EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD`/`EMAIL_USE_TLS`) for
  production. Telegram delivery is a no-op until `TELEGRAM_BOT_TOKEN` is set.

## Verification commands

```bash
# Local (Homebrew PG; DATABASE_URL defaults to postgresql://parham@localhost:5432/bama_saas)
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver          # http://localhost:8000/api/ and /admin/

# OpenAPI docs (Phase 6)
python manage.py spectacular --format openapi-json --urlconf config.urls   # 0 warnings
# then visit /api/docs/ (Swagger) or /api/redoc/

# Seed + analytics
python manage.py import_scraped
python manage.py import_history
python manage.py refresh_analytics --min-count 3

# MVP-completion commands (Phase 3-5)
python manage.py mark_inactive_ads            # flip stale ads → REMOVED
python manage.py daily_snapshot               # write DailyInventorySnapshot for today
python manage.py market_snapshot              # write MarketSnapshot for today
python manage.py compute_deal_scores          # rebuild DealScoreCache
python manage.py evaluate_alerts              # user alerts → notifications
python manage.py send_digest --kind daily     # (or --kind weekly)

# Full worker pipeline (Phase 2 / Phase 4 integration)
python manage.py run_pipeline

# Worker cron
deploy/worker/install_cron.sh                 # install the 3 auto-managed crontab entries

# Tests
pytest                                        # 95 passing
```

---

**Last updated:** 2026-07-18
**Status:** Foundation + MVP Phases 3–6 complete; 95 tests green; OpenAPI schema clean.
