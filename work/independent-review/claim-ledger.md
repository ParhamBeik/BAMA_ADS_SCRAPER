# Previous-task claim ledger

Recovered task: `Audit app UX and performance` (`01a0926d-3037-71a0-b2b7-0224abdb450c`).

Status vocabulary: `VERIFIED`, `REFUTED`, `PARTIAL`, `BLOCKED`, `NOT TESTED`.

| Previous claim | Original evidence | Independent method | Current result | Required follow-up |
| --- | --- | --- | --- | --- |
| Public login, signup, and methodology pages were responsive without console errors | 2026-09-12 browser session | Recovered session and artifacts; attempted fresh browser connection | PARTIAL | Current browser bridge and production access must be restored |
| Authenticated market, budget, deals, explorer, analysis, saved, alerts, listing, methodology, account, control, and admin surfaces rendered | Existing staff browser session | Recovered 15-surface matrix and source routes; current browser unavailable | PARTIAL | Re-run changed and critical routes in a fresh session |
| Every reachable surface/control was covered | Point-in-time 15-row matrix | Compared matrix with explicit mutation blockers | REFUTED | Read-only enumeration did not cover account lifecycle or user-owned mutations |
| Mobile deal tabs had a vertical scrollbar and reversed RTL ArrowRight behavior | Browser reproduction | Reviewed pre-fix source and root-level Radix remediation | VERIFIED historically | Fresh rendered recheck is blocked |
| The RTL/tab remediation fixed the root cause | Deployed browser check at `3c89d62` | Direct diff/current-call-path review plus build | VERIFIED locally / PARTIAL live | Re-run keyboard and 390 px checks |
| Server auth errors remained visible after input edits | Browser/form observation | Direct pre/post diff and current source review | VERIFIED | Optional component test only if a DOM test stack is later introduced |
| Production auth limits were too small for shared NAT use | Product observation and config review | Compose expansion and scoped-throttle call-path review | VERIFIED as configured | Product sizing remains a policy choice, not a correctness proof |
| Trusted forwarding prevented rotating-header throttle bypass | Request-level backend test | Full PostgreSQL suite, including spoofed-header test | VERIFIED | Recheck live forwarding diagnostic after VPS recovery |
| Listing images were incorrectly decorative | Six live images with empty `alt` | Pre-fix/current source diff and historical live reproduction | VERIFIED historically | Current live DOM recheck blocked |
| Gallery alternative-text correction was shipped and live | CI/deploy and browser check for `0c4f88a` | Current source plus historical workflow evidence | VERIFIED historically / BLOCKED current | Confirm current deployed SHA and DOM |
| Frontend checks passed | 30 tests, typecheck, contrast, build | Re-ran all four on current local HEAD | VERIFIED |
| Backend was fully verified | Original run had 307 pass and 488 DB connection errors | Fresh PostgreSQL 16 suite | VERIFIED AFTER CORRECTION: 795 passed | None locally |
| Latest remote revision passed CI and deployment | GitHub workflow results | Inspected terminal jobs and steps | VERIFIED for `9979008` | Does not prove current host health |
| Local, remote, and VPS revisions were aligned | Historical SSH and refs | Current local/remote inspection; previous VPS is decommissioned and replacement identity is unavailable | REFUTED current | Local is ahead; replacement deployed SHA is unknown |
| Containers, migrations, worker, and ML were healthy | Historical VPS inspection | Fresh probes targeted the now-retired host | BLOCKED | Obtain the replacement endpoint and inspect it directly |
| No current worker errors, coverage failures, or ML warnings remained | Historical three-hour log sample | Fresh logs unavailable | NOT TESTED | Inspect bounded recent logs after recovery |
| Performance was acceptable | Warm route and interaction samples | Historical figures reviewed; no current browser | NOT TESTED current | Remeasure representative routes and interactions |
| The end-to-end goal was complete | Final message despite explicit blockers | Requirement-by-requirement completion audit | REFUTED | Complete current live, browser, CRUD, persistence, and cleanup checks |
