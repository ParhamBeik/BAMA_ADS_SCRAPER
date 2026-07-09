# BAMA Project

This repo now contains two separate projects:

- `bama-scraper/` — the flat scraper/audit/analyze workflow with SQLite.
- `bama-saas/` — the PostgreSQL + FastAPI backend for the SaaS version.

The shared root keeps only repo-level docs, Docker Compose, graph outputs, and
ignore rules. The projects have separate dependencies and never import each other.

## How To Read It

- Start with `README.md` to choose the project.
- Read `bama-scraper/README.md` for the flat JSON/SQLite workflow.
- Read `bama-scraper/src/fetch.py`, `bama-scraper/src/history.py`, and `bama-scraper/src/audit.py` in that order.
- Read `bama-saas/README.md` for the API/PostgreSQL workflow.
- Use the graph outputs in `graphify-out/` as a navigation aid, not as source of truth.

## Layout

```text
.
├── bama-scraper/   # scraper-only project
├── bama-saas/      # SaaS backend project
├── docker-compose.yml
├── .gitignore
└── AGENTS.md
```

## Start Here

- Read `bama-scraper/README.md` for the scraper workflow.
- Read `bama-saas/README.md` for the backend/API/database workflow.
- Use `docker compose up --build` to start the SaaS API + Postgres stack.
- The root has no Python package or requirements file; work inside the selected project.
