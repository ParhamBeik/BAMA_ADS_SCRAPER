# Role and workflow coverage

## Roles

| Role/context | Covered | Result | Limitation |
| --- | --- | --- | --- |
| Anonymous API | Yes | Analytics/catalog requests without credentials returned HTTP 403; `/api/auth/me/` returned `authenticated:false`; methodology model metadata remained public. | Browser UI could not be made anonymous without logging out the existing staff session. |
| Existing staff/admin browser session | Yes | All reachable read-only screens and the Django admin index were inspected. Staff-only control and provenance routes were reachable. | Real-admin mutations were intentionally not performed. |
| Isolated demo account | No account created | BLOCKED at the required confirmation gate immediately before final account creation. | No safe pre-existing test account was supplied. |

## Workflow status

- Read-only navigation, refresh/deep links, legacy redirects, back/forward, search, filtering,
  sorting, pagination, empty states, not-found state, responsive layout, and console checks were
  exercised on the live staff session.
- Validation was exercised for an empty budget submission; result persistence was checked through
  URL state and rendered output. No data mutation was submitted.
- Save, alert-rule creation, notifier configuration, password change, logout-all, dangerous worker
  jobs, and deletion were inspected but deferred because the available session is the real admin
  and the objective requires isolated synthetic data plus action-time confirmation for account
  creation/destructive actions.
- Role boundaries were also checked through direct unauthenticated API probes: gated analytics
  endpoints returned 403 rather than empty success payloads.
