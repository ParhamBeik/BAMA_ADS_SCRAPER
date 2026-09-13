# Evidence ledger

Status vocabulary: `PASS`, `FAIL`, `BLOCKED`, `N/A`, and `NOT YET TESTED`.

| ID | Area | Status | Evidence | Notes |
| --- | --- | --- | --- | --- |
| B-001 | Local repository baseline | PASS | `git status --short --branch`; `git rev-parse HEAD`; `git diff --check` | Audit began on clean `main` at `3c89d624`; current tracked diff is the documented local accessibility fix and `work/e2e-audit/` is untracked. |
| B-002 | Deployed version match | PASS | Read-only SSH `git rev-parse HEAD` and `origin/main` | VPS checkout matches local/origin at `3c89d624`. |
| B-003 | Public health | PASS | VPS `curl` probes to `/api/health/` and `/api/db/health/` | Both returned HTTP 200 through Caddy. |
| B-004 | Live container health | PASS | Read-only `docker ps`/`docker inspect` | BAMA Postgres, Redis, Django, and frontend healthy; worker/ML running without healthchecks. |
| B-005 | Live migration state | PASS | Read-only `showmigrations --plan` piped through a pending-migration check | No pending migrations reported. |
| B-006 | Worker/ML runtime | PASS with warning | Read-only worker/ML logs | Latest worker pipeline and coverage cycle succeeded; ML emitted scikit-learn artifact-version warnings, tracked as F-003. |
| B-007 | Browser session | PASS | Existing tab AX snapshot | Existing tab at `/`, authenticated, staff menu visible. |
| B-008 | Browser console baseline | PASS | `tab.dev.logs({levels:["error","warn"]})` | No warnings/errors observed across tested routes and interactions. |
| B-009 | Staff control read-only page | PASS with finding | AX snapshot of `/control` | Page rendered operational metrics; five failed runs in 24h are recorded as F-001. |
| B-010 | Anonymous/API boundary | PARTIAL/BLOCKED | Unauthenticated stdlib HTTPS probes; authenticated browser redirects | Analytics APIs returned 403 without credentials and `/api/auth/me/` returned unauthenticated. Anonymous form rendering cannot be isolated without logging out the existing admin. |
| B-011 | Django admin read-only | PASS | Authenticated `/admin/` AX snapshot | Admin index rendered; no change/add form was submitted. |
| B-012 | Unknown listing | PASS | `/listing/not-real-20260912` after 2.2s | Rendered the expected not-found state with no console errors. |
| B-013 | Legacy routes/navigation | PASS | `/market`, `/research`, `/research/115`, back/forward | Legacy routes redirected to `/analyse` with the expected model query; browser back/forward preserved route state. |
| B-014 | Synthetic account creation | BLOCKED pending confirmation | Objective requires action-time confirmation immediately before final account creation | No account creation attempted. |
| B-015 | Destructive/mutating controls | BLOCKED/DEFERRED | Read-only forms and control inspection | No real-admin password/logout-all/notifier changes, dangerous jobs, saved-item deletion, alert creation, or account deletion were executed. |

Findings and route-level coverage are maintained in `findings.md` and `feature-matrix.md`.
