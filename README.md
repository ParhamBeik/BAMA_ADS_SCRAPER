# BAMA Ads Scraper & Deal Finder

Personal vehicle deal finder for `bama.ir`: it crawls listings, stores listing
history, calculates fair prices and deal scores, tracks market movement, and
delivers alerts.

The application lives in [`bama-saas/`](bama-saas/). Start with the application
README, then read [`bama-saas/ARCHITECTURE.md`](bama-saas/ARCHITECTURE.md).

## Quick start

```bash
cd bama-saas
docker compose up --build
```

- UI: <http://localhost:5174>
- API: <http://localhost:8001>
- Admin: <http://localhost:8001/admin/>

## Checks

```bash
cd bama-saas
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/python manage.py makemigrations --check --dry-run
cd ui/web
npm run typecheck
npm test
npm run check:contrast
npm run build
```

## Important files

- `AGENTS.md` — repository rules for contributors and coding agents.
- `bama-saas/README.md` — product, API, jobs, and local setup.
- `bama-saas/ARCHITECTURE.md` — directory responsibilities and invariants.
- `bama-saas/deploy/` — worker, training, and deployment docs/scripts.
- `work/independent-review/` — historical audit evidence; it is not runtime code.
