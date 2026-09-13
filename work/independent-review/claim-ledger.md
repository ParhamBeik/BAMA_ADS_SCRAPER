# Independent claim ledger — 2026-09-12

Recovered task: `Audit app UX and performance` (`01a0926d-3037-71a0-b2b7-0224abdb450c`). Its scope was a read-only browser/VPS audit with a local listing-image accessibility correction. The later commits `8c85156` and `3c89d62` are reviewed as follow-on remediation because they are deployed on the same branch.

| Previous claim | Independent method | Result | Required follow-up |
| --- | --- | --- | --- |
| Local, remote, and VPS baseline is `3c89d624` | Local refs; `git ls-remote`; read-only VPS `git rev-parse` | VERIFIED | None. |
| Public liveness and readiness are healthy | External HTTPS probes and VPS loopback probes | VERIFIED | Continue routine monitoring. |
| Containers are healthy and migrations are current | Read-only `docker ps` and `showmigrations --plan` | VERIFIED | Worker/ML have no Docker healthcheck; rely on job evidence as well. |
| The current worker is operating successfully | Read-only worker log sample | VERIFIED | Latest pipeline completed `ok=True` in 84.5 s. |
| Five failed runs in the prior 24-hour window were accounted for by upstream 503s and an orphan | Current ORM aggregate and logs | PARTIAL | The time-bound count is now four, all `coverage` rows with empty detail; no HTTP 503 appeared in the last 24 hours. Diagnose why coverage failures record no detail. |
| ML-version warnings are present but not a demonstrated scoring failure | Read-only ML logs and dependency inspection | VERIFIED | Artifacts are 1.9.0 and runtime is 1.9.1; retrain/validate artifacts under the locked runtime before treating the warning as harmless. |
| Listing photographs are incorrectly decorative to assistive technology | Fresh authenticated browser DOM inspection of `/listing/emt1opqj` | VERIFIED | The deployed page has six images and all six have `alt=""`. |
| The local image-alt correction addresses the defect | Direct source review plus TypeScript, test, contrast, and production-build checks | VERIFIED LOCALLY | Publish only with separate authorization, then rerun the live browser check. |
| The deployed RTL tab remediation removes the mobile regression | Fresh 390px browser route, overflow, keyboard, and console checks | VERIFIED | No page overflow or console issues; ArrowRight from the second tab selected the visually right tab. |
| The prior audit covered every reachable surface and control | Recovered matrix/artifacts and risk-based fresh samples | PARTIAL | The artifacts enumerate 15 surfaces, but this review did not re-execute all 15. It independently sampled changed, critical, and previously failing flows. |
| Synthetic-account and destructive workflows were tested | Browser/session and artifact review | BLOCKED | The current instruction requires confirmation immediately before account creation; no disposable account was created and no real account was altered. |
| Prior latency values establish current performance | Recovered latency notes | NOT TESTED | They are point-in-time observations, not independently remeasured in this review. |
| CI and deployment for `3c89d624` completed | GitHub Actions run/job inspection and VPS revision check | VERIFIED | CI `34712639679` and deployment `34712639916` are terminal successes for the deployed SHA. |
