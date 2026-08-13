# Bama Ad Completeness Profile

Snapshot: `2026-08-11 16:08:14.951509+00`
Ads: **65,838** (active 35,962 / removed 29,876)
Freshness lag: **1.22 h**
Orphans brand/model/variant/city: 0/0/0/0
Invalid status: **0**

## Field families

- **Presentation evidence**: 88.75% complete (11.2489 missing share)
- **Descriptive attributes**: 99.70% complete (0.3021 missing share)
- **Catalog dimensions**: 100.00% complete (0.0046 missing share)
- **Conditional pricing**: 100.00% complete (0.0 missing share)
- **Identity and provenance**: 100.00% complete (0.0 missing share)
- **Lifecycle**: 100.00% complete (0.0 missing share)

## Exceptions

- Missing descriptions: **4,681**
- Placeholder body colors: **198**
- Placeholder trim: **1**
- Missing normalized year: **3**
- Raw year=0: **3**
- Lump-sum with price: **53,892** / **53,892**
- Complete installment rows: **1,703** / **1,703**

## Lowest completeness fields

- `current_installments`: 2.82% (missing 63,979; Conditional)
- `current_payment`: 2.82% (missing 63,979; Conditional)
- `current_prepayment`: 2.82% (missing 63,979; Conditional)
- `dealer_id`: 28.7% (missing 46,943; Optional)
- `removed_at`: 45.38% (missing 35,962; Conditional)
- `current_price`: 84.31% (missing 10,329; Conditional)
- `description_length`: 92.9% (missing 4,673; Expected when supplied)
- `image_count`: 94.41% (missing 3,680; Expected)
- `body_color`: 100.0% (missing 0; Observed)
- `body_status`: 100.0% (missing 0; Observed)
- `body_type`: 100.0% (missing 0; Observed)
- `brand_id`: 100.0% (missing 0; Observed)
