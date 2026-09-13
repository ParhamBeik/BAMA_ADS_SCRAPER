# Findings

## F-001 — Failed crawler runs visible in staff health

- Severity: HIGH operational / MEDIUM user-facing impact
- Status: OPEN, needs diagnosis and recheck
- Evidence: Live `/control` AX snapshot at 2026-09-12 showed `failed_runs FAIL`, five failed
  runs in 24 hours. The visible details included an orphaned `backfill` run and repeated upstream
  HTTP 503 responses from `https://bama.ir/cad/api/search?pageIndex=707&image=1&priced=1` and
  page 0.
- Impact: Crawl coverage and freshness can be degraded even though the public health endpoints and
  current summary pages are healthy.
- Recheck: Subsequent read-only worker logs showed a successful full pipeline and successful
  coverage cycle, but the staff health panel still reported five failed runs in its 24-hour
  window. A direct read-only ORM query accounted for all five: one orphaned/interrupted backfill
  after a process exit and four fetch errors caused by upstream HTTP 503 responses (three at page
  707 and one at page 0). This reduces the likelihood of a current outage but does not clear the
  recorded failures from the health window.
- Constraint: This audit did not trigger a new fetch or alter the live worker. The failure history
  remains open for operator diagnosis and recheck.

## F-002 — Informative listing images have empty alternative text

- Severity: MEDIUM, accessibility/user-facing
- Status: LOCAL FIX APPLIED; LIVE OPEN
- Reproduction: Open the live `/listing/emt1opqj` route in the staff browser and inspect its six
  rendered `<img>` elements. Each live image had `alt=""`, even though the images are the primary
  listing photographs and are not decorative.
- Expected: Each informative image should expose a concise listing description and position to
  assistive technology.
- Local trace/fix: The local image map in `ui/web/src/pages/ListingDetail.tsx` now supplies
  `${data.title} — تصویر ${index + 1}`. Frontend tests, typecheck, contrast checks, and production
  build pass after the change.
- Deployment boundary: The VPS remains at `3c89d624`; no deployment was performed, so the live
  browser still requires re-verification after a future deploy.

## F-003 — ML artifacts load with a scikit-learn version mismatch warning

- Severity: LOW operational / MEDIUM model-integrity risk
- Status: OPEN, monitor and align runtime artifacts
- Evidence: Read-only `bama-ml`/worker logs emitted `InconsistentVersionWarning` while unpickling
  several estimators saved with scikit-learn 1.9.0 under runtime 1.9.1. The same log window showed
  successful ML training/scoring and an active promoted model, so this is a compatibility warning,
  not a demonstrated scoring failure.
- Impact: Future library changes can make persisted estimator behavior unreliable or fail to load.
- Recommended next action: Pin the ML runtime and artifact-training version to the same resolved
  dependency, retrain or validate persisted artifacts, and make the compatibility check a visible
  deployment gate. No live package or artifact change was made during this audit.
