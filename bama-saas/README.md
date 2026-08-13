# Bama — personal deal finder

Local Docker Compose tool for bama.ir listings. Not a public SaaS: no login,
JWT, subscriptions, alerts, notifications, or digests. Django admin inspects
records; `/control` is crawl health + jobs only.

Analytics that remain: **fair price**, the **deal board** (min 8 peers; asking
price at least 50% of the peer median), the **matched-cohort market index**,
and **Kaplan–Meier time-to-sell**. PostgreSQL only — SQLite is not supported.

## Stack

Django 5.2, djangorestframework, **drf-spectacular**, django-filter,
django-cors-headers, dj-database-url, `psycopg[binary]`, numpy, jdatetime,
requests, gunicorn. Python ≥ 3.11. UI: React + Vite + TypeScript.

## Screens

| Path | Page |
| --- | --- |
| `/` | Deal board |
| `/explore` | Catalog explorer |
| `/listing/:code` | Listing detail + fair price |
| `/market` | Market overview / index |
| `/research/:modelId` | Kaplan–Meier time-to-sell + year retention |
| `/saved` | Saved cars (user-less favorites) |
| `/control` | Crawl health + job triggers |

## Layout

```
bama-saas/
├── config/                 settings.base/dev/prod, urls, wsgi, asgi
├── apps/
│   ├── accounts/           Django admin User + Favorite (saved cars)
│   ├── core/               catalog, history, prices, remaining analytics
│   ├── jobs/               management commands + ingestion (no models)
│   └── parsing/            zero-Django Bama payload rules
├── ui/web/                 React + Vite + TypeScript
├── docs/
├── deploy/                 local backup/restore + compose worker loop
├── tests/
├── manage.py
├── docker-compose.yml      Postgres, Django, worker, Vite frontend
├── Dockerfile
└── requirements.txt
```

## Docker (the usual path)

Requires Compose v2. From `bama-saas/`:

```bash
docker compose up --build          # add --profile dev for pgadmin on :5050
```

Compose starts PostgreSQL, Django, the worker, and the Vite dev server.
Open <http://localhost:5174>. Django is on <http://localhost:8001>
(`migrate` + `ensure_dev_admin` + `runserver`). The frontend bind-mounts
`ui/web/` and proxies `/api` to Django.

Frontend only (still brings up Django + Postgres):

```bash
docker compose up --build frontend
```

Two host-port traps:

- **Postgres publishes on 5433, not 5432.** Override with `POSTGRES_HOST_PORT`.
  Inside the network the port is always 5432.
- **`SECRET_KEY` must be non-empty.** An empty env var overrides the dev
  fallback. Compose supplies an insecure default; set a real one in `.env`
  if you care.

The compose `postgres_data` volume is a **separate database** from any native
PostgreSQL on the host. There is no prod compose file and no public deploy.

