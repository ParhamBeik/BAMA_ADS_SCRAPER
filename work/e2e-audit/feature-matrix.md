# Feature and route coverage matrix

This is the source-discovered inventory. Browser statuses are filled as each route is inspected.

| Surface | Source route(s) | UI route | Status | Evidence / limitation |
| --- | --- | --- | --- | --- |
| Public health | `/api/health/`, `/api/db/health/` | N/A | PASS | Live VPS probes returned HTTP 200 with security headers. |
| Authentication | `/api/auth/me/`, `register/`, `email-available/`, `login/`, `logout/`, `logout-everywhere/`, `password/`, token routes | `/login`, `/signup` | PARTIAL/BLOCKED | Unauthenticated API probes returned expected 403/unauthenticated results; authenticated browser redirects `/login` and `/signup` to home. Isolated anonymous form and successful sign-up require a separate session and final account-creation confirmation. |
| Market pulse | analytics overview, market-read, movers, turnover, arrivals | `/` | PASS | Metrics and six tables rendered; 7/30/90/365-day controls, custom date picker open/close, and model/brand/price/year/body tabs exercised. |
| Budget search | `/api/analytics/affordable/` | `/budget` | PASS | Empty-submit validation and 1B toman / 10% result flow produced a coherent result table and URL state. |
| Deal board | deal scores list/detail | `/deals` | PASS | Warm load, all/ML/review tabs, pagination next/previous, notifier panel, and listing links exercised read-only. |
| Catalog explorer | brands/models/variants, ads, fair price | `/explore` | PASS | Search, brand filter, sort, table/card view, price filter, pagination/listings, and no-console-error checks passed. |
| Analysis | market index, distribution, movement, liquidity, depreciation, scoped deals | `/analyse` | PASS | 7/30/90-day controls, model scope, dependent selectors, condition filter, tables/charts, and long-page content exercised. |
| Listing detail | ad detail, price history, fair price, prediction, image proxy | `/listing/:code` | PASS with live finding | Live listing loaded with six proxy images, price/fair-price/ML/deal sections, and source links. Live image alt text is empty; local fix is un-deployed and tracked as F-002. Unknown-code state also passed. |
| Saved cars | favorites | `/saved` | PASS read-only / mutation blocked | Existing saved state rendered; no deletion or save mutation attempted against the real account. |
| Alerts | watchlists, alert rules, alert inbox, mark-read | `/alerts` | PASS read-only / mutation blocked | Empty inbox/rules and rule form/cancel path rendered; creation and notification-affecting mutations were not submitted. |
| Methodology | ML model cards | `/methodology` | PASS | Full long page rendered with active/shadow/retired model cards and 85 tables; no console errors. |
| Account | password change, sign out | `/account` | PASS read-only / mutation blocked | Password/session controls rendered; real-admin password, logout-all, and logout were not used. |
| Staff control | crawl health, job overview, ML health/review queue, job trigger buttons | `/control` | PASS with F-001 | Health/job/backup/coverage panels rendered; dangerous job triggers were identified and deliberately not clicked. |
| Django admin | staff admin views | `/admin/` | PASS read-only | Admin index and model links rendered; no add/change/delete operation was submitted. |
| Legacy redirects | SPA redirect routes | `/market`, `/research`, `/research/:modelId` | PASS | Redirects reached `/analyse` and preserved model scope for `/research/115`. |

Source inventory also includes 59 server `path`/router registrations and the route families listed in
`bama-saas/README.md`; the difference between API endpoints and rendered screens is intentional and is
tracked above rather than treated as missing coverage.
