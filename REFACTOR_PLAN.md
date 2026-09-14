# Refactor plan — simplification and architectural consolidation

Phase 0 (recon) output. Read-only: no source file was modified to produce this.

## Summary of the finding

**This repository is already close to the target state.** 215 tracked files, one
conventional Django 5.2 project, one Vite SPA, a clean working tree, zero
committed build artifacts, zero `TODO`/`FIXME` markers anywhere, and nearly every
non-obvious line carries a written rationale naming the incident that produced
it. Exactly **two functions** are dead in the entire backend. **No unused
dependency** exists on either side of the stack.

So the work is structural, not volumetric. What is genuinely wrong:

- A committed debug block writes to a hardcoded laptop path on every gunicorn
  boot in production.
- **Every Django app imports every other Django app.** There is no dependency
  direction at all; three real cycles are held open by function-local imports.
- `apps/accounts/views.py` is three files in one, and `apps/core/views.py` is the
  module `apps/ml` reaches into for shared response helpers.
- The same coverage computation is hand-synchronised across three call sites in
  two apps — and one of the three diverges.
- The frontend has one home for number formatting that ~40 call sites bypass.
- A cloner lands at a repo root with no README and twelve stale audit files.

The success criterion is "a new engineer finds any given piece of logic in under
60 seconds", not lines removed. Batches below are ordered lowest-risk first.

---

## 1. Stack

Django 5.2.17 + DRF 3.18 + PostgreSQL 16 (`psycopg` 3, `dj-database-url`),
SimpleJWT, django-filter, django-cors-headers, gunicorn. Redis is a **cache
only** — no Celery and no broker; the scheduler is `deploy/worker.sh`, a shell
loop plus `flock`. Python ≥3.11, one `pyproject.toml`, `uv.lock` committed. The
learned layer is an optional `ml` extra (scikit-learn, LightGBM, numpy, joblib);
`apps/ml` degrades to a refusal rather than an ImportError when it is absent.

Frontend: React 19, Vite 8, TypeScript 7, Tailwind 4, TanStack Query 5,
react-router 7, radix-ui, ECharts. `package-lock.json` committed and current.

Tests: pytest-django (12 files, ~9,900 lines — 35% of the Python here) and vitest
(2 files, pure logic, no DOM). Lint: ruff (`E,F,I,UP,B`, line-length 100, ignore
`UP017`). **No formatter and no Python type checker are configured.** Both CI and
the local venv were verified working during recon.

## 2. Inventory

| Area | Files | Lines |
|---|---|---|
| `apps/core` | 13 | ~4,485 |
| `apps/jobs` | 9 | ~4,470 |
| `apps/ml` | 9 | ~2,925 |
| `apps/accounts` | 5 | ~1,047 |
| `config` | 3 | ~411 |
| `tests` | 12 | ~9,900 |
| `ui/web/src` + `scripts` | 40 | ~10,900 |
| migrations | 47 | — |
| docs (19 files) | 19 | ~2,400 |

Five files exceed 1,100 lines: `jobs/jobs.py` (1,626), `core/research.py`
(1,616), `ml/train.py` (1,314), `core/views.py` (1,303), `core/pricing.py`
(1,183). `tests/test_api.py` (2,322) is larger than any source file.

Oldest untouched files are the `__init__.py` stubs and the initial migrations
(2026-07-17 to 2026-07-20). Nothing else is stale: every non-migration source
file has been touched since 2026-08-10. **Age produced no deletion candidate.**

Committed junk: none. No `*.bak`/`*.old`/`*_v2`/`temp_*`, no `__pycache__`, no
`dist/`, no `.DS_Store`, no editor directories, no `.env`, no secrets, no
certificates. `.gitignore` is correct and needs no change.

Dependency graph, app to app, module-level imports only:

```
accounts  → core, jobs
core      → jobs, ml
jobs      → core, ml
ml        → core
config    → jobs
```

