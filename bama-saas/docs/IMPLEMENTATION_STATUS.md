# Implementation status

Local Docker Compose deal finder. Django 5.2 + DRF + drf-spectacular +
PostgreSQL (Python ≥ 3.11). No JWT, login, subscriptions, multi-user alerts,
or public SaaS. One optional local Telegram deal notifier is disabled by
default. OpenAPI schema is warning-free.

## What exists

| Area | State |
| --- | --- |
| Catalog | `Brand→Model→Variant→City→Dealer→Ad`; `Ad` pk is `code` + `raw_payload` JSONB. |
| Provenance | Append-only `FetchRun`, `AdVersion`, `AdObservation`, `PageCoverage`, `IngestReject`. |
| Price history | Change-only `PriceObservation` + `PriceDropEvent`. |
| Crawler | `fetch_live` delta/full/backfill, `PageCoverage`, `crawl_gaps`. |
| Verification | Row rules in `verify.py`. Hard failures → `IngestReject` + drop `Ad`. Reads through `quality.verified`. |
| Analytics | Fair price, deal board (min 8 peers, ask ≥ 50% of peer median), matched-cohort market index, Kaplan–Meier time-to-sell, year retention. |
| Saved cars | `Favorite` — one local list, no user accounts. |
| Notifier | Optional singleton Telegram deal notifier; disabled by default, one send per ad, capped at ten per run. |
| Job visibility | `JobRun`; `/control` + `GET /api/admin/jobs/overview/`. |
| Frontend | Seven screens: `/`, `/explore`, `/listing/:code`, `/market`, `/research/:modelId`, `/saved`, `/control`. Record inspection is Django admin. |
| Worker | HOT/WARM/COLD via `run_worker.sh`; HOT evaluates the optional notifier after deal-score refresh. No Celery. |

## Market index

Raw median ≠ "did prices move". Jul 5 → Aug 7: raw median **−6.7%**, index
**+0.45%**. The median was reporting crawl coverage (Jul 16 collapsed to 3,063
ads). The index only compares a cohort against itself.

## Invariants that still hold

- Cohort key is `year_jalali`, never raw `Ad.year`.
- Zero kilometres is 0, not NULL.
- Removal is absence from the last two completed (`reached_end`) sweeps.
- Leaving the feed is not a sale; Kaplan–Meier censors still-listed cars.

## Known gaps

- Catalog aliases (one car, two names) are split across cohorts; merging needs a human.
- Survival medians can cluster on backfilled history; `still_listed_at_30d` is reported alongside.
- Prices are nominal Toman — no CPI/FX deflator.
- Detail pages are not fetched; the listing feed already carries what the UI uses.
- `new_count` is first-*seen*, not newly-published.

## Verification

```bash
docker compose up -d
pytest
python manage.py spectacular --format openapi-json --urlconf config.urls
python manage.py crawl_health
curl -s 'localhost:8001/api/analytics/market-index/?days=90' | jq
```

**Last updated:** 2026-08-16
