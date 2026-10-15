"""The home page's market answers: one cache key and one producer each.

Shared by the views that serve them and by ``jobs.warm_pulse``, which computes
them on the worker right after the warm tick rewrites their inputs. Before
that step a visitor paid for every miss: ``turnover`` alone is ~10 s on the
VPS (measured 2026-10-15), the cache held it for two minutes, and the data only
changes every thirty — so nearly every visit recomputed it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from apps.core import research
from apps.core.api import cache_key
from apps.core.models import MarketIndex

# Longer than the warm cadence (~30 min), so the worker replaces each entry
# before it expires and a reader never waits on a miss. If the worker stops,
# the entry outlives its data by at most one cadence, which `coverage` reports.
PULSE_CACHE_SECONDS = 45 * 60

# The windows `WindowPicker` offers. A custom date range is any number of days
# and stays a live computation.
PULSE_WINDOWS = (7, 30, 90, 365)

# `_leaderboard_limit`'s default, which is what the home page requests.
DEFAULT_LIMIT = 8

# Which scopes the movers board can rank. The market series has nothing to be
# ranked against — it is one row — so it is the one scope excluded.
RANKABLE_SCOPES = tuple(
    s for s in MarketIndex.Scope.values if s != MarketIndex.Scope.MARKET
)

Answer = tuple[str, Callable[[], dict]]


def movers(scope: str, days: int, limit: int) -> Answer:
    return (cache_key("pulse:movers", {"scope": scope, "days": days, "limit": limit}),
            lambda: research.movers(scope, days=days, limit=limit))


def turnover(days: int, limit: int) -> Answer:
    return (cache_key("pulse:turnover", {"days": days, "limit": limit}),
            lambda: research.turnover(days=days, limit=limit))


def arrivals(days: int, limit: int) -> Answer:
    return (cache_key("pulse:arrivals", {"days": days, "limit": limit}),
            lambda: research.arrivals(days=days, limit=limit))


def market_read(days: int) -> Answer:
    return (cache_key("pulse:read", {"days": days}),
            lambda: research.market_read(days=days))


def default_answers() -> Iterator[Answer]:
    """Every answer the home page asks for at a picker window."""
    for days in PULSE_WINDOWS:
        yield turnover(days, DEFAULT_LIMIT)
        yield arrivals(days, DEFAULT_LIMIT)
        yield market_read(days)
        for scope in RANKABLE_SCOPES:
            yield movers(scope, days, DEFAULT_LIMIT)