Every arrow that could exist does. Three genuine cycles are broken by
function-local imports — `core.pricing ↔ core.research`, `jobs.verify ↔
jobs.ingest`, and `core.quality ↔ jobs.verify` (the last via a 6-line
`core/rules.py` extracted purely to break it, then re-exported from two places
with `# noqa: F401 - public compatibility export`).

Config sprawl: **five** env vars are read outside `config/settings.py`, all at
module-import time, so `override_settings` cannot reach them —
`BAMA_BLOCK_COOLDOWN`, `BAMA_BLOCK_COOLDOWN_MAX`, `BAMA_UPSTREAM_COOLDOWN`,
`BAMA_UPSTREAM_COOLDOWN_MAX` (`jobs/fetcher.py`) and `BAMA_SOLD_PROBE_ADS`
(`jobs/jobs.py`). `.env.example` has drifted: it documents a value the code
deliberately moved away from, and omits ~14 variables added since.

Dead data: **none proposed.** No table, column, or index is dropped by this plan.

## 3. Current architecture, and where it is violated

A request goes Caddy → frontend nginx → gunicorn → `config/urls.py` → per-app
`urls.py` → DRF view → `apps/core` analytics (`pricing`, `research`, `quality`) →
ORM. Separately `deploy/worker.sh` loops `manage.py bama <cadence>` →
`jobs/pipeline.py` → `jobs/jobs.py` → `fetcher`/`ingest` → ORM.

1. **No dependency direction** — see the graph above.
2. **Views hold domain logic.** `core/views.py` computes medians over a full
   `values_list().iterator()` scan (`_market_summary`, :437), holds crawl-state
   logic (`_coverage`, :92) and builds SQL `Case` expressions (`_freshness_band`,
   :660). `jobs/views.py::system_health` (:371) runs raw `pg_database_size` and
   `pg_stat_activity` SQL inline.
3. **A view module is imported as a library.** `ml/views.py:37` imports
   `cached, envelope` from `apps.core.views`; `accounts/views.py:463`
   function-locally imports `movement_payload` from the same module.
4. **`accounts/views.py` (670 lines)** holds auth views, 8 serializer classes and
   the whole watchlist-digest domain layer. There is no `accounts/serializers.py`
   while `apps/core` has one.
5. **Frontend** — `src/ui.tsx` is a 919-line grab-bag of four unrelated concerns;
   `Chart.tsx` and `FilterPanel.tsx` are components at `src/` root while ten
   others live in `src/components/`; `pages/AuthLayout.tsx` exports no route.

## 4. Target architecture

The boring, conventional Django layout, plus one leaf package so dependencies
finally flow one way.

```
config/          settings, root urls, wsgi
apps/
  common/        NEW — the leaf. No app imports, no models, no migrations:
                 parsing.py normalization.py rules.py verify.py quality.py
  core/          models, serializers, views, api.py, filters, admin
                 + analytics: pricing.py research.py notify.py images.py
                 + coverage.py (NEW — crawl-state arithmetic core already owns)
  jobs/          fetcher.py ingest.py jobs.py health.py pipeline.py views.py
  ml/            features metrics train registry inference monitoring views
  accounts/      models.py serializers.py views.py digest.py
ui/web/src/
  api.ts auth.tsx theme.tsx filters.ts alerts.ts   app-level singletons
  format.ts      NEW — the pure formatters lifted out of ui.tsx
  components/    every component, including Chart, FilterPanel, AuthLayout
  pages/         routed screens only
```

Resulting direction: `common ← core ← {jobs, ml, accounts} ← config`.

The convention being followed is stock Django app layout — `models.py`,
`serializers.py`, `views.py`, `urls.py`, `admin.py` per app, with domain modules
beside them and no `services/` package. That is what `AGENTS.md` already
describes and what the framework's own documentation assumes; the plan brings the
two apps that drifted back onto it rather than inventing anything.

`apps/common/` rather than a top-level package because `pyproject.toml`'s
`packages.find` includes only `apps*` and `config*` — a sibling would need a
build-config change on top of a move. It holds no models, so it never enters
`INSTALLED_APPS` and generates no migration.

