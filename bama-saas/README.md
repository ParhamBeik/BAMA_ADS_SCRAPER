# Bama — personal deal finder

Crawls bama.ir listings and answers three questions about them: what is this car
worth (**fair price**), which listings are underpriced against their own cohort
(**deal board**), and did the market actually move (**matched-cohort index**,
**Kaplan–Meier time-to-sell**). Session login, one operator screen, an optional
Telegram notifier. PostgreSQL only — SQLite is not supported.

## Stack

Django 5.2 + DRF, PostgreSQL 16, `psycopg[binary]`, django-filter,
django-cors-headers, dj-database-url, jdatetime, requests, gunicorn.
Python ≥ 3.11. UI: React 19 + Vite + TypeScript (Persian, RTL). Redis is a
*cache only* — proxied listing photos and the deal board's computed window,
both pure derived data, evicted under an LRU cap. Still no Celery and no message
broker: the scheduler is a shell loop plus `flock`.

## Layout

```
bama-saas/
├── config/settings.py       one settings file; DJANGO_DEBUG=1 picks local
├── apps/
│   ├── accounts/            user, session auth, saved cars
│   ├── core/                models, views, serializers + the analytics:
│   │                        pricing.py quality.py research.py notify.py
│   └── jobs/                the crawler side:
│                            parsing.py fetcher.py ingest.py verify.py
│                            jobs.py pipeline.py
├── ui/web/                  React + Vite + TypeScript
├── deploy/                  worker.sh (the scheduler), backup, VPS deploy
├── tests/                   pytest-django, 8 files
├── docker-compose.yml       local: postgres, redis, django, worker, vite
└── docker-compose.prod.yml  VPS: postgres, redis, gunicorn, worker, nginx
```

One file per concern. `apps/core` is what the API serves; `apps/jobs` is what
fills it. `parsing.py` is the only module with no Django import.

## Screens

| Path | Page |
| --- | --- |
| `/` | Deal board |
| `/explore` | Catalog explorer |
| `/listing/:code` | Listing detail + fair price |
| `/market` | Market overview / index |
| `/research/:modelId` | Kaplan–Meier time-to-sell + year retention |
| `/saved` | Saved cars |
| `/control` | Crawl health + job triggers (staff only) |

## Running it

```bash
docker compose up --build
```

Starts PostgreSQL, Django (`migrate` → `runserver`), the
worker loop, and Vite. UI on <http://localhost:5174>, API on
<http://localhost:8001>, Django admin on <http://localhost:8001/admin/>.

Two host-port traps:

- **Postgres publishes on 5433, not 5432.** A native PostgreSQL install usually
  owns 5432, and a host-run `manage.py` pointed there would silently read a
  different database. Override with `POSTGRES_HOST_PORT`; inside the compose
  network the port is always 5432.
- **`SECRET_KEY` must be non-empty.** An empty env var overrides the fallback.

Without Docker:

```bash
python -m venv .venv && source .venv/bin/activate && pip install -e '.[test]'
export DATABASE_URL='postgresql://postgres:postgres@localhost:5433/bama_saas'
python manage.py migrate
DJANGO_DEBUG=1 python manage.py runserver
```

There are no seeded logins. Open the UI, create an account, and the first one on
an empty database gets staff rights (the Control page and Django admin);
everyone after it is an ordinary user. `manage.py wipe_users --yes` empties the
table if you want to start again.

`DJANGO_DEBUG` is unset by default and the default is the *hardened* profile —
HTTPS redirect, HSTS, throttles, login required. A deployed process that forgets
an env var must not fail open.

## The one command

```bash
python manage.py bama <cadence|job> [--json] [--dry-run] [options]
```

Cadences bundle jobs; a job name runs that job alone.

| Cadence | Jobs | Worker interval |
| --- | --- | --- |
| `hot` | fetch → mark_inactive → deal_scores → probe_sold → notify | 15 min |
| `coverage` | coverage | 10 min |
| `warm` | episodes → snapshot → market_index | 30 min |
| `maintenance` | deal_scores → backfill_images → prune → health | 6 h |
| `full` | every hot + warm step, full deal rebuild | on demand |

Jobs: `fetch`, `mark_inactive`, `episodes`, `snapshot`, `market_index`,
`deal_scores`, `probe_sold`, `notify`, `coverage`, `backfill_images`, `prune`, `health`,
`reap_orphans`.
Every step writes a `JobRun` row, so "did last night's snapshot run?" is a
query rather than a log excavation. A step whose declared prerequisite failed is
recorded as `skipped`, never allowed to publish a number computed from stale
input.

`deploy/worker.sh` is the scheduler: PID 1 of the compose `worker` service, or
`worker.sh hot` from host cron. Never both — two fetchers double the request
rate against bama.ir.

There is no full-feed sweep. Coverage accumulates from bounded chunks, because
the old ~936-page sweep only completed 11 times in 28 attempts and removal
detection cannot depend on a job that usually dies halfway.

## API

Everything under `/api/`. Health: `/api/health/`, `/api/db/health/`.

- **Auth** — `/api/auth/{me,register,login,logout}/`
- **Catalog** — `/api/brands/`, `/api/brands/<slug>/models/`,
  `/api/models/?q=&brand=` (searchable, with listing counts),
  `/api/models/<pk>/variants/`, `/api/ads/`, `/api/ads/<code>/`
- **Photos** — `/api/img/<code>/<n>/`, proxied and Redis-cached
- **Market** — `/api/markets/`, `/api/ads/<code>/price-history/`,
  `/api/ads/<code>/fair-price/`
