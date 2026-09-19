# Independent review: BAMA end-to-end audit remediation

Audit date: 2026-09-19 (Asia/Tehran)

Current verdict: **NOT VERIFIED**. The recovered remediation is locally sound, but the exact
deployed revision, current containers, logs, rendered UI, and production persistence cannot be
verified until the replacement VPS has a stable public hostname and its Iran-specific TLS path is
diagnosed.

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
| Deployed revision now | Replacement VPS identity is not present in the repository or audit context | BLOCKED |
| Current production health | Legacy host is retired; replacement DNS and Iran TLS path are unresolved | BLOCKED |

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

### Critical: production cutover identity and network path are unresolved

- The owner confirmed that the previously probed VPS is decommissioned and a replacement VPS is
  in place. The failed SSH and HTTPS probes therefore do not describe the replacement host.
- The legacy BAMA hostname embeds the retired public IP, and deployment documentation, the
  production environment example, the frontend site-URL fallback, and Lighthouse instructions
  still reference it.
- On the current Iranian network, the legacy hostname resolves to private address `10.10.34.36`.
  Queries addressed to Cloudflare, Google, and Quad9 DNS receive the same rewritten answer, while
  DNS-over-HTTPS attempts time out or reset. This is consistent with DNS interception or
  sinkholing, not an authoritative `sslip.io` answer.
- The owner also reports SNI-dependent TLS blocking by the Iranian ISP. DNS resolution and TLS SNI
  are independent failure layers and need separate probes against the replacement endpoint.
- Before that clarification, SSH reached TCP port 22 but closed or timed out before server key
  exchange, and HTTPS reached TCP port 443 without completing TLS on the retired host.
- An external HTTP probe from Austria, Hong Kong, Iran, Sweden, and the US returned `No route to
  host` or timeout from every node against the retired host.
- An external TCP/22 probe against the retired host failed from four of five nodes; one Ukrainian
  node completed TCP only.

Impact: current deployed SHA, containers, migrations, logs, persisted data, browser behavior, and
performance cannot be verified. The replacement public IP and intended hostname are required
before the DNS cutover, SNI behavior, SSH access, or application health can be tested accurately.

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

- The replacement public IP and intended public hostname are required to continue the live audit.
- DNS correctness and SNI-dependent TLS reachability must be tested separately from both an Iranian
  network and an external network; a successful external certificate handshake would not prove
  reachability through the affected ISP.
- When production and browser access return, the task requires action-time approval immediately
  before creating and later deleting an isolated production test account. Approval has not been
  requested early because the action is not yet reachable.
- No replacement value was guessed for the retired IP or hostname because that could redirect
  deployment, CSRF, CORS, canonical metadata, and health checks to the wrong endpoint.

## Remaining limitations and next evidence

1. Record the replacement public IP and intended public hostname.
2. Compare authoritative DNS with Iranian recursive DNS, then probe TCP/443 and TLS with and without
   the intended SNI from Iranian and external networks.
3. Update the DNS record, deployment secrets, allowed origins/hosts, canonical site URL, health
   check, and deployment documentation only after the endpoint is confirmed.
4. Verify the deployed SHA, service state, migrations, disk/memory, edge proxy, and recent logs.
5. Restore Browser or Computer Use connectivity and re-run changed and critical browser workflows.
6. Request action-time approval, then create an isolated account, exercise user-owned CRUD and
   persistence, clean up the account, and verify deletion.
7. Recheck production latency and update the claim ledger before declaring completion.