**One exception survives and is documented, not papered over:** `core` reads
`ml.models.AdPrediction` to put predictions on the deal board. It is one edge
against many in the other direction, and inverting it would require an event bus
— an abstraction this codebase does not need.

## 5. Deployment reality

- **CI** (`.github/workflows/ci.yml`): `backend` (pytest with `[test,ml]` plus
  `makemigrations --check --dry-run`), `backend-hardened` (same suite with
  `API_PUBLIC_READS=""`, plus `manage.py check --deploy --fail-level WARNING`),
  `frontend` (typecheck, vitest, contrast check, build, Lighthouse CI), `lint`
  (ruff). `deploy.yml` reuses `ci.yml` via `workflow_call` and runs only if all
  four pass.
- **Deploy**: push to `main` → CI green → SSH under a forced command
  (`sudo /opt/apps/deploy_bama.sh`) which resets the VPS checkout to `origin/main`
  and rebuilds → smoke test polls `/api/health/` and `/api/db/health/` for up to
  90s each.
- **Migrations apply automatically on deploy** (the `django` service runs
  `migrate --noinput` before gunicorn). No batch in this plan creates one.
- **There is no staging and no canary.** The health gate proves the process
  boots, not that a screen renders. This materially raises the risk of every
  batch and is why the size ceiling is enforced rather than nominal.
- **Rollback**: `git revert <sha> && git push`. A manual `git reset` on the VPS is
  undone by the next push (`deploy/CICD.md:99`).

**String-reachable entrypoints** — live code invisible to static analysis, none
of which this plan deletes: `manage.py bama <cadence|job>` and
`manage.py wipe_users`; `pipeline.JOBS` (19 names → callables, the *only* inbound
reference for most of `jobs.py`); `STEP_ORDER` / `CADENCES` / `DEPENDS_ON` /
`ADVISORY`; `jobs.CHECKS`; `views.BANDS`; `registry.FITTED_SPEC_KEYS`;
`views.FETCH_BOUNDS`; every dotted path in `INSTALLED_APPS`, `MIDDLEWARE`,
`REST_FRAMEWORK` and `LOGGING`; DRF router basenames; `addopts = "-p testenv"`;
`@admin.register` classes; and `lazy(() => import("../Chart"))` in the SPA.

## 6. Verification gate

Confirmed available during recon: PostgreSQL 16 listening on 5432, and the
project venv holding pytest, pytest-django, ruff and the full `ml` extra. Docker
is **not** running on this machine, so "the app boots" is verified with
`runserver` and a real request, not with compose.

```bash
# backend gate — every batch
export DATABASE_URL='postgresql://postgres@127.0.0.1:5432/bama_refactor_check'
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/python manage.py makemigrations --check --dry-run
DJANGO_DEBUG=1 .venv/bin/python manage.py runserver 8009 &
curl -fsS localhost:8009/api/health/ && curl -fsS localhost:8009/api/db/health/

# frontend gate — any batch touching ui/web
npm ci && npm run typecheck && npm test && npm run check:contrast && npm run build
```

Plus, for every batch: a repo-wide grep (source, tests, configs, CI, Dockerfiles,
docs, shell, string literals) for every identifier and filename removed or
renamed, expecting zero hits; and a line-by-line self-review of the diff.

## 7. Batch list

Ordered lowest-risk first. One batch = one category of change = one revertible
commit. Ceiling ~400 changed lines; anything larger is split.

### Phase 1 — Deletion

**B1 · Remove the committed agent-debug instrumentation** (~45 lines)
`config/wsgi.py:11-31` writes JSON to a hardcoded path under a developer's home
directory, inside `try/except: pass`, **on every gunicorn boot in production** —
where the path does not exist, so the write fails silently. Landed in `87926eb`,
described there as the "temporary wsgi debug block". Two matching
`# #region agent log` blocks at `tests/test_logical_fixes.py:237` and `:255` go
with it; that test's assertions about the compose gunicorn command are real and
stay.
*Breaks if wrong:* nothing — the block's only current effect is a swallowed
exception.

