# Independent review: BAMA end-to-end audit remediation

Audit date: 2026-09-19 (Asia/Tehran)

Current verdict: **NOT VERIFIED**. The recovered remediation is locally sound, but the exact
deployed revision, current containers, logs, rendered UI, and production persistence cannot be
verified while the shared VPS is unreachable.

## Recovered work

- Original browser task: `Audit app UX and performance`
  (`01a0926d-3037-71a0-b2b7-0224abdb450c`).
- Best-supported pre-remediation baseline: `b91017905d18aad150968168b593e303a80c2a40`.
- Attributable commits:
  - `8c851562b5752ae8f3a0ba27921834f161334334`: RTL primitives, responsive tabs,
    accessible labels, Persian numeric input, stale form-error cleanup, and related UX fixes.
  - `3c89d624541e4280ddacf1c0388c8af19e5ef998`: production auth throttle defaults.
  - `0c4f88a40841a79236ca38342f0eb8d51d435db1`: listing-gallery alternative text and
    point-in-time audit artifacts.
- The original task explicitly left disposable-account CRUD and destructive cleanup untested.
  A later task nevertheless called the goal complete; this review rejects that broad claim.

## Repository and deployment state

| State | Evidence | Result |
| --- | --- | --- |
| Working tree before this review | `git status --porcelain=v2` | Clean |
| Local branch | `main` at `c0e37bf8a3cc3a6f3950eca8af25e165cdf1a810` | Three commits ahead of remote |
| Remote branch | `origin/main` at `99790085092e68de7534d3aea60c7591f464201b` | Verified with local refs and GitHub |
| Other branches/worktrees | None; one worktree | Verified |
| Latest remote CI | Run `34971861903`, terminal success for `9979008` | Verified historically |
| Latest deploy workflow | Run `34971862165`, terminal success for `9979008` | Verified historically |
| Deployed revision now | SSH unavailable before key exchange | BLOCKED |
| Current production health | TLS handshakes time out across all known domains on the VPS | FAIL/BLOCKED |

The three local-only commits add the root README, rename architecture documentation, remove the
superseded production checklist, and add the final refactor report. They are not pushed, not known
to be deployed, and do not alter runtime behavior except for a comment-only edit.

## Reviewed change inventory

| Change | Review result |
| --- | --- |
| Global Radix RTL provider | Correct root-cause fix for primitive direction and keyboard order; retained at current HEAD |
| Scrollable tab strip | Correctly hides classic scrollbars and scrolls the active trigger into view; browser recheck blocked |
| Login/signup/account stale errors | Errors are retired when input changes; no auth or persistence behavior changed |
| Persian numeric input | Normalizes Persian and Arabic-Indic digits before controlled/uncontrolled consumers read the value |
| Scope/model labels | Accessible names and stale URL values are made explicit; later refactor retained behavior |
| Listing layout/copy | Loading order, gallery grid, units, and unavailable reasons are coherent; current build passes |
| Gallery alternative text | Informative images use listing title plus one-based position; retained at current HEAD |
| Production throttles | Compose resolves to register 30/min, login 60/min, password 30/min, anon 300/min, user 600/min |
| Trusted proxy count | Remains two hops and has request-level tests against spoofed forwarding headers |
| Later structural refactor | Full PostgreSQL suite and frontend gates pass on current HEAD; not attributable to the prior browser task |

All 14 frontend files in `8c85156`, both deployment files in `3c89d62`, and the source change in
`0c4f88a` were reviewed directly, followed through their current call sites, and compared with the
current build and tests. No objective correctness, authorization, data-integrity, or security
defect was found in those changes.

## Findings, ordered by severity

### Critical: shared VPS is currently unreachable

- SSH reaches TCP port 22 but closes or times out before server key exchange.
- HTTPS reaches TCP port 443 but never completes TLS for BAMA, Portfolio, Twitter, or News.
- An external HTTP probe from Austria, Hong Kong, Iran, Sweden, and the US returned `No route to
  host` or timeout from every node.
