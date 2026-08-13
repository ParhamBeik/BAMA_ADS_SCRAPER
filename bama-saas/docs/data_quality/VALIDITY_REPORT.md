# Bama Data Quality Phase 2 — Validity & Outliers

Snapshot: `2026-08-11 16:08:53.862634+00`
Ads: **65,930** (active 36,054 / removed 29,876)
Freshness lag: **0.0 h**
Orphans (brand/model/variant/city): 0/0/0/0
Invalid status: **0**

## Semantic sentinels

- Placeholder body colors: **198**
- Placeholder / default trim: **0**
- `year=0`: **0**
- Year present but unnormalized: **0**
- Negative mileage: **0**
- Non-positive price: **0**
- Price below verify band (<10M): **0**
- Price above verify band: **19**
- Mileage above verify band: **53**

## Active price distribution

- Priced active ads: **28,539**
- Median / p01 / p99: **2290000000** / **90000000** / **27561999999.999973**

## Quality flags (soft verify)

- `(none)`: 65,421
- `mileage_zero_on_old_car`: 341
- `mileage_implausible_for_age`: 139
- `mileage_implausible`: 53
- `price_too_high`: 19
- `mileage_regression`: 6
- `price_too_low`: 2
- `price_sentinel`: 1

## Cohort flags

- `(none)`: 65,304
- `price_outlier_low`: 442
- `price_outlier_high`: 184

## Listing episodes

- Listing episodes: **65,908**

## Ingest rejects (hard quarantine)

- `price_too_low`: 518
- `price_sentinel`: 3

## Recent data-quality snapshots (drift inputs)

| date | active | rejects | null year_j | null mileage | null price | median price | alarms | unconf brands | unconf models |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-08-11 | 36705 | 22 | 0.0001 | 0.0 | 0.2083 | 2270000128 | 0 | 1 | 4 |
| 2026-08-10 | 36241 | 4 | 0.0001 | 0.0 | 0.2135 | 2300000000 | 0 | 0 | 3 |
| 2026-08-08 | 38890 | 19 | 0.0001 | 0.0 | 0.2127 | 2310000000 | 0 | 0 | 0 |
