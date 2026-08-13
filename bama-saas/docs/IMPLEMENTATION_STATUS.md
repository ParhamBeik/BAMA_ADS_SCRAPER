# Bama SaaS Implementation Status

Backend stack: **Django 5.2 + DRF + SimpleJWT + drf-spectacular + PostgreSQL**
(Python ≥ 3.11). PostgreSQL-only (GIN indexes, `django.contrib.postgres`) —
SQLite is not supported.

**382 tests pass. OpenAPI schema is warning-free.**

## What exists

| Area | State |
| --- | --- |
| Catalog | Normalized `Brand→Model→Variant→City→Dealer→Ad`; `Ad` pk is `code`, hot columns + `raw_payload` JSONB with GIN. |
| Provenance | Append-only `FetchRun`, `AdVersion` (semantic-hash dedup), `AdObservation`, `AdChangeEvent`, `PageCoverage`, `IngestReject`. |
| Price history | Change-only `PriceObservation` + `PriceDropEvent`. |
| Crawler | `fetch_live` delta/full/backfill, adaptive 429/5xx backoff, checkpoint resume (SIGTERM-safe), confirmed end-of-feed, `PageCoverage` ledger, `crawl_gaps` repair. |
| Verification | **Four layers.** Row rules (`verify.py`, incl. cross-field contradictions) → temporal rules vs the ad's own past (`verify_temporal.py`) → cohort-relative outliers by median/MAD (`verify_cohort.py`) → daily distribution drift (`drift.py`). Hard failures quarantine in `IngestReject`; everything else flags. Reads go through `quality.verified` / `verified_by_ad` / `without_cohort_outliers`. |
| Identity | `VehicleIdentity` + `ListingEpisode`. Cars are matched on Bama's per-vehicle image folder uuid, which it reuses across relistings — 65 uuids cover 139 codes. Shared identity is classified by dates: overlapping = duplicate listing, sequential = relist. |
| Insight products | **Liquidity** (Kaplan-Meier with right-censoring, hazard by price position), **fair price** (explainable components + measured negotiation room + dispersion leaderboard), **retention** (per-year medians, cohort-adjusted regional spreads). |
| Job visibility | `JobRun` records every scheduled step including `skipped` when a prerequisite failed. `GET /api/admin/jobs/overview/`. |
| Frontend | `ui/web/` — React + Vite + TypeScript: Explorer, Deals, Research, Compare, My Market, Control. |
| Analytics | true-mean, Bollinger (median-based), liquidity, market depth, undervalued, depreciation, rankings, regional, dealers, inventory trend, time-on-market, fast movers, price drops, mileage-adjusted deal scores. |
| **Market index** | **Matched-cohort chained index** (`services/index.py`) — the composition-controlled answer to "did prices move". Market/brand/model scopes. |
| Monitoring | `crawl_health` — sweep freshness, failed runs, ingest-reject spikes, coverage gaps, ingest progress. CLI (exit 1) + `GET /api/admin/jobs/crawl-health/` (503). |
| Engagement | Favorites, alerts, notification inbox; subscription-aware throttles on writes. |
| Worker | HOT/WARM/COLD cadences via cron (`install_cron.sh`) **or** the in-container loop (`run_worker.sh`); they refuse to run together. |

## The market index — why it exists

The raw market median answers "what does a car cost today", which is *not*
"did prices move". Measured on the live 54k-ad database:

| Window | Raw median | Matched-cohort index |
| --- | --- | --- |
| Jul 5 → Aug 7 | **−6.7%** | **+0.45%** |

The median's swing was not a market move. On Jul 16 crawl coverage collapsed to
3,063 ads and the median "rose" 37%; when coverage recovered it "crashed" 31%.
The index reported ~0% through both, because it only ever compares a cohort
against itself and cohorts present on just one of two dates contribute no
return. The old `price-trends` endpoint bucketed on `observed_at` — crawl time —
so it was measuring crawler behaviour and calling it the market.

## Data-integrity invariants