- **Analytics** — `/api/analytics/deal-scores/?band=top|all|review`,
  `/api/analytics/deal-scores/<code>/`,
  `/api/analytics/market-index/?scope=market|brand|model&id=&days=`,
  `/api/analytics/overview/`
- **Research** — `/api/research/{liquidity,depreciation}/<model_id>/`
- **Saved cars** — `/api/favorites/`, session-scoped to the user
- **Notifier** — `/api/notifier-settings/` (singleton, disabled by default)
- **Operator** (staff only) — `POST /api/admin/jobs/{fetch,refresh-analytics,deal-scores,backfill-images}/`
  (202, runs in a thread), `GET /api/admin/jobs/{overview,crawl-health}/`
  (503 when unhealthy), `GET /api/admin/health/`,
  `GET /api/admin/ads/<code>/provenance/`

Ad responses are curated columns. `raw_payload` is operator-only — it is the
whole scraped record, not a public field.

```bash
curl -s 'http://localhost:8001/api/analytics/deal-scores/?band=top&limit=20' | jq
curl -s 'http://localhost:8001/api/analytics/market-index/?days=90' | jq
```

## Measuring market movement

A median over live listings answers "what does a car cost today", not "did
prices move" — a shift in what is *listed* moves the median on its own. The
index never compares different cars (`apps/core/research.py`):

1. `r_c = median_d / median_prev - 1` per (model, variant, year_jalali) cohort
2. `R_d = Σ(r_c · n_c) / Σ n_c`, weighted by the smaller side's ad count
3. `index_d = index_prev · (1 + R_d)`, base 100

A cohort present on only one of two dates shifts weights but contributes no
return. Input is `DailyInventorySnapshot`.

## Data integrity

- **The cohort key is `year_jalali`, never raw `Ad.year`.** Bama publishes model
  years in either calendar; `Ad.year` is provenance only.
- **Zero kilometres is `0`, not NULL.** `"صفر کیلومتر"` is ~33% of ads.
- **Verify, then quarantine.** Every ingest runs `apps/jobs/verify.py`. Soft
  rules set `Ad.quality_flags`; a hard failure writes an `IngestReject` and
  deletes the `Ad`. Analytics reads go through `apps/core/quality.py`.
- **Removal is proven by coverage, not elapsed time.** An ad is `REMOVED` only
  after being absent from two complete coverage windows.
- **The deal board and fair price are the same number.** Minimum 8 peers; an
  asking price below half the peer median is dropped as a down payment or typo.
  Installment ads are excluded — their "price" is a deposit, and left in they
  were 74% of the top deals.
- **Freshness ranks before size of discount.** The board groups by how recently
  an ad was published or bumped (`publish_at`, never `first_seen_at` — that one
  says when *our crawler* arrived) and the discount only orders within a group.
  How far back it looks and how good a deal must be are both measured per
  rebuild from the batch on the board (`pricing.deal_window`), never hardcoded.
- **Above 25% is a review band, not a recommendation.** The peer key is
  (model, trim, year) and knows nothing about damage, free-zone plates or
  pre-sales, so past that the gap is usually an attribute the model cannot see.
  Nothing is hidden; it moves to a tab that says what it is.
- **Every stored ad has a photo.** Every feed request carries
  `fetcher.FEED_FILTERS` (`image=1&priced=1`), so a photoless ad is outside the
  collected population, not a listing with a field missing.
  `verify._photo_missing` is hard — it quarantines rather than stores — and
  `backfill_images` fills what it can from `raw_payload` then deletes the rest.
  There is no `has_image` filter, because there is nothing for it to exclude.
- **Photos are served from our own origin.** Bama's CDN blocks our egress
  periodically, so each photo is fetched once and cached in Redis
  (`apps/core/images.py`). The card thumbnail and the gallery are *different
  files* — `resize,w_450` and `w_600` — and have separate addresses:
  `GET /api/img/<code>/thumb/` and `GET /api/img/<code>/<n>/`.

## Frontend

```bash
cd ui/web && npm install
npm run dev        # proxies /api to http://localhost:8001
npm run build      # tsc -b && vite build
npm run typecheck
```

Persian throughout, RTL from `<html>`. Vazirmatn and JetBrains Mono are
bundled, never fetched from Google Fonts — that host is unreliable from Iran and
the fallback was Tahoma. Digits stay Latin: they sit in tabular columns beside
Latin magnitude suffixes, so "۳.۹۰B" would be two numeral systems in one token.

Filter state lives in the URL, so every view is shareable and the back button
works. `<Provenance>` renders `as_of` + coverage on every research answer.
`<Async>` treats *unavailable* — the backend refusing to compute from too little
data — as a real answer, not an error, and never as an empty chart. A survival
curve drawn across a coverage hole reads crawler downtime as cars selling.

## Open question: the 25-50% band — closed 2026-08-26

272 of 9,075 scored listings sat between 25% and 50% under their peer median.
Measured against production: **condition explains it**. Bama's `body_status` is
populated on every ad and was read by no scoring code; a full respray trades
~16.5% under its cohort, and 69% of the served board was a damage-declared car
against 26% of the catalogue. Recency-weighting the median was tested and
**rejected** (0.07% move at the median, 48% of cohorts lost). The remaining
review-band rows are cars whose gap the (model, variant, year, condition) key
still cannot see (plates, provenance) — they stay in review, labelled, not
recommended.

## Tests

```bash
pytest
```

Eight files, one per subject: `test_parsing` (pure Python, no DB), `test_verify`,
`test_ingest`, `test_fetcher`, `test_jobs`, `test_pricing`, `test_research`,
`test_api`. Shared fixtures are in `conftest.py`.