- An external TCP/22 probe failed from four of five nodes; one Ukrainian node completed TCP only.
- Because multiple applications and protocols fail together, this is evidence of a VPS/network
  incident, not evidence that the reviewed BAMA changes caused the outage.

Impact: current deployed SHA, containers, migrations, logs, persisted data, browser behavior, and
performance cannot be verified. No restart or redeploy was attempted without a working diagnostic
channel or evidence that BAMA caused the host-wide failure.

### High: the prior goal's completion claim was false

The original audit and its completion checklist both state that anonymous browser behavior,
disposable-account creation, user-owned CRUD, persistence, and deletion were blocked. The later
final answer called the whole goal complete anyway. Read-only route coverage is not equivalent to
complete end-to-end coverage.

### Medium: current browser evidence is unavailable

Both supported UI-control paths fail because the local browser bridge file is absent. The current
RTL, accessibility, responsive, console, interaction, and latency claims therefore remain
unverified even before considering the production outage.

### Low: later refactor failed diff integrity

`git diff --check origin/main..HEAD` found one extra blank line at end-of-file. This review removed
it. The edit is formatting-only and needs no behavioral regression test.

## Automated evidence

| Command/check | Current result |
| --- | --- |
| PostgreSQL-backed backend suite | `795 passed in 124.36s` |
| Frontend Vitest | `2 files, 30 tests passed` |
| TypeScript | Pass |
| Contrast contract, light and dark | Pass |
| Production frontend build | Pass; lazy chart chunk remains 557.22 kB / 188.77 kB gzip warning |
| Ruff | Pass |
| Lockfile check | Pass |
| Missing migrations | None |
| Django hardened deployment checklist | Pass with warnings promoted to failures |
| Production Compose expansion | Expected proxy and throttle values present |

The backend suite uses PostgreSQL 16 on host port 5433. The focused auth and API paths are included
in the 795-test result. CI independently ran the permissive and hardened backend profiles, frontend
gates, Lighthouse, and lint for remote revision `9979008`.

## Browser and performance evidence

- Historical evidence from 2026-09-12 measured warm route readiness around 0.76-1.13 seconds and
  interaction updates around 0.38-0.62 seconds. These are historical observations, not current
  performance proof.
- Historical browser evidence reproduced the RTL tab defect and empty gallery alternative text,
  then verified the deployed corrections at `0c4f88a`.
- No current browser workflow, console inspection, viewport check, accessibility-tree inspection,
  or latency measurement succeeded on 2026-09-19.

## Coverage matrix

| Requirement | Current status |
| --- | --- |
| Correct prior task recovered | VERIFIED |
| Objective and constraints recovered | VERIFIED |
| Attributable commits and source changes reviewed | VERIFIED |
| Current local automated gates | VERIFIED |
| Remote CI and deploy terminal state | VERIFIED for 2026-09-15 revision |
| Current deployed revision and containers | BLOCKED |
| Current live health and logs | FAIL/BLOCKED |
| Changed RTL/accessibility browser workflows | BLOCKED |
| Anonymous and disposable-account CRUD lifecycle | NOT TESTED/BLOCKED |
| Current performance comparison | NOT TESTED/BLOCKED |
| Critical security/data-integrity regression in reviewed code | None found locally |
| Completion criteria | NOT MET |

## Questions and decisions

- No product, compatibility, API, security-policy, retention, cost, or architecture choice is
  currently required for the local remediation.
- When production and browser access return, the task requires action-time approval immediately
  before creating and later deleting an isolated production test account. Approval has not been
  requested early because the action is not yet reachable.
- Operational recovery of the VPS may require the hosting control plane. A blind reboot or deploy
  was not chosen because the host-wide failure has no application-level diagnosis.

## Remaining limitations and next evidence

1. Restore an authoritative VPS access path or recover the host through its provider control plane.
2. Verify the deployed SHA, service state, migrations, disk/memory, edge proxy, and recent logs.
3. Restore Browser or Computer Use connectivity.
4. Re-run changed, critical, and previously blocked browser workflows.
5. Request action-time approval, then create an isolated account, exercise user-owned CRUD and
   persistence, clean up the account, and verify deletion.
6. Recheck production latency and update the claim ledger before declaring completion.
