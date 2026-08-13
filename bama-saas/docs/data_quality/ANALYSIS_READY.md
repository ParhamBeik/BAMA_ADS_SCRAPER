# Analysis-Ready Data Quality

**Verdict: safe to analyze.** Active lump-sum priced ads meet the analysis-ready
contract at **99.99%** (threshold ≥95%).

Snapshot window: 2026-08-11 ~16:08–16:09 UTC (worker live; freshness lag 0–1.2 h).
Ads in profile: ~65.8k–65.9k (counts move slightly while fetching).

## Headline metrics

| Metric | Value |
|---|---:|
| All ads analysis-ready | 99.99% (65,925 / 65,930) |
| Active ads analysis-ready | 99.99% (36,050 / 36,054) |
| Active lump-sum priced analysis-ready | **99.9854%** (27,375 / 27,379) |
| Passes `quality.verified()` (no hard flags) | 65,928 / 65,930 |
| Active not ready | **4** |
| Success bar (≥95% active lump-sum priced) | **PASS** |

Source: [`ANALYSIS_READY_METRICS.json`](ANALYSIS_READY_METRICS.json) from
[`build_analysis_ready.py`](build_analysis_ready.py).

## Analysis-ready contract

An ad is analysis-ready for market/price work when all hold:

1. No hard `quality_flags` (`code_missing`, `price_missing_for_lumpsum`,
   `price_too_low`, `brand_missing`) — same gate as `quality.verified()`
2. `brand_id`, `model_id`, `variant_id`, `city_id` present
3. `year_jalali` present (cohort / year-filter key)
4. `mileage` present (0 allowed)
5. Price usable for `price_type` (lump-sum ⇒ `current_price > 0`; installment ⇒
   payment fields; negotiable may omit price)
6. `status` in `{active, removed}`
7. `publish_at` or `first_seen_at` present

**Not required for the gate:** description, body color, soft flags, cohort
outliers (outliers stay in the catalog; baselines use
`without_cohort_outliers`).

## Safe to analyze

- Identity / provenance / lifecycle field families: **100%** complete
  ([`COMPLETENESS_REPORT.md`](COMPLETENESS_REPORT.md))
- Catalog dimensions: **~100%** (orphans brand/model/variant/city = 0)
- Conditional pricing: **100% within applicable populations** — every lump-sum
  ad has a price; every installment ad has payment fields
- No distribution drift alarm on 2026-08-11 (`manage.py data_quality`:
  “no drift detected”)
- Analytics services already funnel aggregates through `verified()` /
  `verified_by_ad()` / `without_cohort_outliers()` (deal scores, insights,
  metrics, bollinger, fair price, liquidity, true-mean, retention, index inputs)

## Exclude or treat carefully

| Bucket | Count (approx.) | How to treat |
|---|---:|---|
| Active not analysis-ready | 4 | Exclude from market stats |
| → hard `price_too_low` | 2 | Excluded by `verified()` |
| → missing `year_jalali` (raw `year=0`) | 2 | Unrecoverable from payload; no cohort key |
| Soft quality flags (advisory) | ~500 | Keep in listings; monitor spikes |
| Cohort price outliers | ~600 | Keep in catalog; exclude from baselines only |
| Hard ingest rejects (quarantined) | ~498 | Never in `catalog_ad` (mostly `price_too_low`) |
| Missing descriptions | ~4.7k | Source-optional; do not invent text |
| Placeholder body color `-` | 198 | Cosmetic; ignore for price/year stats |
| Multi-code vehicle identities | 66 ids / 142 codes | Deduplicate carefully for “unique cars” |

## Field-family completeness (refreshed)

| Family | Complete % |
|---|---:|
| Identity and provenance | 100.00 |
| Lifecycle | 100.00 |
| Conditional pricing | 100.00 |
| Catalog dimensions | 100.00 |
| Descriptive attributes | 99.70 |
| Presentation evidence | 88.75 |

Presentation gap is almost entirely missing descriptions (not recoverable from
stored JSON when absent at source). That does **not** block price/cohort
analysis.

Raw sparsity in `current_payment` / `dealer_id` / `removed_at` is **conditional
applicability**, not broken ingestion.

## Validity / outliers (refreshed)

See [`VALIDITY_REPORT.md`](VALIDITY_REPORT.md). Highlights:

- No orphan dimensions; no invalid statuses
- Soft flags dominated by mileage heuristics (`mileage_zero_on_old_car`, etc.)
- Cohort outliers: price low/high — intentional bargain/scam signal, not deletes
- Active price median ~2.29B toman (p01/p99 wide but within verify bands for
  nearly all rows)

## Enforcement applied

| Action | Result |
|---|---|
| `backfill_normalization` | Partial batches run (presentation backfill). Year=0 rows cannot gain `year_jalali`. Full sweep paused when Postgres entered recovery under load; worker stopped during batches then restarted. |
| `flag_cohort_outliers` | 3,222 cohorts / 28,590 ads scanned; 1 flagged, 1 cleared |
| Analytics gate fixes | `deal_score_detail`, `ad_price_history`, and `market_snapshot` new/removed counts now use `verified` / `verified_by_ad` |

## Residual risks

1. **Raw `year=0` listings** — cannot normalize; leave out of year cohorts.
2. **~21% of ads lack `current_price` in the raw column** — mostly non-lump-sum /
   conditional types; use the analysis-ready price predicate, not raw null rate.
3. **Soft flags and cohort outliers** are monitoring signals; do not mass-delete.
4. **Presentation fields** (`description`, image URLs) still backfilling on some
   rows; irrelevant to price/market analysis readiness.
5. **Freshness** depends on the worker; re-run
   `python docs/data_quality/build_analysis_ready.py` before publishing figures.

## How to reproduce

```bash
cd bama-saas
docker compose up -d postgres django
python docs/data_quality/build_completeness.py
python docs/data_quality/build_validity.py
python docs/data_quality/build_analysis_ready.py
docker compose exec -T django python manage.py data_quality
```

## Bottom line

Fetched ads are **complete enough for analysis**. Use `quality.verified()` (and
`without_cohort_outliers` for baselines). Ignore description completeness for
price work. The only material active exclusions are four rows (two hard price
flags, two unknown years).
