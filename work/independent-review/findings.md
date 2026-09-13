# Findings — 2026-09-12

## Medium — listing-gallery images remain inaccessible in production

Fresh browser inspection of `/listing/emt1opqj` found six loaded listing photographs, each with `alt=""`. These are the primary content of the gallery, not decorative thumbnails. The local correction supplies the listing title and one-based image position. It is verified locally but intentionally not published by this review.

## Low operational — four failed coverage runs are still recorded

There are four failed `coverage` records in the last 24 hours. The latest worker pipeline completed successfully and no worker `HTTP 503` log entries occurred in that same period, but the failed records carry no diagnostic detail. This is not evidence of a current outage; it is an observability gap that prevents root-cause classification.

## Low operational / medium model-integrity risk — persisted ML artifacts use a different sklearn patch version

The ML service logged seven `InconsistentVersionWarning` messages in the last 24 hours: artifacts were saved with 1.9.0 and load under 1.9.1. The locked project dependency resolves to 1.9.0, while the live runtime reports 1.9.1. Training and scoring continue, so no scoring failure is demonstrated. Aligning the runtime and validating or retraining the live artifacts requires an operational artifact decision and was not changed here.

## Observation — the production build retains a lazy chart chunk above Vite's 500 kB warning threshold

The current build reports a 557 kB (189 kB gzip) chart chunk. It is lazy-loaded and the repository deliberately keeps it out of the entry graph, so this is not a demonstrated initial-load regression. No performance budget or fresh benchmark established that further splitting is justified.
