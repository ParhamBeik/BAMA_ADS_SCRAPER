# Data quality rollout: canonical identity, cash price, working photo

An ad exists in the catalog only with a cash price and a working photo, and each
real car has exactly one catalog model. PostgreSQL remains the source of truth.

## What ships

- `apps/core/taxonomy.csv` — the reviewed map from Bama's
  `(brand_fa, title model)` to `(model, trim prefix)`. Brand is the badge Bama
  sends; the maker (`normalization.MANUFACTURER`) is searchable. A pair missing
  from the file keeps Bama's labels and is marked `needs_review`; add it here.
- Ingest refuses new ads with no cash price (`cash_price_required`) or no photo
  (`photo_missing`).
- `price_basis_unclear` no longer fires on financing words; migration 0039
  recomputes it for every stored ad.
- `image_sweep` (warm cadence) re-checks active covers about daily; a cover the
  CDN refuses hides the ad (`image_dead_at`) without deleting it.
- Brand/model/variant pickers list only rows some ad uses.

## Production order

Each step needs explicit authorization. Stop `bama-worker` for steps 2 and 4:
both hold row locks for minutes.

1. Deploy (runs migrations 0037–0039).
2. `python manage.py canonicalize_catalog` — dry run, rolled back. Review
   `ads_changed`, `unmapped_pairs`, `snapshots`, `orphan_variants_unresolved`,
   `deleted`. Then `--apply`.
3. Rebuild analytics: `snapshot`, `market_index`, `deal_scores`, `ml_score`.
4. `python manage.py purge_ineligible` — dry run. Review counts, then
   `--apply`. **Irreversible.**
5. Confirm: no unconfirmed models with ads, no empty models or variants, listing
   counts match `scorable_rows()`.
