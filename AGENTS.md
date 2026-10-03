# BAMA Ads Scraper contribution guide

## Project shape

- `bama-saas/` is the application root.
- `apps/common/` contains framework-light leaves.
- `apps/core/` owns domain models and analytics.
- `apps/jobs/` fetches and processes Bama listings.
- `apps/ml/` trains and scores optional learned models.
- `apps/accounts/` owns users, favorites, watchlists, and alerts.
- `ui/web/` is the React frontend.

Dependencies flow `common → core → {jobs, ml, accounts} → config`.

## Architecture

Read before changing structure, data flow or app boundaries. Claude Code loads it at session start:

@bama-saas/ARCHITECTURE.md

## Required checks

From `bama-saas/`:

```bash
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/python manage.py makemigrations --check --dry-run
```

From `bama-saas/ui/web/`:

```bash
npm run typecheck
npm test
npm run check:contrast
npm run build
```

PostgreSQL is required. Do not use SQLite. Local Compose publishes PostgreSQL on
host port `5433`.

## Safe changes

- Before changing anything that stores data (photos, payloads, tables, volumes,
  caches), read and follow `bama-saas/docs/STORAGE-POLICY.md`. The VPS has no
  backups and a 100 GB disk shared by four apps.
- Preserve unrelated dirty files, especially `work/` audit material.
- Do not print secrets, tokens, cookies, or database URLs.
- Keep `apps/ml.urls` before `apps.core.urls` in `config/urls.py`.
- Keep `ml_train` immediately before `ml_score` in `apps/jobs/pipeline.py`.
- Run `bash -n` or `sh -n` on changed deployment scripts.
- Do not deploy, push, or mutate production without explicit authorization.