**B2 · Delete the two dead functions** (~60 lines)
`core/pricing.py:475 cohort_peers` and `ml/train.py:192 _peer_median_baseline`.
Evidence for both: repo-wide grep across `.py`, `.ts`, `.tsx`, `.md`, config, CI,
Dockerfiles and shell returns only the definition plus prose mentions
(`pricing.py:448`, `:463`, `AGENTS.md:293`, `train.py:158`) — zero call sites, not
in `JOBS`/`CHECKS`/`BANDS` or any dispatch table, not a URL target, not a
management command, not reachable by dotted path or reflection. `laddered_peers`
superseded the first; the second is a refactor remnant. The prose mentions are
corrected in the same commit so they do not become dangling references.
*Breaks if wrong:* the fair-price endpoint 500s, or a training fit fails — both
caught by `tests/test_pricing.py` and the `-m slow` fits before push.

**B3 · Delete `work/`** (12 files, ~260 lines)
Point-in-time audit evidence dated 2026-09-12 whose one code finding shipped in
`0c4f88a`. Git keeps the archive; open items carry into `REFACTOR_REPORT.md`.

### Phase 2 — Consolidation

**B4 · Move the five stray env reads into settings** (~15 lines)
Same names, same defaults, same types, read via `settings.*`. Makes every tunable
visible in one file and reachable by `override_settings`.

**B5 · Fix `.env.example` drift** (documentation only)
Correct `BAMA_WORKER_FETCH_ADS` to the value the code actually defaults to, and
add the ~14 variables missing since it was last updated. Placeholders only.

**B6 · One `Ad.bama_url` property** (~20 lines, 6 call sites)
`absolute_ad_url(ad.url or ad.canonical_path)` is spelled out at
`core/serializers.py:87`, `core/views.py:607`, `core/notify.py:223`, `:259`,
`accounts/views.py:624`, `jobs/jobs.py:669` — three of them `get_bama_url`
methods differing only in `obj` vs `obj.ad`. A read-only model property: no
field, no migration, pure derivation. Response bodies stay byte-identical.

**B7 · One `coverage_state()`** (~50 lines, 3 call sites)
`known_feed_depth()` → `find_gaps(...)` → `uncovered_ranks()` → compare to
`COVERAGE_GAP_TOLERANCE_RANKS` is written three times (`core/views.py:99`,
`jobs/jobs.py:1104`, `jobs/views.py:378`) and the third **omits the tolerance
comparison**. The three are diffed line by line first and each caller's exact
output shape is preserved; the divergence is reported as a bug, not silently
unified. The helper lands in `apps/core/coverage.py`, which also sets up B11.

**B8 · Frontend: one home for formatting** (two sub-batches, ~150 lines each)
*B8a* — `.toLocaleString("en-US")` is inlined at ~40 sites across 9 files while
`ui.tsx:155` exports `fa()` for exactly this, and `pages/Control.tsx:99`
re-implements it locally. *B8b* — Persian dates exist in five incompatible forms
across seven files; `Chart.tsx:73 faDate` is the natural home and has zero
external importers today. **These do not all render identically** —
`Control.tsx:109` is timezone-explicit. Outputs are diffed before collapsing and
any genuinely different variant is left alone and reported.

**B9 · Frontend: shared query keys and types** (~120 lines)
`["brands"]` is declared three times with identical queryFn and staleTime,
`["variants", model]` twice, `["fair-price", code]` twice. `interface DealBoard`
is declared three times — verified to be three *narrowings* of one server
payload, not three contracts, so collapsing is type-level only and erased at
runtime. `Brand` and `Variant` are declared twice each.

### Phase 3 — Architecture

**B10 · Create `apps/common/`** (pure move; ~40 files touched, imports only)
Move `jobs/parsing.py`, `jobs/verify.py`, `core/normalization.py`,
`core/rules.py`, `core/quality.py` into it. All five verified as leaves: their
only imports are stdlib, `re`, `django.db.models.Q`, `jdatetime`, and each other.
**No models, therefore no `INSTALLED_APPS` entry and no migration.** `git mv`
plus an import rewrite — nothing else in the diff. The two `# noqa: F401`
re-exports of `HARD_RULE_IDS` collapse into one home.
*Widest diff in the plan; split into one commit per module if it exceeds 400
lines.*

