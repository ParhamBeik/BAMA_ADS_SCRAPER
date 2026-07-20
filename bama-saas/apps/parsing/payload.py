"""Raw-payload utilities: history-blob unpacking and forbidden-key stripping.

- ``unpack_payload`` decodes stored payload blobs. It is used to
  replay ``ad_versions.payload_zlib`` blobs stored in the scraper's
  ``history.db``.
- ``pure_ad`` removes scraper
  bookkeeping keys (``computed_publish_date_jalali``, ``fetch_time_ts``) so
  that ``ads.json`` files equal the raw Bama payloads.

Only these two functions are ported from ``fetch.py``; the live-fetch helpers
(``iter_ads``, ``warmup``, ``load_brand_aliases``, ``clean_name``,
``intended_path_for_ad``) are Phase 5 work and intentionally omitted.
"""

from __future__ import annotations

import json
import zlib
from typing import Any

# Keys the scraper adds for its own bookkeeping; they must never be stored in
# ads.json, otherwise the raw payload would no longer match Bama's response.
FORBIDDEN_AD_KEYS = ("computed_publish_date_jalali", "fetch_time_ts")


def unpack_payload(blob: bytes) -> dict[str, Any]:
    """Decompress + JSON-decode a zlib-compressed payload blob from history.db."""
    return json.loads(zlib.decompress(blob).decode())


def pure_ad(ad: dict[str, Any]) -> dict[str, Any]:
    """Keep ads.json equal to Bama payloads, not scraper bookkeeping."""
    return {key: value for key, value in ad.items() if key not in FORBIDDEN_AD_KEYS}
