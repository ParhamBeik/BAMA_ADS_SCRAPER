# Bama — personal deal finder

Crawls bama.ir listings and answers the questions a buyer actually arrives with:
what is this car worth (**fair price**), which listings are underpriced against
their own cohort (**deal board**), where is the market going and in which
segment (**matched-cohort index**, robust slopes, **Kaplan–Meier
time-to-sell**), what a given budget can reach, and — once you follow a car —
being told when one of them turns up. Session login, one operator screen, an
optional Telegram notifier. PostgreSQL only — SQLite is not supported.

## Stack

Django 5.2 + DRF, PostgreSQL 16, `psycopg[binary]`, django-filter,
django-cors-headers, dj-database-url, jdatetime, requests, gunicorn.
Python ≥ 3.11. The learned layer is an optional extra (`pip install -e '.[ml]'`)
— scikit-learn, LightGBM, numpy, joblib — so a host that wants the web app
without ~120MB of it can have one; `apps/ml` refuses rather than failing to
import. No `shap`: LightGBM computes exact TreeSHAP itself. No `pandas`: the
feature matrix goes straight from `values_list` into a numpy array. UI: React 19 + Vite + TypeScript (Persian, RTL). Redis is a
*cache only* — proxied listing photos and the deal board's computed window,
both pure derived data, evicted under an LRU cap. Still no Celery and no message
broker: the scheduler is a shell loop plus `flock`.

## Layout

```
bama-saas/
├── config/settings.py       one settings file; DJANGO_DEBUG=1 picks local
├── apps/
│   ├── accounts/            user, session auth, saved cars,
│   │                        watchlists + alert rules + the alert inbox
│   ├── core/                models, views, serializers + the analytics:
│   │                        pricing.py quality.py research.py notify.py
│   ├── jobs/                the crawler side:
│   │                        parsing.py fetcher.py ingest.py verify.py
│   │                        jobs.py pipeline.py
│   └── ml/                  the learned layer:
│                            features.py metrics.py train.py registry.py
│                            inference.py monitoring.py
├── ui/web/                  React + Vite + TypeScript
├── deploy/                  worker.sh + train.sh (the two loops), backup, deploy
├── tests/                   pytest-django, 10 files
├── docker-compose.yml       local: postgres, redis, django, worker, vite (+ ml)
└── docker-compose.prod.yml  VPS: postgres, redis, gunicorn, worker, ml, nginx
```

One file per concern. `apps/core` is what the API serves; `apps/jobs` is what
fills it. `parsing.py` is the only module with no Django import.

## Screens

| Path | Page |
| --- | --- |
| `/` | Market pulse — the read, movers by segment, what is turning |
| `/budget` | "I have this much money" — reachable cars, ranked |
| `/deals` | Deal board |
| `/explore` | Catalog explorer |
| `/analyse` | One scope, market → brand → model → trim → year, in the URL |
| `/listing/:code` | Listing detail + fair price + the deal verdict |
| `/alerts` | Alert inbox and the rules behind it |
| `/methodology` | Model cards — what produced each number, and how well it did |
| `/saved` | Saved cars |
| `/control` | Crawl health + job triggers (staff only) |

Five of these are tabs; `/alerts` and `/saved` are icons in the header, because
they are "my stuff" rather than ways into the market and the unread badge has to
be visible from every screen.

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
| `hot` | fetch → mark_inactive → deal_scores → probe_sold → notify → alerts | 15 min |
| `coverage` | coverage | 10 min |
| `warm` | link_reposts → episodes → snapshot → market_index | 30 min |
| `maintenance` | deal_scores → backfill_images → prune → health | 6 h |
| `train` | ml_train → ml_score | daily, own container |
| `full` | every hot + warm step, full deal rebuild | on demand |

Jobs: `fetch`, `mark_inactive`, `link_reposts`, `episodes`, `snapshot`,
`market_index`, `deal_scores`, `ml_score`, `probe_sold`, `notify`, `alerts`,
`coverage`, `backfill_images`, `prune`, `health`, `reap_orphans`, `ml_train`.
Every step writes a `JobRun` row, so "did last night's snapshot run?" is a
query rather than a log excavation. A step whose declared prerequisite failed is
recorded as `skipped`, never allowed to publish a number computed from stale
input.

