# BAMA end-to-end audit

This directory contains evidence for the read-only live audit of the deployed BAMA application.

- Audit date: 2026-09-12 (Asia/Tehran)
- Application: `https://bama-89-106-206-4.sslip.io/`
- Local checkout: `/Users/parham/Downloads/GITHUB_PROJECTS/BAMA_ADS_SCRAPER`
- Deployed checkout: `/opt/apps/BAMA_ADS_SCRAPER`
- Local and deployed commit at audit baseline: `3c89d624541e4280ddacf1c0388c8af19e5ef998`
- Browser: existing Codex in-app browser tab, already authenticated as staff

The audit inspected all 15 source/UI surface rows in `feature-matrix.md`, so surface coverage is
100% by that documented denominator. This does not mean all mutations were executed: account
creation, real-admin changes, notifier changes, destructive deletion, and dangerous job triggers
remain intentionally blocked or deferred by the objective's safety rules.

Local-only change made during the audit:

- `ui/web/src/pages/ListingDetail.tsx` now gives each informative listing image a title and ordinal
  alternative text. The live VPS was not changed; live verification still reflects the deployed
  baseline and remains open for this defect.

The current local worktree therefore contains this source fix plus the untracked audit artifacts.
No commit, push, deploy, migration, service restart, or database mutation was performed.

The audit is intentionally split into source-level discovery, public/authenticated browser evidence,
read-only VPS evidence, and synthetic-account mutation coverage. Secrets, cookies, tokens, and private
credentials must not be stored here.
