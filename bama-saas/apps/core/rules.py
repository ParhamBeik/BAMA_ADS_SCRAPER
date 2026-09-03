"""Identifiers whose presence excludes an ad from analytics."""

HARD_RULE_IDS = frozenset({
    "code_missing", "price_missing_for_lumpsum", "price_too_low", "brand_missing",
    "photo_missing",
})