**B11 · Break `core → jobs` entirely** (~80 lines)
After B7 the only edges left are `core/images.py:32` (`HEADERS`,
`consecutive_blocks`) and the `fetcher` import block at `core/views.py:63`.
`consecutive_blocks` reads `FetchRun`, a core model → `core/coverage.py`.
`HEADERS` is a bama.ir site constant → joins `SITE_ROOT`/`CDN_HOSTS` in
`common/parsing.py`.

**B12 · Extract `apps/core/api.py`** (~200 lines, pure move)
`envelope`, `cache_key`, `cached`, `cached_answer` out of `core/views.py`. This
is what `ml/views.py:37` already imports from the view module — the tell that
they were never view code. Not a new abstraction; the existing one, relocated.

**B13 · Split `apps/accounts/views.py`** (670 → three files, pure move)
→ `accounts/serializers.py` (8 classes) and `accounts/digest.py` (5 functions),
matching what `apps/core` already does. The function-local import of
`movement_payload` moves with the digest code and stops being a views→views edge.
`accounts/urls.py:31` reads `views.LoginView.throttle_classes`; that reference
must survive. **Auth serializers are relocated verbatim — nothing inside them is
edited.**

**B14 · Split `apps/jobs/jobs.py`** (1,626 → ~1,050 + ~570, pure move)
The 11 `check_*` functions, the `Check` dataclass, `CHECKS` and `health()` move
to `apps/jobs/health.py`. `pipeline.JOBS["health"]` must keep resolving to the
same callable — asserted already by
`tests/test_jobs.py::test_every_routed_option_is_one_its_job_accepts`.

**B15 · Frontend: split `ui.tsx`, place components** (two sub-batches)
*B15a* — lift the seven pure formatters into `src/format.ts` (10–13 importers
each), and stop exporting the six symbols nothing outside their own file uses.
*B15b* — `git mv` `Chart.tsx` and `FilterPanel.tsx` into `components/`, and
`pages/AuthLayout.tsx` into `components/` (it exports no route). The
`lazy(() => import(...))` specifiers are string references: a broken one passes
`tsc` and fails in the browser, so this batch is verified by loading `/`,
`/analyse`, `/deals`, `/explore` and `/login` against a real production build.

### Phase 4 — Ergonomics

**B16 · Root `README.md`** — there is none today; a cloner sees `.github/`,
`work/` and `bama-saas/`. Short: what this is, the one command to run it, the one
for tests, and a pointer into `bama-saas/README.md`, which is accurate and stays.

**B17 · `AGENTS.md` → `ARCHITECTURE.md`** — it already *is* one. Add the
dependency-direction rule and the four invariants that currently exist only as
prose comments: `ml` before `core` in `config/urls.py`, `ml_train` before
`ml_score` in `STEP_ORDER`, `NUM_PROXIES` matching the deployed proxy chain, and
`setdefault` in `testenv.py`. Retire `docs/production-readiness-checklist.md`
into `REFACTOR_REPORT.md`. `deploy/CICD.md`, `deploy/RESTORE.md` and
`LIGHTHOUSE.md` are accurate and operational — untouched.

## 8. Deletion candidates

| Path / symbol | Evidence of deadness | Confidence | If wrong |
|---|---|---|---|
| `config/wsgi.py:11-31` | Committed as "temporary" in `87926eb`; writes to a path absent in every container; `except: pass` | High | Nothing; already a no-op in prod |
| `tests/test_logical_fixes.py` two log regions | Same session id; assertions preserved | High | A test loses two file-writes |
| `core/pricing.py:475 cohort_peers` | Repo-wide grep: definition + 3 prose mentions, zero call sites, no dispatch/URL/command/dotted-path reference; superseded by `laddered_peers` | High | Fair-price 500s; caught by `test_pricing.py` |
| `ml/train.py:192 _peer_median_baseline` | Repo-wide grep: definition + 1 prose mention; refactor remnant | Medium-high | A fit fails; caught by `-m slow` |
| `work/` (12 files) | Dated audit evidence, zero code references, finding shipped in `0c4f88a` | High | Loses a git-recoverable report |

