# Performance baseline — 2026-08-13 (local Docker)

Captured before the cadence/API/UI refactor. Same host as portfolio-saas + twitter-saas.

## Containers (idle/active crawl)

| Container | RSS | CPU sample |
|-----------|-----|------------|
| bama-worker | 489 MiB | ~21% (sweep/pipeline) |
| bama-postgres | 175 MiB | ~13% |
| bama-django | 111 MiB | ~25% |
| bama-frontend | 98 MiB | ~0% |
| bama-mailpit | 22 MiB | ~0% |
| **sum** | **~895 MiB** | |

No Compose memory limits locally (Docker Desktop VM ~3.8 GiB).

## Database

Logical size **1537 MB**.

| Table | Live rows | On-disk |
|-------|-----------|---------|
| history_adversion | 2.6k | 611 MB |
| catalog_ad | 66k | 454 MB |
| history_adobservation | 640k | 223 MB |
| history_adchangeevent | 2.4k | 98 MB |
| history_listingepisode | — | 35 MB |
| market_priceobservation | 492 | 30 MB |
| analytics_dailyinventorysnapshot | 76k | 24 MB |
| history_pagecoverage | 15k | 3.6 MB |

## Pipeline step timings (JobRun, last 24h)

| Step | Typical | Worst observed |
|------|---------|----------------|
| fetch (delta, 500 ads) | 60–62 s | 43 s fail / 3109 s full |
| mark_inactive | 0.5–1.7 s | — |
| episodes | 49–91 s | **2662 s** |
| daily_snapshot | 1.3–1.7 s | — |
| market_index | 2.3–3.0 s | — |
| market_snapshot | 0.6–1.0 s | — |
| deal_scores (full rebuild) | 3.3–5.1 s | — |

HOT tick before refactor includes all of the above every ~5 min. Episodes dominate.

## API (localhost:8001, n=5)

| Endpoint | p95 | avg | size |
|----------|-----|-----|------|
| GET /api/health/ | 27 ms | 21 ms | 16 B |
| GET /api/ads/?page=1 | **992 ms** | 1031 ms | 72 KB |
| GET /api/analytics/deal-scores/ | 226 ms | 147 ms | 27 KB |
| GET /api/analytics/overview/ | 973 ms | 734 ms | 746 B |
| GET /api/research/liquidity/307/ | 401 (auth required) | — | — |

Ad list has no `select_related` on brand/model/variant/city.

## Frontend bundle (`ui/web/dist`, 2026-08-11)

| Chunk | Size |
|-------|------|
| `index-*.js` (main, includes ECharts via Overview) | **1.3 MB** |
| Research lazy | 5.1 KB |
| ControlApp lazy | 5.9 KB |
| Compare lazy | 1.9 KB |

## Post-refactor validation (same day)

- Migration `accounts.0003_drop_watchlists_saved_searches` applied.
- **386 passed** (`pytest` in Docker).
- `ui/web` `tsc --noEmit` clean.
- Worker boot log shows `pipeline=300s analytics=1800s` (HOT/WARM split live).
- Graphify AST rebuild completed after code changes.

