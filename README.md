# BAMA Project

A market-intelligence platform for the Iranian used-car market. This repo is a
monorepo with two decoupled projects:

- **`bama-scraper/`** — the local data-acquisition tool. A flat, stdlib-only
  Python workflow (`fetch.py` → `audit.py` → `analyze.py`, plus `history.py`)
  that pulls car listings from **bama.ir**, writes pure-payload JSON files under
  `data/BAMA ADS/**/ads.json`, and keeps a SQLite provenance store
  (`code_map.db` + `history.db`). No web framework, no ORM.
- **`bama-saas/`** — the **SaaS product backend**: a Django 5.2 + DRF +
  PostgreSQL service (normalized catalog, append-only provenance, change-only
  price-through-time, and derived market analytics). This is the project being
  productized.

The two projects share **no code** — only data. The SaaS imports the scraper's
JSON/SQLite output (`import_scraped`, `import_history`) or fetches live itself
(`fetch_live`), and the payload rules are re-implemented (not imported) in
`bama-saas/apps/parsing/`.

## Layout

```text
.
├── bama-scraper/   # data-acquisition tool (JSON + SQLite, no web layer)
├── bama-saas/      # SaaS backend (Django + DRF + PostgreSQL)
├── docker-compose.yml   # root entry point; includes bama-saas/docker-compose.yml
├── graphify-out/   # local knowledge-graph output (gitignored)
└── .gitignore
```

## Start here

- Building / running the product → **`bama-saas/README.md`** (Django + DRF +
  PostgreSQL quick-start, management commands, full API surface).
- The scraper workflow → **`bama-scraper/README.md`** (fetch → audit → analyze).
- Agent notes for the backend → **`bama-saas/AGENTS.md`**.

## Run the SaaS stack

The whole stack is started from the repo root with one command (the root
`docker-compose.yml` includes the canonical `bama-saas/docker-compose.yml`):

```bash
docker compose up --build            # postgres :5432 + django :8000
docker compose --profile dev up      # also start pgadmin on :5050
```

> Requires the Compose v2 plugin (`docker compose …`) or the standalone
> `docker-compose` binary. A Docker daemon must be running.

For local development without Docker (PostgreSQL must be reachable), see
`bama-saas/README.md`.
