# Bama SaaS Agent Notes

Local, single-operator market-intelligence app. Django 5.2 / DRF, PostgreSQL
only (GIN, `django.contrib.postgres`). `ui/web/` is the product UI (React +
Vite), seven screens, no auth. Schema lives in Django models and is migrated
via Django migrations only.

**Standalone.** No code or path dependency on any other repo. Payload rules
live in `apps/parsing/` — zero-Django (`extract_ad`, `parse_publish_time`/
Jalali, `payload_hashes`, `diff_payloads`, `pure_ad`, `unpack_payload`). The
live fetcher (`apps/jobs/services/fetcher.py`) has its own HTTP helpers. Seed
data is `data/bama.db` (gitignored).

Keep these architecture decisions intact:

- **Normalized dimensions + JSONB snapshot.** `Brand→Model→Variant`, `City`,
  `Dealer` are lookup tables. `Ad` is the current-snapshot row (pk=`code`)
  with hot denormalized columns plus `raw_payload` JSONB. Indexes: `(model,
  variant, year)`, `(model, current_price)`, GIN on `raw_payload`.
- **Append-only provenance.** `AdVersion` (semantic-hash dedup),
  `AdObservation` (one per run/ad), `AdChangeEvent` (only on a genuinely new
  version). Re-import is idempotent for `Ad`/`AdVersion`/`PriceObservation`;
  only `AdObservation` grows.
- **Change-only price history.** `core.PriceObservation`: one row per actual
  price change, not per sighting. Table names are `catalog_ad` /
  `history_adobservation` (not `core_*`).
- **`apps/parsing/` is authoritative** for payload rules; persistence is the
  consuming app's job. `extract_ad → parse_publish_time → ingest_ad` backs
  `fetch_live`.
- **Calendar-normalized model year.** `Ad.year` is provenance and is never a
  grouping or range-filter key. Cohorts and `?year=` use `Ad.year_jalali`
  (index `ad_market_jy_idx`). The `+621` offset in `apps/parsing/normalize.py`
  is for *model years* only.
- **Zero is a value.** `detail.mileage` is `"صفر کیلومتر"` for ~33% of ads.
  Use `parse_mileage` (returns `0`), never `parse_int(positive=True)`.
- **Verify, then quarantine.** `apps/jobs/services/verify.py` on every ingest.
  Soft rule ids → `Ad.quality_flags`. A hard failure writes `IngestReject`,
  returns `(None, False, False)`, and deletes any existing `Ad` with that
  code. Analytics reads only through `apps.core.services.quality.verified`
  (excludes `verify.HARD_RULE_IDS`). Soft flags must not drop otherwise-good
  data.
- **Fair price is the deal board.** `apps/core/services/fair_price.py`
  (`MIN_PEERS=8`, bucket-median mileage adj, no OLS). Deal scores are
  `discount_pct` against that baseline. Asking below half the peer median is
  dropped as a deposit/typo (`MIN_ASK_VS_MEDIAN`), as are حواله titles and
  the 10M-toman sentinel. Age is reported, not multiplied in.
- **Provenance, not inspect.** `GET /api/admin/ads/<code>/provenance/` is the
  unabridged record. Public `/api/ads/` stays curated. Worker boot runs
  `reap_orphan_runs`.

Crawl invariants (`apps/jobs/services/fetcher.py`) — do not simplify away:

- **`pageIndex` is 0-based.** `FIRST_PAGE = 0`.
- Feed is strictly recency-ordered, `rank = 30*page + 1..30`, ends on an
  empty page. No total-count field.
- Insertions push ads to higher ranks (harmless re-reads). Deletions pull
  ads to lower ranks past pages already read (**silent loss**). That is why
  `PageCoverage` and `crawl_gaps` exist.
- **Delta never resumes from a checkpoint** — always restart at page 0.
  Only `full`/`backfill` resume.

Settings: `config/settings/base.py` (shared, `AllowAny`), `dev.py` (Django
admin via `ensure_dev_admin`), `prod.py` (HSTS/secure cookies + anon
throttle). `manage.py` defaults to `config.settings.dev`. There is no JWT,
subscription, alert, or inspect surface.

## Worker cadences

- **HOT** (~5 min): `fetch_live` delta + `mark_inactive` + incremental
  `refresh_cohort_deal_scores` for models sighted in that fetch.
- **WARM** (~30 min): `sync_episodes` + `daily_snapshot` + `build_market_index`
  + `market_snapshot`.
- **COLD** (~6 h sweep): full fetch + gap repair + full deal-score rebuild +
  `prune_history` (90-day observations / coverage / job runs; last two
  completed sweeps' coverage is kept).
- No Celery/Redis. One compose `worker` loop (or host cron, never both).

## Layout

- `config/` — `settings/{base,dev,prod}.py`, `urls.py`, `wsgi.py`, `asgi.py`.
- `apps/core/{models,serializers,views}/` — catalog / history / market·price
  / analytics; `filters.py`, `urls.py`.
- `apps/accounts/` — `User` (Django admin) + `Favorite` (one operator's
  saved list). No auth routes.
- `apps/<app>/services/` — `core/services/{fair_price,deal_score,quality,
  index,liquidity,retention}.py`, `jobs/services/{ingest,fetcher,dimensions,
  pipeline,episodes,verify,health}.py`.
- `apps/jobs/management/commands/` — `fetch_live`, `crawl_gaps`,
  `crawl_health`, `compute_deal_scores`, `mark_inactive_ads`,
  `daily_snapshot`, `market_snapshot`, `build_market_index`, `sync_episodes`,
  `prune_history`, `run_pipeline`, `reap_orphan_runs`.
- `apps/parsing/` — pure-Python, no ORM.
- `tests/` — pytest-django; `test_parsing.py` has no DB dependency.

## API (surviving)

- Catalog: `/api/brands/`, `/api/ads/`, models/variants.
- Market: `/api/markets/`, `/api/ads/<code>/price-history/`.
- Analytics: `/api/analytics/deal-scores/`, `/api/analytics/market-index/`,
  `/api/analytics/overview/`.
- Research: `/api/research/{liquidity,price-position,depreciation}/<model_id>/`,
  `/api/ads/<code>/fair-price/`.
- Saved: `/api/favorites/`.
- Operator: `/api/admin/jobs/{fetch,refresh-analytics,deal-scores,crawl-health,overview}/`,
  `/api/admin/ads/<code>/provenance/`, `/api/admin/health/`.
- Docs: `/api/schema/`, `/api/docs/`, `/api/redoc/`. Health: `/api/health/`,
  `/api/db/health/`.

Update this file when the backend architecture changes.