## Host setup (optional)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL='postgresql://postgres:postgres@localhost:5433/bama_saas'
python manage.py migrate
python manage.py ensure_dev_admin   # or createsuperuser
python manage.py runserver
```

Swagger: <http://localhost:8000/api/docs/>. Admin:
<http://localhost:8000/admin/>. Reads are `AllowAny`.

For live data: `python manage.py fetch_live --max-ads 1000`.

## Management commands

All via `python manage.py <command>`.

| Command | Description |
| --- | --- |
| `migrate` | Apply Django migrations. |
| `ensure_dev_admin` | Seed the local Django-admin superuser. |
| `runserver` | Dev server on `:8000`. |
| `fetch_live [--mode delta\|full\|backfill]` | Stream live ads from bama.ir. `delta` (default) stops after N stale pages; `full` walks the feed and records `reached_end`; `backfill` refetches a page range. |
| `crawl_gaps [--since-hours 24]` | Refetch rank ranges no run has covered recently. |
| `mark_inactive_ads` | Flip ads absent from the last two completed sweeps to `REMOVED`. |
| `sync_episodes` | Open/close `ListingEpisode` rows. |
| `daily_snapshot` | Today's `DailyInventorySnapshot`. |
| `build_market_index` | Rebuild the matched-cohort price index. |
| `market_snapshot [--date]` | Market-wide rollup for a day. |
| `compute_deal_scores [--model]` | Rebuild the deal board (fair-price discount, min 8 peers). |
| `crawl_health [--json]` | Sweep freshness / failed runs / reject spikes / gaps. Exit 1 when unhealthy. |
| `prune_history` | Drop old observations / coverage / job runs. |
| `reap_orphan_runs` | Clear leftover `RUNNING` FetchRun / JobRun rows. |
| `run_pipeline [--cadence hot\|warm\|full]` | Orchestrate a worker tick. Each step records a `JobRun`. |

## Background worker

The compose `worker` service runs `deploy/worker/run_worker.sh` (no Celery).
Do not also install host cron: two fetchers against one database.

| Cadence | Job |
| --- | --- |
| every 5 min | delta fetch → mark_inactive → incremental deal scores |
| every 30 min | episodes → daily_snapshot → market_index → market_snapshot |
| every 6 h | full fetch → crawl_gaps → warm pipeline → full deal scores → prune_history → crawl_health |

The 5-minute tick only proves the newest pages are unchanged. The 6-hourly
sweep walks every page, writes `PageCoverage`, and sets `reached_end`.

## API

Mounted under `/api/` (`config/urls.py`), documented by drf-spectacular:
`GET /api/schema/`, `/api/docs/`, `/api/redoc/`. Health: `/api/health/`,
`/api/db/health/`.

**Catalog** — `GET /api/brands/`, `/api/brands/<slug>/models/`,
`/api/models/<pk>/variants/`, `/api/ads/` (filterable), `/api/ads/<code>/`.
Ad responses are curated columns; `raw_payload` is
`GET /api/admin/ads/<code>/provenance/` (Django-admin inspection, not a
public field).

**Market / analytics**
- `GET /api/markets/`
- `GET /api/ads/<code>/price-history/`
- `GET /api/ads/<code>/fair-price/`
- `GET /api/analytics/deal-scores/` and `/api/analytics/deal-scores/<code>/`
- `GET /api/analytics/market-index/?scope=market|brand|model&id=&days=`
- `GET /api/analytics/overview/`
- `GET /api/research/liquidity/<model_id>/` — Kaplan–Meier time-to-sell
- `GET /api/research/price-position/<model_id>/`
- `GET /api/research/depreciation/<model_id>/` — year-over-year retention

**Saved cars** — `GET/POST /api/favorites/`, `DELETE /api/favorites/<code>/`.
No auth; one local list.

**Control** — `POST /api/admin/jobs/{fetch,refresh-analytics,deal-scores}/`
(202, runs in a thread). `GET /api/admin/jobs/overview/`,
`GET /api/admin/jobs/crawl-health/` (503 when unhealthy),
`GET /api/admin/health/`.

```bash
curl -s 'http://localhost:8001/api/analytics/deal-scores/?limit=20' | jq
curl -s 'http://localhost:8001/api/ads/CODE/fair-price/' | jq
curl -s 'http://localhost:8001/api/analytics/market-index/?days=90' | jq
```

## Measuring market movement

A median over live listings answers "what does a car cost today", not "did
prices move" — mix shifts move the median. `apps/core/services/index.py`
never compares different cars:

1. `r_c = median_d / median_prev - 1` per (model, variant, year_jalali) cohort
2. `R_d = Σ(r_c · n_c) / Σ n_c` weighted by the smaller side's ad count
3. `index_d = index_prev · (1 + R_d)`, base 100

A cohort present on only one of two dates shifts weights but contributes no
return. Input is `DailyInventorySnapshot`.

## Crawl health

```bash
python manage.py crawl_health
curl -s localhost:8001/api/admin/jobs/crawl-health/
```

Checks: no completed sweep within 13 h, any `FAILED` run in 24 h, ingest-reject
spikes, uncovered rank ranges, pages fetched with zero ads stored.

## Data integrity

- **Cohort key is `year_jalali`, never raw `Ad.year`.** Bama publishes years
  in either calendar; `Ad.year` is provenance only.
- **Zero kilometres is 0, not NULL.** `"صفر کیلومتر"` is ~33% of ads.
- **Row verify then quarantine.** `apps/jobs/services/verify.py` on ingest.
  Hard failures write `IngestReject` and delete the `Ad`. Reads go through
  `quality.verified` / `verified_by_ad` / `without_cohort_outliers`.
- **Deal board vs fair price are the same number.** Min 8 peers; asking below
  half the peer median is dropped as a deposit/typo.

## Frontend

`ui/web/` — seven screens above. `npm run api:types` regenerates
`src/api/schema.d.ts` from the OpenAPI schema. Filter state lives in the URL.
`<Provenance>` renders `as_of` / coverage; `<Async>` treats "unavailable"
(too little data) as an answer, not an error.

## Tests

```bash
pytest
```

`test_parsing.py` / `test_normalize.py` are pure-Python. Remaining coverage:
verify, catalog guard, importer, crawler (pagination / coverage / delta /
resilience), market index, fair price / retention, lifecycle, pipeline,
API exposure, crawl health.
