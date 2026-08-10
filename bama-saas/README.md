# Bama SaaS Backend

Django 5.2 / DRF / PostgreSQL service for Bama car-listing data: a normalized
catalog, append-only provenance, change-only price-through-time, and derived
market analytics (true-mean, Bollinger, liquidity, depreciation). The schema
uses PostgreSQL-specific features (`django.contrib.postgres`, GIN indexes), so
**SQLite is not supported**.

## Stack

Django 5.2, djangorestframework, djangorestframework-simplejwt (JWT auth +
token blacklist), **drf-spectacular** (OpenAPI 3 schema + Swagger/ReDoc UI),
django-filter, django-cors-headers, dj-database-url,
`psycopg[binary]` (PostgreSQL), numpy, jdatetime, requests, gunicorn. Requires
**Python ≥ 3.11**.

## Layout

```
bama-saas/
├── config/                 project package (settings.base/dev/prod, urls, wsgi, asgi)
├── apps/
│   ├── accounts/           email-based User, Subscription; SimpleJWT auth
│   ├── core/               all domain data: catalog (Brand→Model→Variant, Ad),
│   │                       history (FetchRun, AdVersion, …), prices, analytics;
│   │                       models/, serializers/, views/ split by those themes
│   ├── jobs/               management commands + ingestion services (no models)
│   └── parsing/            zero-Django pure-Python Bama payload rules (ported)
├── frontend/               no-build Persian SPA (vanilla ES modules)
├── web/                    React + Vite + TypeScript workspaces (see "Frontend")
├── deploy/worker/          cron installer + runner scripts (pipeline, sweep, alerts, digest)
├── tests/                  pytest-django: parsing, verification, analytics, crawler, pipeline
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

Open <http://localhost:8000/api/docs/> for the interactive Swagger docs
(auto-generated via drf-spectacular), <http://localhost:8000/api/redoc/> for
ReDoc, <http://localhost:8000/api/> for the API root, and
<http://localhost:8000/admin/> for the Django admin. `manage.py` defaults to
`config.settings.dev`, which sets `DEFAULT_PERMISSION_CLASSES = [AllowAny]` so
read endpoints are open locally; `config.settings.prod` keeps the
`IsAuthenticated` default.

## Docker

Requires the Compose v2 plugin (`docker compose …`). From `bama-saas/`:

```bash
docker compose up --build          # add --profile dev to also start pgadmin on :5050
```

Compose starts `postgres:16-alpine` and a `django` service that runs
`migrate --noinput` then `runserver`. It mounts the project's own `data/`
read-only at `/data` (`./data:/data:ro`) and sets
`BAMA_SCRAPED_DATA_ROOT=/data`.

Two things that will bite you without a `.env`:

- **Postgres publishes on host port 5433, not 5432.** A native PostgreSQL
  install usually already owns 5432 and the bind fails. Override with
  `POSTGRES_HOST_PORT`. The container-internal port is always 5432, so the
  `django` service's `DATABASE_URL` is unaffected.
- **`SECRET_KEY` must be non-empty.** `settings/base.py` has a dev fallback, but
  an empty env var still *sets* the variable and overrides it, so SimpleJWT dies
  at import with `SECRET_KEY must not be empty`. Compose supplies an obvious
  insecure dev default; set a real one in `.env` for anything but local dev.

The compose stack has its own `postgres_data` volume — it is a **separate
database** from any native PostgreSQL you run locally.

## Management commands

All commands run via `python manage.py <command>`.

| Command | Description |
| --- | --- |
| `migrate` | Apply Django migrations. |
| `createsuperuser` | Create an admin (email-based) user. |
| `runserver` | Dev server on `:8000`. |
| `import_scraped [--root --limit --batch-size]` | Bulk-load publish-complete ads from `BAMA ADS/**/ads.json` under `BAMA_SCRAPED_DATA_ROOT` (~1,560 files / ~49.6k ads). Idempotent for Ad / AdVersion / PriceObservation. |
| `import_history [--db --limit --batch-size]` | Replay the seed SQLite history DB (`BAMA_HISTORY_DB_PATH`, default `data/bama.db`) into the append-only schema to build price-through-time. |
| `fetch_live [--mode delta\|full\|backfill] [--start-page --end-page --max-ads --page-pause --request-timeout --max-stale-pages]` | Stream live ads from bama.ir into Postgres via the same ingest pipeline as `import_scraped`. `delta` (default) reads the newest pages and stops after N stale pages; `full` sweeps page 0 → end of feed (~936 pages / ~28k ads, 15-20 min) and records `reached_end`; `backfill` refetches an explicit page range. Writes a `PageCoverage` row per page. |
| `crawl_gaps [--since-hours 24 --max-ranges 5 --dry-run]` | Find rank ranges no run has covered recently (`coverage.find_gaps`) and refetch them via `backfill`. This is what catches ads lost to deletion-driven rank shift. |
| `backfill_normalization [--batch-size --since-code --limit --dry-run]` | One-off, resumable, idempotent: recompute `year_jalali`/`year_gregorian`/`year_calendar`, zero-km `mileage`, and `canonical_path` on existing rows from `raw_payload`. **Run once after upgrading.** |
| `mark_inactive_ads [--days N]` | Flip ads absent from the **last two completed sweeps** to `Ad.Status.REMOVED`, stamping `removed_at` with the ad's own `last_seen_at`. Marks nothing if fewer than two `reached_end` sweeps exist, so a stalled crawler can't empty the market. `--days` is a wall-clock escape hatch. |
| `backfill_snapshots [--days 60 --liveness-days 2]` | Reconstruct historical `DailyInventorySnapshot` rows from `AdObservation` + `PriceObservation`, so the market index has history on a fresh install instead of starting flat. Idempotent; never touches today's row. |
| `build_market_index [--min-cohorts 5 --scope market\|brand\|model\|all]` | Rebuild the matched-cohort price index. Reads `DailyInventorySnapshot`; runs after `daily_snapshot`. |
| `crawl_health [--json]` | Report sweep freshness, failed runs, ingest-reject spikes, coverage gaps and ingest progress. **Exits 1 when unhealthy** so cron/CI can gate on it. |
| `data_quality [--json]` | Record today's `DataQualitySnapshot` (null rates, cardinality, flag counts) and compare it to the trailing window. **Exits 1 on drift**, same contract as `crawl_health`: the exit code is the interface. |
| `flag_cohort_outliers [--model-id]` | Recompute `Ad.cohort_flags` by median/MAD within each (model, variant, year) cohort. Runs *after* a sweep, not during ingest — judging a price against its peers needs the peers. |
| `sync_episodes [--limit]` | Open/close `ListingEpisode` rows and attach `VehicleIdentity`. Runs after removal marking, because an episode ends when an ad stops being seen — a conclusion no single observation can reach. Idempotent; back-fills all history on first run. |
| `confirm_dimensions [--brand --all --aliases]` | List or confirm catalog brands/models that ingestion invented from free-text titles (`unknown_dimension`). `--all` is the cold-start path; `--aliases` reports model rows that are two names for one car, keyed on the same ad code appearing under both. |
| `daily_snapshot` | Write today's `DailyInventorySnapshot` (active/new/removed counts + price spread). |
| `market_snapshot [--date]` | Write a day's `MarketSnapshot` (market-wide rollup + brand breakdown); defaults to today. |
| `compute_deal_scores [--min-peers 3 --model]` | Rebuild `DealScoreCache` — the best-deal board — scoring each ad vs its (brand,model,variant,year) peers. |
| `evaluate_alerts` | Evaluate every enabled user `Alert` and dispatch notifications (in-app/email/telegram). |
| `send_digest [--kind daily\|weekly]` | Per-user digest email. |
| `run_pipeline [--skip-fetch --steps]` | Orchestrate the worker tick: `fetch → mark_inactive → episodes → daily_snapshot → market_index → market_snapshot → deal_scores`. Each step records a `JobRun`; a step whose declared prerequisite failed is recorded as `skipped`. (`refresh_analytics` is gone, along with the `PriceStatistics` table it wrote — no view, serializer or service ever read it.) |

### Seeding data

With a seed DB at `data/bama.db` (a SQLite snapshot of historical listings):

```bash
python manage.py import_history        # price-through-time from the seed SQLite
python manage.py confirm_dimensions --all   # a fresh catalog is 100% unconfirmed by definition
python manage.py backfill_snapshots --days 60
```

For fresh data, run `python manage.py fetch_live --max-ads 1000` instead.

## Background worker

A Linux cron + `flock` worker (no Celery) keeps the data fresh and the alerts
firing. `deploy/worker/install_cron.sh` installs four auto-managed crontab
entries (idempotent — each owns a distinct marker line). It refuses to install
while the compose `worker` container is running: the two cannot see each other's
`flock` files (host `/tmp` vs container `/tmp`), so both would mean two fetchers
against one database.

| Cadence | Runner | Job |
| --- | --- | --- |
| every 5 min | `run_pipeline.sh` | `run_pipeline` (delta fetch → mark_inactive → episodes → snapshots → market index → deal scores) |
| every 6 h | `run_sweep.sh` | `fetch_live --mode full → crawl_gaps → flag_cohort_outliers → data_quality → crawl_health` |
| every 30 min | `run_alerts.sh` | `evaluate_alerts` (user alerts → notifications) |
| daily ~08:17 | `run_digest.sh` | `send_digest --kind daily` |

The 5-minute tick stops early once the top of the feed goes quiet, which is cheap
but can only prove *the newest pages* are unchanged. It cannot see an ad deleted
deep in the feed — that pulls everything below it up past a page boundary an
earlier run already read. The 6-hourly sweep is what makes coverage provable: it
walks every page, records a `PageCoverage` row per page, and sets `reached_end`.
`crawl_gaps` then refetches any rank range still uncovered. The sweep takes its
own lock, so it never blocks a tick.

Remove with `crontab -l | grep -v 'bama-saas-worker' | crontab -`.

## API surface

All routes are mounted under `/api/` (see `config/urls.py`) and auto-documented
via **drf-spectacular**.

**Documentation**
- `GET /api/schema/` — OpenAPI 3 schema (`manage.py spectacular` is warning-free).
- `GET /api/docs/` — Swagger UI; `GET /api/redoc/` — ReDoc.

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

  Ad responses are curated columns plus `cohort_flags`, never `raw_payload`. The
  full scraped record — dealer contact details, internal identifiers, promotion
  state — was riding along on the public list endpoint and made every response
  many times larger than the fields anyone reads. It moved to
  `GET /api/admin/ads/<code>/provenance/` (staff-only).

**Market analytics** (`/api/`) — keyed by `<int:model_id>` because Bama model names are Persian; the brand is implied.
- `GET /api/markets/` — per-model summary (top-N by ad count): `?limit=` (≤500).
- `GET /api/markets/<model_id>/true-mean/?variant=&year=&method=zscore|percentile&z=2.0`
- `GET /api/markets/<model_id>/bollinger/?variant=&window=20&sigma=2.0`
- `GET /api/markets/<model_id>/price-trends/?variant=&bucket=day|week|month`
- `GET /api/ads/<code>/price-history/` — single ad's change-only price series.

**Insights** (`/api/`) — kind ∈ `liquidity | market-depth | undervalued | depreciation`:
- `GET /api/insights/<model_id>/<kind>/?variant=&year=`

**Analytics** (`/api/analytics/`) — deal scoring, rankings, and market metrics (Phase 4):
- `GET /api/analytics/deal-scores/` — top deals vs peers (`?model=&brand=&year=&min_score=&limit=`); `GET /api/analytics/deal-scores/<code>/` for one ad.
- `GET /api/analytics/rankings/<dim>/` — `dim` ∈ `brands|models|variants` (`?limit=`).
- `GET /api/analytics/regional/?model=&limit=` — regional pricing.
- `GET /api/analytics/dealers/?limit=` — dealer statistics.
- `GET /api/analytics/inventory-trends/<model_id>/?days=` — inventory growth/decline; `GET /api/analytics/market-overview/?days=` — market-wide rollup.
- `GET /api/analytics/market-index/?scope=market|brand|model&id=&days=` — **the composition-controlled price index** (base 100). See "Measuring market movement" below.
- `GET /api/analytics/time-on-market/<model_id>/` — days listed + days-to-delist; `GET /api/analytics/fast-movers/<model_id>/?limit=` — listings that left the feed fastest.
- `GET /api/analytics/price-drops/?model=&min_pct=&days=&limit=` — recent price drops.
- `GET /api/analytics/newest/?model=&limit=` and `GET /api/analytics/oldest/?model=&limit=` — newest / oldest publish-complete ads.

**History / provenance** (`/api/`)
- Cross-ad: `GET /api/changes/`, `GET /api/observations/`, `GET /api/fetch-runs/` (plus `/<id>/` detail).
- Per-ad: `GET /api/ads/<code>/versions/`, `GET /api/ads/<code>/changes/`, `GET /api/ads/<code>/timeline/` (merged observations + change events, newest first).

**Engagement** (`/api/<resource>/`) — owner-scoped, JWT-authenticated (Phase 5). Write
viewsets carry `SubscriptionThrottle` (burst by plan) + `MonthlyQuotaThrottle` (hard
monthly cap):
- Favorites: `GET/POST /api/favorites/` (POST body `{code}`; idempotent),
  `DELETE /api/favorites/<code>/`.
- Watchlists: `GET/POST /api/watchlists/`, `GET/PATCH/DELETE /api/watchlists/<pk>/`,
  `GET/POST /api/watchlists/<pk>/ads/` (POST `{code}`),
  `DELETE /api/watchlists/<pk>/ads/<code>/`.
- Saved searches: `GET/POST/PATCH/DELETE /api/saved-searches/` (`{name, params, notify}`).
- Alerts: `GET/POST/PATCH/DELETE /api/alerts/` (`alert_type` ∈
  `price_drop|undervalued|new_listing`, with an ad/watchlist/model/saved-search target
  and a `threshold`).
- Notifications: `GET /api/notifications/` — read-only in-app inbox (paged).

**Admin** (`/api/admin/`) — IsStaff-gated. The POST triggers run their command in
a daemon thread and return `202`; the GETs are read-only:
- `POST /api/admin/jobs/fetch/`, `/import/`, `/refresh-analytics/`,
  `/deal-scores/`, `/evaluate-alerts/`. `/refresh-analytics/` keeps its URL but
  now runs `run_pipeline --skip-fetch` — it used to rebuild `PriceStatistics`,
  which nothing read, so the button did nothing observable.
- `GET /api/admin/jobs/overview/` — recent `JobRun` outcomes plus the latest per
  job name, including `skipped`. "Did the scheduled work actually run" stops
  being a question you answer by reading container logs.
- `GET /api/admin/jobs/crawl-health/` — the `crawl_health` checks; `503` when any fails.
- `GET /api/admin/ads/<code>/provenance/` — the unabridged record: `raw_payload`,
  both flag sets, and every `AdVersion` payload. This is where the scraped record
  now lives; it is deliberately not on the public ad serializer.

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
- `apps/accounts/throttles.py` ships `SubscriptionThrottle` (burst/min scaled by plan) and `MonthlyQuotaThrottle` (hard monthly cap from `Subscription.monthly_api_limit`, atomic increment). These are attached to every **engagement write** viewset (favorites, watchlists, saved searches, alerts); read endpoints and the operator-only admin job triggers are unthrottled.

## Measuring market movement

"What does a car cost today" and "did prices move" are different questions, and
a median over live listings only answers the first. It moves whenever the *mix*
of listings moves. Measured on the live database:

| Jul 5 → Aug 7 | Raw median | Matched-cohort index |
| --- | --- | --- |
| Change | **−6.7%** | **+0.45%** |

The median's swing was crawler behaviour, not the market: on Jul 16 coverage
collapsed to 3,063 ads and the median "rose" 37%, then "crashed" 31% when
coverage recovered.

`apps/core/services/index.py` fixes this by never comparing different cars:

1. `r_c = median_d / median_prev - 1` per (model, variant, year_jalali) cohort
2. `R_d = Σ(r_c · n_c) / Σ n_c` weighted by the smaller side's ad count
3. `index_d = index_prev · (1 + R_d)`, base 100

A cohort present on only one of two dates shifts the weights but contributes no
return, so listing churn cannot move the index. Thin cohorts (`< 3` ads) are
dropped and single-cohort moves beyond ±50% are clipped as data artefacts.

Input is `DailyInventorySnapshot`, so the index adds no crawl load. On a fresh
install run `backfill_snapshots` first to reconstruct history from
`AdObservation` + `PriceObservation`.

## Monitoring the crawl

Everything needed to detect a broken crawl was already recorded and nothing read
it. `crawl_health` closes that:

```bash
python manage.py crawl_health          # exit 1 when unhealthy
curl -s localhost:8000/api/admin/jobs/crawl-health/   # 503 when unhealthy
```

Checks: no completed sweep within 13 h · any `FAILED` run in 24 h · one
`IngestReject` rule firing >3× its 7-day baseline (how a Bama schema change
announces itself) · uncovered rank ranges · pages fetched but zero ads stored
(the silent-block signature). It runs after every sweep and logs loudly.

## Data integrity

Two properties the analytics depend on, both enforced in the shared ingest path:

- **Model year is calendar-normalized.** Bama publishes years in either calendar
  depending on brand (36,782 Jalali vs 20,480 Gregorian across 57,262 ads, and
  20+ brands use both), so `Ad.year` mixes `1399` and `2025` in one column and is
  unusable for grouping. `Ad.year_jalali` is the canonical cohort key used by
  every peer group and by `?year=`/`year_min`/`year_max`. `Ad.year` is kept raw
  for provenance.
- **Zero-kilometer means 0, not NULL.** `"صفر کیلومتر"` is ~33% of ads; treating
  it as NULL silently removed every brand-new car from mileage filters and biased
  mileage statistics upward.

### Four layers of validation

Each layer exists because the one above it is structurally blind to a class of
corruption — not because more checking is generally better.

1. **Row rules** — `apps/jobs/services/verify.py`. Each rule is a pure
   `(extracted, payload) -> Rejection | None`, so `RULES` is introspectable and
   testable in isolation. Fields are bounded against plausible bands (a rial
   price is 10× a toman one and lands outside), plus cross-field contradictions.
2. **Temporal rules** — `verify_temporal.py`, comparing an ad against its own
   previous row. Every value can sit inside its band while the *transition* is
   impossible: `15,000,000,000 → 1,500,000,000` overnight is two legal prices and
   one unit switch. All temporal rules are soft, because an impossible transition
   proves one of the two observations is wrong without saying which.
3. **Cohort outliers** — `verify_cohort.py`, run by `flag_cohort_outliers` after
   a sweep. A Pride at 4,000,000,000 toman passes both layers above; only its
   peers say it is wrong. Median and MAD, never mean and σ: one extreme value
   drags the mean toward itself *and* inflates σ, so the outlier widens the very
   band meant to catch it. Median/MAD have a 50% breakdown point, which also
   removes any need for a remove-and-refit loop.
4. **Distribution drift** — `drift.py`, run daily by `data_quality`. The first
   three all encode an expectation, so they can only catch what someone
   anticipated. A renamed source field fires no rule at all; the data just gets
   quietly emptier. This layer watches null rates, cardinality collapse and fresh
   catalog rows against a trailing median/MAD baseline.

A **hard** failure means the source value is unusable and unrepairable, so the ad
is **not persisted at all**: an `IngestReject` row retains the raw payload
(nothing is silently dropped — a wrong rule can still be replayed) and any
existing `Ad` with that code is deleted. Flagging alone was insufficient, because
the next fetch's upsert re-inserted anything a cleanup pass had removed.

Everything else flags rather than deletes, and the read side is where that
matters. `apps.core.services.quality` is the single chokepoint:
`verified` drops hard-flagged rows, `verified_by_ad` does the same for querysets
that merely reference an `Ad`, and `without_cohort_outliers` is applied **only
where a baseline is computed** — a median, a regression, an index level. An
outlier must not help define the number that judges it, but it is still a real
listing, and a genuinely underpriced car is the most valuable thing this product
can find. Data quality and the bargain signal are one mechanism read in two
directions, which is why `cohort_flags` is served on the ad itself.

A spike in one rule id is how a Bama schema change announces itself:

```sql
SELECT rule, count(*) FROM history_ingestreject
WHERE observed_at > now() - interval '1 day' GROUP BY rule ORDER BY 2 DESC;
```

## Frontend

Two live side by side until parity:

- `frontend/` — the working no-build Persian SPA (vanilla ES modules, hash
  router, vendored Chart.js).
- `web/` — **React + Vite + TypeScript**, five workspaces: Market Overview
  (public), Buyer Explorer, Research (subscription), My Market, Operations
  (staff). `npm run dev` proxies `/api` to Django so the browser sees one origin
  and dev has no CORS/cookie-domain difference from production.

Three decisions there are worth knowing before changing anything:

- **The API types are generated, not written.** `src/api/schema.d.ts` comes from
  the Django OpenAPI schema via `npm run api:types`. A renamed or removed
  endpoint then becomes a compile error instead of a blank panel someone notices
  in production.
- **Filter state lives in the URL.** `useFilters` reads and writes search params,
  so every view is shareable, the back button works, and two panels reading the
  same filter cannot disagree — there is only one copy of it.
- **Provenance is not decoration.** Every research answer carries `as_of`,
  coverage and a methodology version, and `<Provenance>` renders them. These
  numbers come from a crawl that can be incomplete, and a survival curve computed
  across a coverage hole reads crawler downtime as cars leaving the market.
  `<Async>` handles loading, error, empty, auth-required, subscription-required
  and *unavailable* — the last being the backend refusing to compute from too
  little data. That is a real answer, not an error, and never an empty chart.

See `web/README.md` for the commands.

## Tests

```bash
pytest
```

`tests/test_parsing.py` and `tests/test_normalize.py` are pure-Python (no DB);
`test_verify.py`, `test_verify_temporal.py`, `test_verify_cohort.py` and
`test_drift.py` cover the four validation layers one file each;
`test_catalog_guard.py` covers the unconfirmed-dimension gate; `test_analytics.py`
and `test_analytics_engine.py` exercise the analytics services and the Phase-4
engine; `test_insight_products.py` covers liquidity, fair price and retention;
`test_importer.py` covers the import pipeline; `test_premium.py` covers Phase-5
engagement, alerts and digests; `test_fetcher_pagination.py`, `test_coverage.py`,
`test_fetcher_delta.py` and `test_fetcher_resilience.py` cover the crawler;
`test_index.py` covers the market index (including the mix-shift property the
whole feature exists for); `test_identity.py` covers vehicle identity and listing
episodes; `test_lifecycle.py` covers sweep-based removal and the
mileage-adjusted deal score; `test_pipeline_jobs.py` covers step ordering,
prerequisite skips and `JobRun`; `test_api_exposure.py` pins what the public
serializers may return; `test_crawl_health.py` covers the health checks and the
empty-page truncation guard — **382 tests** total, all green against PostgreSQL.

The pagination tests mock `session.get`, not `fetch_page`, deliberately: mocking
the higher level is what let a 0-based-`pageIndex` bug survive undetected, so the
page-index arithmetic must stay inside the system under test.
