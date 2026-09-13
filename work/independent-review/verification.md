# Verification evidence — 2026-09-12

## Local checks

All commands below ran from the current checkout and exited successfully unless noted.

| Command | Result |
| --- | --- |
| `git diff --check` | Pass. |
| `git diff --check b910179..3c89d62` | Pass. |
| `cd bama-saas && uv lock --check` | Pass. |
| `cd bama-saas && uv run ruff check .` | Pass. |
| `DJANGO_SECRET_KEY=independent-review DJANGO_DEBUG=1 uv run python manage.py check` | Pass. |
| `DJANGO_SECRET_KEY=independent-review DJANGO_DEBUG=1 uv run python manage.py makemigrations --check --dry-run` | No changes; PostgreSQL connection at `localhost:5433` was unavailable, so this is not a database-backed migration proof. |
| `cd bama-saas/ui/web && npm run typecheck` | Pass. |
| `npm run test -- --run` | 2 files, 30 tests passed. |
| `npm run check:contrast` | Pass for light and dark token checks. |
| `npm run build` | Pass. Vite reported the documented lazy chart-chunk size warning. |

## CI, deployment, and VPS

* CI `34712639679` for `3c89d624` is terminal success: PostgreSQL backend, hardened backend/deployment checklist, lint, frontend typecheck/tests/contrast/build/Lighthouse.
* Deployment `34712639916` for the same SHA is terminal success, including VPS restart and smoke test.
* Read-only VPS checks found local checkout, `origin/main`, and `/opt/apps/BAMA_ADS_SCRAPER` at `3c89d624`; frontend, Django, PostgreSQL, and Redis are healthy; no migration is pending.
* External HTTPS `/api/health/` and `/api/db/health/` returned `{"status":"ok"}`; root returned HTTP 200 with the expected security headers.

## Fresh browser evidence

* `/listing/emt1opqj`: 390px viewport had no horizontal overflow; six gallery images were loaded and all had empty alternative text; no console warnings or errors.
* `/deals`: 390px viewport had no document overflow; the tab strip had a 428px scroll width within a 332px viewport but no unwanted page scrollbar; ArrowRight from the second tab selected the visually right tab; no console warnings or errors.
* No account, alert, saved item, settings, job, or admin mutation was performed.
