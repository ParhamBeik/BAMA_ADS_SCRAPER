# Reviewed change inventory — 2026-09-12

Baseline before the recovered audit/remediation work: `b91017905d18aad150968168b593e303a80c2a40`.

| Change | State | Independent review result |
| --- | --- | --- |
| `8c851562b5752ae8f3a0ba27921834f161334334` — RTL primitives, numeric input, scope labels, stale form errors, listing presentation | Committed, pushed, deployed | Reviewed all 14 frontend files. The shared RTL provider is the correct root-level repair; current mobile browser checks confirm the tab behavior. No new correctness or authorization defect found. |
| `3c89d624541e4280ddacf1c0388c8af19e5ef998` — production throttle sizing | Committed, pushed, deployed | Reviewed both Compose/example changes and the existing scoped-throttle call sites. Defaults are nonempty and version-controlled; terminal hardened/PostgreSQL CI and deployment passed. No security regression found. |
| Listing-image alternative text | Uncommitted local change | Correctly changes informative gallery images from empty text to title plus ordinal. It is typechecked, built, and covered by a failing live reproduction, but not committed, pushed, deployed, or live-verified. |
| `work/e2e-audit/` | Untracked prior-task evidence | Reviewed as evidence, not treated as proof. It remains preserved and unmodified. |

The aggregate committed diff from `b910179` to `3c89d62` passed `git diff --check`; local `main`, `origin/main`, and the VPS checkout agree at `3c89d62`. The current local tree remains deliberately dirty only for the accessibility correction and review artifacts.