### Suspected dead — needs runtime confirmation

**Empty.** An AST sweep over every top-level `def`/`class` under `apps/` and
`config/`, cross-referenced against app source, tests and `ui/`, found no unused
view, serializer, job, check, model method, component, or dependency beyond the
two above. Three things *look* dead to static analysis and are load-bearing —
listed here so they are not re-derived as candidates later:

- `testenv.py` — a pytest plugin loaded by name via `addopts = "-p testenv"`. It
  cannot be a `conftest.py`: pytest-django imports `config.settings` inside
  `pytest_load_initial_conftests`, and plugin hooks run before conftest
  collection.
- `apps/core/rules.py` (6 lines) — exists solely to break the `quality ↔ verify`
  cycle. Deleting it standalone reintroduces the cycle.
- The seven `@admin.register` ModelAdmin classes in `core/admin.py` — zero
  inbound references, registered by decorator.

## 9. Bugs found — reported, not fixed

Per the zero-behaviour-change constraint, none of these are fixed in this pass.

1. **`jobs/views.py:378-380`** computes coverage without the
   `COVERAGE_GAP_TOLERANCE_RANKS` comparison its two siblings apply, so
   `system_health` can disagree with the deal board's own coverage badge about
   whether a sweep is complete.
2. **`.env.example`** documents `BAMA_WORKER_FETCH_ADS=500`, the value
   `config/settings.py` was deliberately raised away from after measuring it
   pre-empting the saturation rule 62% of the time. Copying the example file
   reinstates the regression. B5 fixes the *document*, not the code.
3. **`ui/web/src/pages/Budget.tsx:92`** strips input with
   `replace(/[^\d]/g, "")`, which silently **drops** Persian digits instead of
   converting them — the exact failure `latinDigits` exists to prevent. Typing
   `۱۲۳` yields an empty budget.
4. **`ui/web/scripts/vite-proxy.test.ts` is not type-checked** —
   `tsconfig.json`'s `include` is `["src", "vite.config.ts"]`.
5. **No `vite-env.d.ts`**, so a typo in `import.meta.env.VITE_*` compiles clean
   and silently yields `undefined`.

## 10. Risks, and what is deliberately untouched

- **No staging, no canary.** The only gate is a health probe that proves the
  process boots. B8 and B15 change user-visible rendering and get a real browser
  check against a production build before push.
- **Migrations apply automatically on deploy.** No batch here generates one;
  `makemigrations --check --dry-run` is in the gate for every batch and CI
  enforces it independently. **No table, column, or index is dropped.**
- **Open question — `apps/common/` naming.** If `apps/shared/` or `apps/domain/`
  is preferred, say so before B10; renaming afterwards is another wide diff.
- **Untouched per the never-touch list:** all 47 migrations; everything under
  auth, session, JWT, password hashing, throttling, CORS and CSRF (B13 relocates
  auth serializers verbatim and edits nothing inside them); both workflow files;
  `nginx.conf`; `security-headers.conf`; the deploy shell scripts; both
  lockfiles; `.gitignore`.
- **Deliberately left alone because they are correct:** `config/settings.py` (one
  file, hardened by default, every branch explained); `jobs/pipeline.py`;
  `testenv.py`; `core/research.py` and `core/pricing.py` (large but cohesive —
  rewriting them would be taste, not simplification); the `styles.css` semantic
  class layer; all 11 shadcn primitives, every one of which is used.
- **A note for whoever executes this:** nearly every non-obvious construct here
  carries a comment naming the production incident that produced it, often with a
  date and measured numbers. Several load-bearing invariants are documented
  *only* in prose. Treat the comments as specification, not as noise.