`deploy/worker.sh` is the scheduler: PID 1 of the compose `worker` service, or
`worker.sh hot` from host cron. Never both — two fetchers double the request
rate against bama.ir.

`deploy/train.sh` is the second loop, in its own container. Training is the one
job here that saturates a CPU for minutes rather than seconds, and running it
inside the worker loop would make the crawl look slow for reasons nothing in the
crawl logs would explain. Daily, not hourly: the promotion gate compares a
challenger against a fresh holdout, and a holdout an hour wide would swap models
on sampling noise.

There is no full-feed sweep. Coverage accumulates from bounded chunks, because
the old ~936-page sweep only completed 11 times in 28 attempts and removal
detection cannot depend on a job that usually dies halfway.

## API

Everything under `/api/`. Health: `/api/health/`, `/api/db/health/`.

- **Auth** — `/api/auth/{me,register,login,logout}/`
- **Catalog** — `/api/brands/`, `/api/brands/<slug>/models/`,
  `/api/models/?q=&brand=` (searchable, with listing counts) or `?id=` (resolve
  one model, so a shared link can name the car it is about),
  `/api/models/<pk>/variants/`, `/api/ads/`, `/api/ads/<code>/`
- **Photos** — `/api/img/<code>/<n>/`, proxied and Redis-cached
- **Market** — `/api/markets/`, `/api/ads/<code>/price-history/`,
  `/api/ads/<code>/fair-price/`
- **Analytics** — `/api/analytics/deal-scores/?band=top|all|review|ml`
  (`ml` is the learned board: listings the price model puts below its own
  predicted p10, ranked by that gap rather than by the cohort discount),
  `/api/analytics/deal-scores/<code>/`,
  `/api/analytics/market-index/?scope=market|brand|model&id=&days=`,
  `/api/analytics/overview/`
- **Market pulse** — all arithmetic over rows the warm tick already writes, no
  extra crawl load:
  `/api/analytics/market-read/?days=` (one categorical position — price
  direction crossed with absorption — with all three inputs attached as
  evidence),
  `/api/analytics/movers/?scope=brand|model|price_band|year_band|body_type&days=&limit=`
  (scopes ranked by index change, each row carrying its cohort and ad counts, a
  robust Theil–Sen slope, a 7-day slope, and a `turning` flag when the two
  disagree),
  `/api/analytics/turnover/?days=` (share of a model's listings that left the
  feed inside the window — departures, never "sold"),
  `/api/analytics/arrivals/?days=` (new listings per model),
  `/api/analytics/distribution/?brand=&model=&variant=&year=&condition=&mileage_bucket=`
  (percentiles, histogram, city and model-year facets; the payload says whether
  the conditioning was `filtered`, `adjusted` or refused),
  `/api/analytics/affordable/?budget=&tolerance_pct=` (which cars a budget
  actually reaches, and at what percentile of each cohort),
  `/api/analytics/movement/?model=&variant=&year=&days=` (an index below the
  three persisted scopes, computed per request)
- **Research** — `/api/research/{liquidity,depreciation}/<model_id>/`, both
  accepting `?variant=` and (liquidity) `?year=`
- **The learned layer** — `/api/ml/models/` (every trained model with its
  metrics and its promotion decision — this is what `/methodology` renders, and
  it includes the models that were *not* promoted, with the reason),
  `/api/ads/<code>/prediction/` (one listing's band plus the exact TreeSHAP
  decomposition behind it), `/api/ml/monitoring/` and `/api/ml/review-queue/`
  (staff only)
- **Saved cars** — `/api/favorites/`, session-scoped to the user
- **Follow and be told** — `/api/watchlists/` (a car, a trim or a whole brand;
  POST is idempotent and answers 200 on a repeat), `/api/alert-rules/` (the
  thresholds a user wants to hear about), `/api/alerts/` (the inbox, plus
  `mark-read/` and `unread-count/`). All user-scoped, all `IsAuthenticated`.
