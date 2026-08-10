# Bama SaaS Agent Notes

- Backend-first. `ui/legacy/` is a working no-build SPA (vanilla ES modules, hash
  router, vendored Chart.js) — not a placeholder.
- The SaaS is a Django 5.2 / DRF project. Schema lives in Django models and is
  migrated via Django migrations only — there is no Alembic, no SQLAlchemy, no
  FastAPI app. SQLite is **not** supported (PostgreSQL-only: GIN indexes,
  `django.contrib.postgres`).
- **Standalone project.** No code or path dependency on any other repo/folder.
  The single source of Bama payload rules is `apps/parsing/` — zero-Django,
  pure-Python (`extract_ad`, `parse_publish_time`/Jalali, `payload_hashes`,
  `diff_payloads`, `pure_ad`, `unpack_payload`). The live fetcher
  (`apps/jobs/services/fetcher.py`) contains its own HTTP helpers. Seed data
  lives in this project's own `data/` dir (`data/bama.db`, gitignored).
- Keep these architecture decisions intact:
  - **Normalized dimensions + JSONB snapshot.** `Brand→Model→Variant`, `City`,
    `Dealer` are normalized lookup tables. `Ad` is the current-snapshot row
    (pk=`code`) with hot denormalized columns (year/mileage/current_price/
    publish_at…) for fast filter/sort, plus the full `raw_payload` JSONB for
    the long tail. Indexes: `(model, variant, year)`, `(model, current_price)`,
    GIN on `raw_payload`.
  - **Append-only provenance.** `AdVersion` (deduped by semantic hash),
    `AdObservation` (one per run/ad), `AdChangeEvent` (only on a genuinely new
    version vs the previous observation). Re-importing the same data is
    idempotent for `Ad`/`AdVersion`/`PriceObservation`; only `AdObservation`
    grows.
  - **Change-only price history.** `core.PriceObservation` is the
    price-through-time backbone: one row per actual price change (fingerprint
    dedup vs the ad's immediately-preceding observation), not per sighting.
    Keeps Bollinger / true-mean / trend series clean.
  - **`apps/parsing/` is authoritative for payload rules**; persistence is the
    consuming app's job. The same pipeline (`extract_ad → parse_publish_time →
    ingest_ad`) backs `import_scraped`, `import_history`, and `fetch_live`.
  - **Calendar-normalized model year.** Bama publishes model years in *either*
    calendar depending on brand (measured: 36,782 Jalali vs 20,480 Gregorian
    across 57,262 ads, and 20+ brands use both). `Ad.year` keeps the raw value
    for provenance and is **never** a grouping or range-filter key. Cohorts and
    `?year=` filters use `Ad.year_jalali` (index `ad_market_jy_idx`); the flat
    `+621` offset in `apps/parsing/normalize.py` is correct for *model years*
    only, not for date arithmetic.
  - **Zero is a value, not a null.** `detail.mileage` is `"صفر کیلومتر"` for
    ~33% of ads. Use `parse_mileage` (returns `0`), never
    `parse_int(positive=True)`, which maps it to `None` and silently drops every
    brand-new car from mileage filters and statistics.
  - **Verify, then quarantine the payload and drop the row.**
    `apps/jobs/services/verify.py` runs on every ingest. Soft rule ids land in
    `Ad.quality_flags`. A **hard** failure means Bama itself sent an unusable
    value, so no amount of re-parsing repairs it: `ingest_ad` writes an
    `IngestReject` row (raw payload retained, so a wrong rule stays replayable),
    returns `(None, False, False)`, and deletes any existing `Ad` with that code.
    The `Ad` table therefore never holds a hard-flagged row — flagging alone was
    not enough, because the next fetch's upsert simply re-inserted whatever a
    cleanup pass had deleted. `backfill_normalization` applies the same rule
    retroactively and purges legacy hard-flagged rows. Analytics reads **only**
    through
    `apps.core.services.quality.verified`, which excludes exactly
    `verify.HARD_RULE_IDS` — soft flags are for monitoring and must not remove
    otherwise-good data. A spike in one rule means Bama changed their schema.
- Crawl invariants (`apps/jobs/services/fetcher.py`) — verified against the live
  API, do not "simplify" any of these away:
  - **`pageIndex` is 0-based.** `pageIndex=0` returns ranks 1..33; starting at 1
    silently skips the ~30 newest ads on every run. `FIRST_PAGE = 0`.
  - The feed is **strictly recency-ordered**, `rank = 30*page + 1..30`, and ends
    naturally (~936 pages / ~28k ads) with an empty page — there is no
    total-count field and no server-side depth cap.
  - Insertions push ads to *higher* ranks → harmless re-reads. Deletions pull ads
    to *lower* ranks, past pages already read → **silent loss**. That asymmetry
    is the only reason `PageCoverage` and `crawl_gaps` exist; early stopping
    alone can never prove coverage.
  - **Delta never resumes from a checkpoint** — it must always restart at page 0.
    Only `full`/`backfill` resume. Coverage is a fact in `PageCoverage`, not a
    guess from elapsed wall-clock time.
- Settings are split: `config/settings/base.py` (shared), `dev.py`
  (`AllowAny` for local reads), `prod.py` (`IsAuthenticated`, HSTS/secure
  cookies). `manage.py` defaults to `config.settings.dev`.
- Subscription-aware throttles in `apps/accounts/throttles.py`
  (`SubscriptionThrottle`, `MonthlyQuotaThrottle`) are attached to every
  engagement **write** viewset; read endpoints and the operator-only admin job
  triggers are unthrottled.
- Update this file when the backend architecture changes.

## Directory / file index convention

- `config/` — project package: `settings/{base,dev,prod}.py`, `urls.py`,
  `wsgi.py`, `asgi.py`.
- `apps/core/{models,serializers,views}/` — packages split by theme
  (catalog / history / market·price / analytics); `filters.py`, `urls.py`.
- `apps/accounts/{models,views,urls,serializers}.py` — the user/auth surface.
- `apps/<app>/services/` — non-trivial logic (e.g.
  `core/services/{bollinger,truemean,insights,metrics,deal_score}.py`,
  `jobs/services/{ingest,fetcher,dimensions,pipeline}.py`).
- `apps/jobs/management/commands/*.py` — CLI entry points (`import_scraped`,
  `import_history`, `refresh_analytics`, `fetch_live`).
- `apps/parsing/` — pure-Python, no ORM. Re-exported via `apps/parsing/__init__.py`.
- `apps/<app>/migrations/` — Django migrations (version-controlled).
- `tests/` — pytest-django; `test_parsing.py` has no DB dependency.

## Architecture Change Log

- 2026-08-08: **Composition-controlled market index + crawl monitoring.**
  - **`DailyInventorySnapshot` is cohort-keyed on `year_jalali`** (column
    renamed from `year`; migration `core.0003` purges the pre-rename rows). It
    had been grouping on the raw mixed-calendar `Ad.year` — the one thing this
    file says is never a grouping key — so every cohort was split in two. It is
    now also the *input to the market index*, so its key had to be right first.
  - **`apps/core/services/index.py` — matched-cohort chained index.** Never
    compares different cars: per-cohort day-over-day return, weighted by the
    smaller side's ad count, chained from base 100. Cohorts present on only one
    of two dates shift weights but contribute no return, so listing churn cannot
    move it. Measured Jul 5 → Aug 7: raw median −6.7%, index +0.45% — the
    median had been reporting *crawl coverage* as market movement (Jul 16
    coverage fell to 3,063 ads and the median "rose" 37%). Do not "simplify"
    this back into a median over live listings.
  - **`backfill_snapshots`** reconstructs historical cohort rows from
    `AdObservation` + `PriceObservation`. Liveness comes from actual sightings,
    never `Ad.removed_at`: under the old 14-day rule `removed_at` ran ~2 weeks
    late, so trusting it would have held delisted cars in their cohorts for a
    fortnight after they were gone. Applying the new rule for the first time
    marked 11,131 ads REMOVED in one pass — that backlog is the size of the
    error the wall-clock rule had been carrying.
  - **Removal is proven by coverage, not wall-clock.** `mark_inactive_ads`
    marks an ad REMOVED when it is absent from the last **two** completed
    (`reached_end`) sweeps; two, because a deletion can pull an ad past a page
    a single sweep already read. With fewer than two such sweeps it marks
    **nothing** — a stalled crawler must never read as an empty market.
    `STALE_AFTER_DAYS` is deleted; do not reintroduce it.
  - **`verified()` coverage completed.** `daily_snapshot`, `market_snapshot`,
    `markets()`, `time_on_market`, `fast_movers`, `price_drops` all route
    through it now; new `quality.verified_by_ad()` covers the price-side tables
    (`PriceObservation`, `PriceDropEvent`) that `verified()` structurally could
    not reach, and `bollinger` moved from `Avg` to median.
  - **Deal scores are mileage-adjusted** to the cohort's median mileage using
    `insights.depreciation()`'s fitted slope — applied only when the fit is
    available, clears `MIN_FIT_R_SQUARED`, **and slopes downward**. A positive
    fitted slope means the regression found a confounder, not depreciation.
  - **Crawl fixes:** an empty page is confirmed with a second request before
    being believed (a throttled 200 + `[]` is byte-identical to end-of-feed, and
    believing it truncated the sweep *and* stamped `reached_end`);
    `crawl_gaps` ceilings on `coverage.known_feed_depth()` (deepest rank of the
    last completed sweep) so a truncated tail is visible as a gap; SIGTERM is
    trapped so `docker compose stop` checkpoints instead of restarting at page 0.
  - **`apps/jobs/services/health.py` + `crawl_health`** — the telemetry already
    existed (`FetchRun`, `PageCoverage`, `IngestReject`) and nothing read it.
    Exit 1 / HTTP 503 when unhealthy; runs after every sweep.
  - **Removed waste:** `AnalyticsCache` deleted (referenced nowhere);
    `refresh_analytics` moved off the 5-minute tick to the sweep (nothing reads
    `PriceStatistics`); the duplicate cohort deal-score refresh in `fetch_live`
    dropped (the pipeline's `deal_scores` step already covers it).
  - **Naming:** `fast_sellers`→`fast_movers`, `days_to_sell`→`days_to_delist`.
    Leaving the feed is not evidence of a sale and the payload cannot tell us.

- 2026-07-26: **State-aware crawling + data verification.** Fixed the 0-based
  `pageIndex` bug (every run had been skipping the ~30 newest ads since launch).
  Added `FetchRun.{mode,start_page,pages_fetched,deepest_rank,reached_end,
  stop_reason,resume_from_page}`, the `PageCoverage` ledger, `coverage.find_gaps`
  / `plan_backfill`, the `crawl_gaps` command, delta/full/backfill modes with
  adaptive 429/5xx backoff + checkpoint-resume, and a 6-hourly `run_sweep.sh`
  cron entry. `AdObservation.rank` is now populated (the column existed but
  nothing wrote it). Replaced the wall-clock watermark heuristic with the
  coverage ledger. **Data-integrity fixes:** calendar-normalized model year
  (`year_jalali`/`year_gregorian`/`year_calendar`) — mixed calendars had been
  splitting every peer cohort; zero-km mileage no longer collapses to NULL
  (~33% of ads); new `verify.py` rule set writing soft ids to
  `Ad.quality_flags`, while a hard failure quarantines the payload in
  `IngestReject` and keeps the ad out of (or deletes it from) the `Ad` table
  entirely, with all analytics routed through `quality.verified` as a second
  line of defence. `Ad.canonical_path` is populated. Backfill for existing
  rows: `manage.py backfill_normalization` (repairs what it can, purges what it
  cannot). Migration `core.0002`.
- 2026-07-20: **Merged 7 apps → 4** (`catalog`+`history`+`market`+`analytics`
  → `apps/core`, with `models/`/`serializers/`/`views/` split by theme;
  `accounts`, `jobs`, `parsing` unchanged). All `db_table` names and every
  `/api/` path preserved; migrations reset (`core.0001`, `accounts.0001`).
  **Decoupled from the sibling scraper project**: seed data now lives in this
  project's own `data/bama.db`, docker-compose mounts `./data`, and no code,
  path, or doc references any external project.
- 2026-07-05: Rebuilt the backend around Alembic-managed PostgreSQL tables,
  live Bama ingestion, immutable sightings, change-only price history,
  DB-native audits, protected tracked background jobs, and public
  catalog/history/insight APIs. Removed the stale frontend, pandas analytics,
  cross-project fetch-core copy, and startup `create_all()`.
- 2026-07-05: Added append-only payload versioning and classified change
  events. Observations now link to immutable semantic versions, repeated
  content reuses versions, reverted content creates a visible transition,
  audits check version/event integrity, and history APIs expose versions,
  changes, and combined timelines.
- 2026-07-16: **Rewrote the SaaS from FastAPI + SQLAlchemy + Alembic to
  Django 5.2 + DRF + SimpleJWT + Django migrations.** The app moved from
  `app/` to the `config/` + `apps/` layout; models are now Django ORM models
  (UUID-keyed email `User` + `Subscription`; normalized catalog; append-only
  history; change-only `PriceObservation`; `PriceStatistics`/`AnalyticsCache`).
  Auth uses SimpleJWT (15 m access / 7 d rotating+blacklisted refresh). Added
  the live `fetch_live` management command and `apps/jobs/services/fetcher.py`
  (mirrors the scraper's HTTP helpers inline). Analytics (Bollinger,
  true-mean, liquidity, market-depth, undervalued, depreciation) and the full
  read API were reimplemented as DRF views. **Bugs fixed during end-to-end
  verification:** `Ad` pk is `code`, not `id` — `Count("id")` on `Ad`
  querysets raised `FieldError`, fixed to `Count("code")` in
  `refresh_analytics` and the `markets` view; `AdSerializer`/`VariantSerializer`
  declared `IntegerField(source="model_id"…)` with `source` equal to the field
  name, which DRF rejects — removed the redundant `source`; the `markets` view
  used `.values(model_name="model__name_fa", …)` (string-lookup kwargs removed
  in modern Django) — rewritten to use `F()` annotations.
