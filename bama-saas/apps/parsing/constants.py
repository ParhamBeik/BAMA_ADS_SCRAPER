"""Shared constants for the Bama parsing layer.

Ported verbatim from ``bama-saas/app/services/ingestion.py`` (the authoritative
parser) so the pure-Python ``apps.parsing`` package reproduces the exact
change-detection thresholds used against real scraped data.
"""

from __future__ import annotations

# Dotted paths dropped before computing the semantic hash: fields that move
# without the ad's content having changed. A prefix drops the whole subtree.
#
# Measured against 1,125 consecutive version pairs from the live database, 81.2%
# differed ONLY in these fields — so four out of five stored AdVersions recorded
# nothing about the ad at all. That matters beyond storage: "a new version
# appeared" is the delta crawler's signal that a page is still moving, and a
# signal that fires 81% of the time for no reason is not a signal.
#
# Observed change rate per field across those pairs:
#   detail.rank          100.0%  feed position, not content
#   detail.time           73.2%  "2 hours ago", recomputed every fetch
#   dealer.ad_count       49.2%  how many ads the DEALER has, not this ad
#   metadata.*            48.4%  SEO tags, derived from the ad rather than part of it
#   detail.modified_date  25.7%  the source's own bookkeeping timestamp
#   dealer.score           8.0%  dealer-wide reputation, not this ad
#
# Deliberately NOT excluded, because these are real content: detail.description
# (4.8%), images (3.0%), detail.image_count (1.7%), price.* and detail.mileage.
VOLATILE_PAYLOAD_PATHS = (
    "detail.rank",
    "detail.time",
    "detail.modified_date",
    "dealer.ad_count",
    "dealer.score",
    "metadata",
)

# Bumped whenever the paths above change, so a semantic hash can be traced to the
# rule set that produced it and versions can be reproducibly recomputed.
SEMANTIC_HASH_VERSION = 2

# Retained for the ``detail``-only callers that predate the dotted paths.
VOLATILE_DETAIL_KEYS = {"time", "rank"}

# When an ad reappears after this long, a "reappeared" change event is emitted.
REAPPEAR_AFTER_SECONDS = 14 * 86400
