"""Shared constants for the Bama parsing layer.

Ported verbatim from ``bama-saas/app/services/ingestion.py`` (the authoritative
parser) so the pure-Python ``apps.parsing`` package reproduces the exact
change-detection thresholds used against real scraped data.
"""

from __future__ import annotations

# ``detail.time`` and ``detail.rank`` change on every fetch without meaning the
# ad's content changed. They are dropped before computing semantic hashes.
VOLATILE_DETAIL_KEYS = {"time", "rank"}

# When an ad reappears after this long, a "reappeared" change event is emitted.
REAPPEAR_AFTER_SECONDS = 14 * 86400
