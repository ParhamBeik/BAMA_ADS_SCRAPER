# Architecture

## Mental model

```text
Bama.ir → jobs fetch/ingest → PostgreSQL → analytics/ML → Django API → React UI
```

The worker writes durable rows. The API reads those rows. Redis stores only
rebuildable derived data such as photo responses and deal-board windows.

## Directory responsibilities

- `config/`: settings, root URL routing, WSGI.
- `apps/common/`: parsing, normalization, rules, verification, and quality
  helpers. It imports no Django app.
- `apps/core/`: authoritative domain models, catalog/listing APIs, pricing,
  coverage, market research, images, and notifications.
- `apps/jobs/`: HTTP fetching, ingestion, scheduled jobs, health checks, and the
  `manage.py bama` command.
- `apps/ml/`: feature construction, training, model registry, inference, and
  monitoring. The optional ML dependency fails closed when absent.
- `apps/accounts/`: authentication, favorites, watchlists, alert rules, and
  the user alert inbox.
- `ui/web/`: React routes, shared components, API client, theme, and styling.
- `deploy/`: Compose deployment, worker/training loops, and deploy
  instructions.
- `tests/`: Django-backed backend tests.

## Dependency direction

```text
common ← core ← {jobs, ml, accounts} ← config
```

`config` assembles the apps. `jobs`, `ml`, and `accounts` may use `core` and
`common`; they must not import one another. The single documented exception is
`apps/core/pricing.py`, which reads ML predictions to show them on the deal
board.

## Runtime services

- `postgres`: source of truth.
- `redis`: bounded LRU cache; never authoritative.
- `django`: migrations, static collection, and Gunicorn API.
- `worker`: hot, coverage, warm, and maintenance cadences.
- `ml`: daily model training and scoring.
- `frontend`: production static bundle behind nginx.

## Pipeline rules

`apps/jobs/pipeline.py` is the scheduler's source of truth. Every step records a
`JobRun`. Failed prerequisites skip dependent calculations so stale data is not
published as fresh. `ml_train` must remain immediately before `ml_score`, and
`apps/ml.urls` must remain before `apps/core.urls` because the core ad route is
a catch-all.

## Security and data rules

- Production defaults are hardened; missing environment variables must not fail
  open.
- Authenticated responses are not publicly cached.
- Raw scraped payloads are operator-only.
- There are no backups. Storage changes follow `docs/STORAGE-POLICY.md`.
- Never print credentials, cookies, bot URLs, or database URLs.

## Change checks

Run the checks in the repository `AGENTS.md`. For changed shell scripts, run
`bash -n` or `sh -n`. For deployment changes, inspect the rendered Compose
configuration before applying it.
