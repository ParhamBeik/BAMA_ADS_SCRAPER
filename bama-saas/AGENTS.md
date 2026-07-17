# Bama SaaS Agent Notes

- Backend-first; a frontend is deferred (the `frontend/` dir is a placeholder).
- The SaaS is a Django 5.2 / DRF project. Schema lives in Django models and is
  migrated via Django migrations only — there is no Alembic, no SQLAlchemy, no
  FastAPI app. SQLite is **not** supported (PostgreSQL-only: GIN indexes,
  `django.contrib.postgres`).
- Do **not** import from `bama-scraper`. The single source of Bama payload
  rules is `apps/parsing/` — a zero-Django, pure-Python port of
  `bama-scraper/src/fetch.py` (`extract_ad`, `parse_publish_time`/Jalali,
  `payload_hashes`, `diff_payloads`, `pure_ad`, `unpack_payload`). The live
  fetcher (`apps/jobs/services/fetcher.py`) mirrors the scraper's HTTP helpers
  inline rather than importing them.
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
  - **Change-only price history.** `market.PriceObservation` is the
    price-through-time backbone: one row per actual price change (fingerprint
    dedup vs the ad's immediately-preceding observation), not per sighting.
    Keeps Bollinger / true-mean / trend series clean.
  - **`apps/parsing/` is authoritative for payload rules**; persistence is the
    consuming app's job. The same pipeline (`extract_ad → parse_publish_time →
    ingest_ad`) backs `import_scraped`, `import_history`, and `fetch_live`.
- Settings are split: `config/settings/base.py` (shared), `dev.py`
  (`AllowAny` for local reads), `prod.py` (`IsAuthenticated`, HSTS/secure
  cookies). `manage.py` defaults to `config.settings.dev`.
- Subscription-aware throttles exist in `apps/accounts/throttles.py`
  (`SubscriptionThrottle`, `MonthlyQuotaThrottle`) but are **not** attached to
  any view yet — per-endpoint tier gating is pending.
- Update this file when the backend architecture changes.

## Directory / file index convention

- `config/` — project package: `settings/{base,dev,prod}.py`, `urls.py`,
  `wsgi.py`, `asgi.py`.
- `apps/<app>/{models,views,urls,serializers,filters}.py` — the DRF surface.
- `apps/<app>/services/` — non-trivial logic (e.g.
  `analytics/services/{bollinger,truemean,insights}.py`,
  `jobs/services/{ingest,fetcher,dimensions}.py`).
- `apps/jobs/management/commands/*.py` — CLI entry points (`import_scraped`,
  `import_history`, `refresh_analytics`, `fetch_live`).
- `apps/parsing/` — pure-Python, no ORM. Re-exported via `apps/parsing/__init__.py`.
- `apps/<app>/migrations/` — Django migrations (version-controlled).
- `tests/` — pytest-django; `test_parsing.py` has no DB dependency.

## Architecture Change Log

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