- **Cohort key is `year_jalali`, never raw `Ad.year`.** Bama publishes model
  years in either calendar depending on brand (36,782 Jalali vs 20,480
  Gregorian), so `year` mixes 1399 and 2025 in one column.
- **Zero kilometres is 0, not NULL** (~33% of ads).
- **Removal is proven, not guessed.** An ad is `REMOVED` when absent from the
  last two *completed* (`reached_end`) sweeps. The old `STALE_AFTER_DAYS=14`
  wall-clock rule, against a 6-hourly sweep, had left `removed_count` at 0 on
  every snapshot date ever taken. Fewer than two sweeps on record ⇒ nothing is
  marked, so a stalled crawler can never be read as an empty market.
- **Deal scores are mileage-adjusted** to the cohort's median mileage, using the
  slope `insights.depreciation()` already fits — applied only when that fit is
  available, explains enough variance, and slopes downward.
- **Leaving the feed is not a sale.** `fast_movers` / `days_to_delist`, never
  "sold": the payload cannot distinguish sold from expired from withdrawn.

## Worker pipeline

Every ~5 min: `fetch → mark_inactive → episodes → daily_snapshot → market_index →
market_snapshot → deal_scores`.

Every 6 h (sweep): `fetch_live --mode full → crawl_gaps → flag_cohort_outliers →
data_quality → crawl_health`.

Steps record a `JobRun` either way, and a step whose declared prerequisite failed
is recorded as `skipped` rather than silently not running — `market_index` is
chained arithmetic over `daily_snapshot`'s rows, so running it on a failed
snapshot would publish the gap as a real market move. A failed *fetch*
deliberately does not cascade: the local steps are idempotent maintenance over
stored data, and one flaky minute should not cost a day of snapshots.

`refresh_analytics` is gone entirely, along with the `PriceStatistics` table it
wrote — no view, serializer or service ever read it.

## Known gaps / deferred

- **Catalog aliases are detected but not merged.** Bama renames models in ad
  titles (`تیگو 8 پرو مکس (F8 PRO MAX)` → `(F8)`), and each spelling becomes its
  own catalog row, so one car's cohort is split across two. `confirm_dimensions
  --aliases` reports them, keyed on the same ad code appearing under two names.
  Merging needs a human: some near-matches (`سوناتا` vs `سوناتا هیبرید`) are
  genuinely different cars, and a wrong merge is unrecoverable.
- **Survival medians go degenerate on backfilled history.** `mark_inactive_ads`
  closed thousands of episodes at one timestamp, so durations cluster and every
  cohort's median lands on the same day. Fixed-horizon survival
  (`still_listed_at_30d`) reads correctly through it and is reported alongside;
  the medians will spread out as episodes close organically.
- **No real-terms (deflated) series.** Prices are nominal Toman, so an
  inflationary rise and a real one look identical. Deliberate — adding an
  FX/CPI deflator means a second ingest source with its own reliability story.
- **Detail pages are not fetched** — and are not needed. The listing feed already
  carries description, images, specs, dealer and authenticity data; description
  length, image count, seller authentication and the source's modified timestamp
  are promoted to typed columns. Engine, battery, range and promotion state stay
  in the JSONB until something reads them.
- **`new_count` means first-*seen*, not newly-published.** Median lag between
  `publish_at` and `first_seen_at` is ~8 days, so backfill inflates it.
- **Celery/Redis still stubbed**; the worker is cron/loop + daemon threads.
- **Email** defaults to the console backend; Telegram is a no-op without
  `TELEGRAM_BOT_TOKEN`.

## Verification

```bash
docker compose up -d postgres worker
python manage.py migrate
pytest                                    # 382 passing
python manage.py spectacular --format openapi-json --urlconf config.urls

python manage.py backfill_snapshots --days 40   # history from provenance
python manage.py daily_snapshot
python manage.py build_market_index
python manage.py crawl_health             # exit 1 when unhealthy

curl -s 'localhost:8000/api/analytics/market-index/?days=90' | jq
```

---

**Last updated:** 2026-08-09
