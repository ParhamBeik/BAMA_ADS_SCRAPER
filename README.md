# BAMA Ads Scraper & Deal Finder

Crawls `bama.ir` listings to surface fair vehicle values, detect deals, track market trends, and deliver buyer alerts.

Full documentation, stack details, and architecture live in [`bama-saas/README.md`](bama-saas/README.md) and [`bama-saas/ARCHITECTURE.md`](bama-saas/ARCHITECTURE.md).

## Quick Start

Run the entire application stack (PostgreSQL, Redis, Django API, crawler worker loop, and Vite frontend):

```bash
docker compose up --build
```

- Web UI: <http://localhost:5174>
- REST API: <http://localhost:8001>
- Django Admin: <http://localhost:8001/admin/>

## Running Tests

Run backend tests:

```bash
cd bama-saas && .venv/bin/pytest -q
```

Run frontend tests:

```bash
cd bama-saas/ui/web && npm test
```

## Structure

- `bama-saas/` — Django backend (`apps/`), React frontend (`ui/web/`), configuration (`config/`), and deployment definitions (`deploy/`).
- `bama-saas/ARCHITECTURE.md` — Layer boundaries, domain invariants, and pipeline architecture.
- `REFACTOR_REPORT.md` — Structural refactoring and consolidation audit log.
