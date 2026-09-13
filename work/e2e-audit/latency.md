# Observed latency and responsiveness

Measurements were taken from the existing in-app browser on 2026-09-12. `goto_ms` is navigation
completion timing from the browser helper; `ready_ms` is time until the route's visible marker was
present. These are observations, not an official product budget.

## Warm route samples (milliseconds)

| Route / marker | Ready samples | Goto samples | Result |
| --- | ---: | ---: | --- |
| `/` / market-pulse heading | 813, 944, 780 | 234, 369, 206 | Rendered; no console warnings/errors |
| `/explore` / ads heading | 767, 791, 795 | 188, 207, 216 | Rendered; no console warnings/errors |
| `/deals` / top-deals heading | 788, 812, 806 | 202, 223, 215 | Rendered; no console warnings/errors |
| `/analyse` / market-analysis heading | 1,131, 760, 823 | 212, 188, 251 | First sample slower while lazy route/data settled; later samples normal |
| `/listing/emt1opqj` / valuation heading | 1,028, 997, 900 | 223, 304, 224 | Rendered six-image detail; no console warnings/errors |

## Warm interaction samples

| Workflow | Samples (ms) | Result |
| --- | ---: | --- |
| Explorer search changes | 574, 378, 403 | Search state and results updated |
| Deal-board tabs: all, ML, review | 391, 419, 396 | Selected tab, explanation, and listing set updated |
| Analysis period: 30d, 90d, 7d | 618, unchanged, 512 | The 90-day sample was already selected, so no state transition was timed |

## Responsive checks

- At 390px requested width, the browser reported 375px content width because of the scrollbar;
  home, explorer, deals, listing, and alerts all had `scrollWidth == clientWidth` (no horizontal
  overflow). Long pages remained scrollable and primary controls were present.
- At 768px requested width, explorer, deals, and listing also had no horizontal overflow.
- The viewport was restored to the default 1405x1234 browser state after testing.
- Contrast checks passed for light and dark themes. No official latency budget was found, so no
  arbitrary pass/fail threshold is asserted.
