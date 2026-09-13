# Test and verification evidence

## Local frontend

- `npm test -- --run`: PASS — 2 test files, 30 tests.
- `npm run typecheck`: PASS.
- `npm run check:contrast`: PASS — light/dark contrast and hue checks all passed.
- `npm run build`: PASS — production Vite build completed; it emitted only the existing warning
  that the chart chunk exceeds 500 kB.

## Local backend

- `.venv/bin/python manage.py check --deploy --fail-level WARNING` with a generated 64-byte
  temporary `SECRET_KEY`: PASS — no issues.
- `.venv/bin/pytest -q`: 307 passed, 1 warning, 488 errors. The errors are database-dependent
  tests failing to connect to the unavailable local PostgreSQL service at `localhost:5433`; this
  is an environment limitation, not a passing backend suite. The result is deliberately not
  reported as green.
- System `python3 -m pytest -q`: not usable because that interpreter has no pytest installation.
- `ruff`: unavailable in the local environment; no lint pass is claimed.

## Read-only VPS

- Deployed checkout and `origin/main` both resolved to
  `3c89d624541e4280ddacf1c0388c8af19e5ef998` at audit time.
- `showmigrations --plan` reported no pending migrations.
- BAMA Postgres/Redis/Django/frontend containers were healthy; worker and ML were running without
  healthchecks.
- Latest worker log showed successful fetch, coverage, scoring, sold-probe, alert, and notify
  steps. ML logs also showed scikit-learn 1.9.0 artifacts being loaded by 1.9.1, recorded as F-003.
