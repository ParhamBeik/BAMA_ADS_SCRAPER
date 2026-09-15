"""The provenance envelope every read answer ships inside, and its cache.

Not view code, which is why it is not in ``views.py``: ``apps.ml.views`` already
imported ``cached`` and ``envelope`` *from the view module* to answer its own
endpoints, and a module holding forty route handlers is not a library. Both apps
import this instead.

The envelope is not decoration. These numbers come from a crawl that can be
incomplete, and a survival curve computed across a coverage hole reads crawler
downtime as cars leaving the market — so every answer carries the coverage that
qualifies it, and the two are cached as one vintage so they cannot drift apart.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Literal

from django.core.cache import cache
from django.utils import timezone
from rest_framework.response import Response

from apps.core.coverage import consecutive_blocks, coverage_state
from apps.core.models import PageCoverage

# Bumped whenever a formula changes, so a screenshotted answer can be traced to
# the logic that produced it.
METHODOLOGY_VERSION = 2

# A sweep older than this means the picture is stale enough to say so.
FRESH_WITHIN = timedelta(hours=13)

def _coverage() -> dict:
    """How much of the market the answers are actually based on.

    Judged on accumulated ``PageCoverage`` over the window rather than on one run
    having set ``reached_end``: under a rolling crawl no single run walks the
    feed end to end, so the old query reported "no completed sweep" indefinitely
    while the feed was in fact fully covered.
    """
    now = timezone.now()
    # Whether the source is refusing us, reported alongside coverage because it
    # is the *cause* of the staleness a reader is looking at. It also implies
    # removal detection is paused, so a listing shown as active may be sold.
    blocked = consecutive_blocks()

    # The same judgement `mark_inactive` acts on, not a stricter one — see
    # `coverage_state`, which is also what the sweep_freshness check reads.
    state = coverage_state(now)
    if not state.depth:
        return {"complete_sweep": False, "reason": "no pages fetched recently",
                "source_blocked": bool(blocked)}

    depth, missing, swept = state.depth, state.missing, state.complete
    last_fetch = (
        PageCoverage.objects.order_by("-fetched_at")
        .values_list("fetched_at", flat=True).first()
    )
    age = now - last_fetch if last_fetch else None
    return {
        "complete_sweep": swept,
        "swept_at": last_fetch,
        "ads_covered": depth - missing,
        "deepest_rank": depth,
        "uncovered_ranks": missing,
        "stale": bool(age and age > FRESH_WITHIN),
        "age_hours": round(age.total_seconds() / 3600, 1) if age else None,
        "source_blocked": bool(blocked),
        # Exactly the condition mark_inactive refuses to run under, so this is
        # the same fact the worker acts on.
        "removal_detection_paused": not swept,
    }


def envelope(payload: dict, *, coverage: dict | Literal[False] | None = None,
             **extra) -> Response:
    """Wrap an answer with everything needed to judge it.

    `coverage` is passed in by the cached endpoints so it keeps the vintage of
    the answer it qualifies — see `cached_answer`. Uncached endpoints read it
    fresh, which for them is the same thing.

    `coverage=False` drops the block entirely, and there are two reasons an
    answer wants that. It is operational state — whether the source is refusing
    us right now, how many hours since the last sweep — which qualifies a market
    number and is nobody's business on an endpoint that serves anonymous
    readers. And on an answer that is not about listing data at all, the block
    is not merely private but wrong: a model card is no more or less true
    because the crawl is behind.
    """
    body = {**payload, "as_of": timezone.now(),
            "methodology_version": METHODOLOGY_VERSION, **extra}
    if coverage is not False:
        body["coverage"] = coverage if coverage is not None else _coverage()
    return Response(body)


def cache_key(prefix: str, parts: dict) -> str:
    """A cache key that cannot be shaped by what the caller typed.

    Brand slugs reach us as text, and `ingest` mints collision-path slugs that
    embed the raw Persian name, spaces and all — which Django's Redis backend
    warns about on every get and memcached rejects outright. Hashing the scope
    keeps the key opaque and fixed-width; the prefix keeps it greppable.
    """
    scope = "|".join(f"{k}={parts[k]!r}" for k in sorted(parts))
    return f"{prefix}:{hashlib.sha256(scope.encode()).hexdigest()[:32]}"


def cached(key: str, seconds: int, produce):
    """Cache the answer, never the response.

    These aggregations are shared — every reader gets the same market — so they
    are worth caching. But `cache_page` wraps a view from the outside, and a hit
    returns the stored response before DRF runs at all, permission check
    included: one signed-in reader warmed the cache and, for the rest of that
    window, anyone at all could fetch the same URL and be served the answer.
    Holding the payload instead keeps the gate in front of every single request
    and still pays for the query once. See tests/test_api.py for the case that
    fails the moment this goes back to a response-level cache.
    """
    hit = cache.get(key)
    if hit is None:
        hit = produce()
        cache.set(key, hit, seconds)
    return hit


def cached_answer(key: str, seconds: int, produce) -> tuple[dict, dict]:
    """An answer and the coverage that qualifies it, kept as one vintage.

    Coverage is what tells a reader whether to trust the number printed beside
    it, so the two have to age together. Held on separate clocks they drift: a
    coverage reading taken after the crawl closed a gap ends up badging a figure
    that was computed while the gap was open, which reads as "complete sweep" on
    a number the hole is precisely the reason to doubt. The response cache this
    replaced got that right for free by storing both under one key; storing them
    as one entry is how to keep it.

    It also keeps `_coverage` off the per-request path for these endpoints —
    `find_gaps` pulls a 13-hour window of rows into Python, which is the
    heaviest query in the envelope.
    """
    hit = cache.get(key)
    if hit is None:
        hit = {"payload": produce(), "coverage": _coverage()}
        cache.set(key, hit, seconds)
    return hit["payload"], hit["coverage"]