- **Notifier** — `/api/notifier-settings/` (the *operator's* Telegram channel, a
  singleton, disabled by default — separate from the per-user alerts above)
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
- **Stale peers are not confident peers.** A cohort whose newest listing has not
  been seen for two days drops one confidence tier and is badged `cohort_stale`.
  Forty peers last seen three weeks ago used to score exactly like forty fresh
  ones.
- **A segment's membership is fixed before its index is chained.** Price bands
  and age bands are assigned once, from the latest snapshot. Recomputed daily,
  a cohort whose price crossed a band edge would leave one series and join
  another, and both would report a move that was only a car changing shelves.
- **A conditioned price distribution says how it was produced.** `filtered` when
  the damage/mileage slice is itself big enough, `adjusted` when the pooled
  measured haircut is shifted onto the full scope instead, `unconditioned` when
  there is no measured haircut to shift by. The third case exists because
  claiming "adjusted" while applying a factor of 1.0 is a false label.
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
npm test           # vitest, pure logic only
npm run check:contrast
```

`npm test` covers the functions something *outside* the component tree depends
on, not rendering: `scopeKey` mirrors `ScopedToACar.build_scope_key` on the
server, and `toman` has to agree with the Telegram notifier quoting the same
price. Both tables are asserted on the Python side too, so a drift fails one
suite or the other rather than going unnoticed until a button reads wrong.

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

## The learned layer

Five models, and each one exists because the statistical method structurally
cannot answer its question. The cohort key is (model, trim, year), so a median
over it cannot use mileage, damage, city, seller type or photo count — eleven
stored columns the old scorer never read.

| Model | Answers | Judged on |
| --- | --- | --- |
| Quantile GBM (p10/p50/p90) | Where does this car's price actually sit, with a band | Interval coverage against 80%, then MAPE vs the peer median |
| Calibrated classifier | Will this listing leave the feed within 14 days | Brier score and a reliability curve, never accuracy |
| Isolation Forest | Is this cheap, or is the record broken | Lift over the base rate on realised delistings |
| Char n-gram TF-IDF + SGD | Does the ad text agree with the catalogue | Macro-F1 and the top confusable pairs |
| Per-variant KMeans | Which value tier is this car in | Silhouette, per variant, with a floor |

Four rules the whole layer is built on, in decreasing order of how easy they are
to get wrong:

- **The learned number never replaces the statistical one.** The discount badge
  is still measured against `peer_median` everywhere. The prediction sits beside
  it with its own decomposition, so a reader gets two independent accounts and
  can see where they disagree. `apps/core/pricing.py` records what happened the
  one time a fitted model *was* the number here.
- **Splits are by time, and by timestamp rather than row index.** A random split
  lets a model see July while predicting June. An index cut puts a bulk upload's
  duplicate rows on opposite sides.
- **A model goes live by winning, not by being newest.** It has to beat both the
  model it would replace *and* the plain statistical baseline, on the same
  holdout. Losers stay in shadow with the comparison published on
  `/methodology`.
- **The published band is conformalised.** Raw fitted quantiles are
  anti-conservative out of sample — measured here at 43% coverage on a p10–p90
  band while MAPE looked excellent. Coverage more than 8 points off 80% vetoes
  promotion outright, because no accuracy metric can see a dishonest interval.

```bash
python manage.py bama train --json          # fit all five, gate, rescore
python manage.py bama ml_train --only price --json
```

## Tests

```bash
pytest
```

Ten files, one per subject: `test_parsing` (pure Python, no DB), `test_verify`,
`test_ingest`, `test_fetcher`, `test_jobs`, `test_pricing`, `test_research`,
`test_api`, `test_ml`, plus `test_logical_fixes` for regressions that span
subjects. Shared fixtures are in `conftest.py`.

Most of `test_ml` runs without fitting anything — the metrics, the feature
builder, the time split and the promotion gate are pure functions, and that is
where the interesting mistakes live. The tests that matter most are the ones
asserting a model is *refused*: a suite that only checked the happy path would
have passed on every version of this code that shipped a broken model.
