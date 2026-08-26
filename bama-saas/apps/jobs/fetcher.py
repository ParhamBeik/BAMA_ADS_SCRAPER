"""Talking to bama.ir: when not to, what we covered, and the fetch loop itself.

Feed facts this module encodes, verified against the live API:

* ``pageIndex`` is **0-based**. Starting at 1 silently skips the newest ~30 ads
  on every single run.
* Each ad carries ``detail.rank``, its position in the recency-ordered feed,
  following ``rank ~= 30 * pageIndex + 1..30``.
* The feed ends naturally: the page past the last ad returns an empty list with
  HTTP 200. That empty page is the only proof of full coverage — and a degraded
  API serves exactly the same thing, which is why it is confirmed twice and
  cross-checked against the depth the feed is already known to reach. A run that
  started mid-feed is held to a tighter bar than a full sweep, because it has
  walked none of the pages above it (see ``end_of_feed_is_credible``).
* Insertions push ads to higher ranks (a forward sweep re-reads them, which is
  idempotent). Deletions pull ads to *lower* ranks, behind pages already read —
  the one case that silently loses ads, and the reason every page writes a
  ``PageCoverage`` row instead of trusting elapsed wall-clock time.
"""

from __future__ import annotations

import logging
import math
import os
import random
import signal
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Max, Q
from django.utils import timezone as djtz

from apps.core.models import FetchRun, PageCoverage
from apps.jobs.ingest import ingest_ad, reset_cache, reset_price_cache
from apps.jobs.parsing import extract_ad, parse_publish_time

logger = logging.getLogger("bama.worker")

PAGE_SIZE = 30
FIRST_PAGE = 0

# ===========================================================================
# Coverage arithmetic over PageCoverage — pure, no network, no writes
# ===========================================================================
#
# Every fetched page is an inclusive rank interval. Union those intervals and
# the holes are exactly the ads nobody looked at in the window.

# How far back the depth ratchet looks. Long enough that a truncated run cannot
# lower the ceiling, short enough that one absurd rank_hi from a bug ages out
# instead of poisoning it forever — a permanently inflated ceiling means
# coverage is never "complete", which silently disables removal detection.
FEED_DEPTH_WINDOW_DAYS = 30

# One coverage pass. Coverage is judged over windows of this length rather than
# per-run, so it does not matter which run covered which page.
COVERAGE_WINDOW_HOURS = 24


def known_feed_depth() -> int | None:
    """Deepest rank any page covered recently, capped by the last real end-of-feed.

    A max over accumulated ``PageCoverage`` needs no run to survive start to
    finish: three interrupted sweeps that jointly walk the feed give the same
    ceiling as one clean one. The old rule ("deepest rank of the last sweep that
    set reached_end") made the ceiling hostage to one uninterrupted ~936-page
    walk against a host that answers 503 — 11 of 28 sweeps completed, so for
    long stretches there was no ceiling and removal detection stalled.

    The cap matters because a ratchet is one-way and the feed shrinks: it
    reached rank 34,107 and a day later ended at ~33,112. Uncapped, coverage
    would be demanded for ~1,000 ranks that no longer exist. The most recent
    authoritative statement wins — a plain min would let a stale end-of-feed
    hide a tail that has since grown.

    The cap deliberately accepts a credible end-of-feed from ANY mode, not only
    a full sweep. Requiring ``mode=FULL`` looked safe and was the bug: rolling
    coverage replaced the full sweep entirely, so no run could ever lower the
    ceiling again. Over one week production logged 624 backfills that reached the
    real end of the feed and 0 full sweeps, so the ratchet stayed pinned at a
    high-water mark from days earlier, ~50 ranks of it referring to ads that no
    longer existed. Coverage could never be complete, and removal detection was
    silently disabled for over 24 hours. The credibility check in
    ``end_of_feed_is_credible`` is what keeps this honest — a shallow delta that
    hits an empty page never sets ``reached_end``.
    """
    since = djtz.now() - timedelta(days=FEED_DEPTH_WINDOW_DAYS)
    covered = PageCoverage.objects.filter(fetched_at__gte=since)
    ratchet = covered.aggregate(depth=Max("rank_hi"))["depth"]
    if not ratchet:
        return None

    last_end = (
        FetchRun.objects.filter(
            stop_reason=FetchRun.StopReason.END_OF_FEED,
            reached_end=True, status=FetchRun.Status.SUCCEEDED,
            started_at__gte=since,
        )
        # feed_end_rank is the honest bound; deepest_rank is the fallback for
        # runs recorded before that column existed.
        .filter(Q(feed_end_rank__isnull=False) | Q(deepest_rank__isnull=False))
        .order_by("-started_at")
        .values("started_at", "feed_end_rank", "deepest_rank").first()
    )
    if not last_end:
        return ratchet
    ceiling = last_end["feed_end_rank"]
    if ceiling is None:
        ceiling = last_end["deepest_rank"]
    deeper_since = covered.filter(
        fetched_at__gte=last_end["started_at"]
    ).aggregate(depth=Max("rank_hi"))["depth"]
    return max(ceiling, deeper_since or 0)


def _merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping *and* adjacent inclusive integer intervals."""
    merged: list[tuple[int, int]] = []
    for lo, hi in sorted(intervals):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def find_gaps(since: datetime | None = None, max_rank: int | None = None,
              until: datetime | None = None) -> list[tuple[int, int]]:
    """Rank ranges no PageCoverage row covers in the window, sorted and merged.

    ``max_rank`` defaults to the deepest rank seen in the window, so the result
    holds interior holes only; pass it to also demand tail coverage. ``until``
    bounds the window on the right, which is what lets a caller ask "was the
    feed fully covered in the window *before* this one".
    """
    qs = PageCoverage.objects.all()
    if since is not None:
        qs = qs.filter(fetched_at__gte=since)
    if until is not None:
        qs = qs.filter(fetched_at__lt=until)
    covered = _merge(list(qs.values_list("rank_lo", "rank_hi")))
    if not covered:
        # Nothing observed: everything up to max_rank is a gap, but with no
        # ceiling there is nothing meaningful to claim.
        return [(1, max_rank)] if max_rank else []

    ceiling = max_rank if max_rank is not None else covered[-1][1]
    gaps: list[tuple[int, int]] = []
    cursor = 1
    for lo, hi in covered:
        if lo > cursor:
            gaps.append((cursor, min(lo - 1, ceiling)))
        cursor = max(cursor, hi + 1)
        if cursor > ceiling:
            break
    if cursor <= ceiling:
        gaps.append((cursor, ceiling))
    return [(lo, hi) for lo, hi in gaps if lo <= hi]


def coverage_is_complete(since: datetime, until: datetime | None = None) -> bool:
    """True when every rank up to the known depth was fetched in the window.

    With no known depth there is nothing to prove against, so this returns False
    — callers must fail closed.
    """
    depth = known_feed_depth()
    if not depth:
        return False
    return not find_gaps(since=since, until=until, max_rank=depth)


def plan_backfill(gaps: list[tuple[int, int]],
                  page_size: int = PAGE_SIZE) -> list[tuple[int, int]]:
    """Turn rank gaps into inclusive ``(start_page, end_page)`` ranges.

    Rank ``r`` lives on page ``(r - 1) // page_size`` (pageIndex is 0-based).
    Adjacent ranges collapse, so two neighbouring holes inside one page yield a
    single one-page refetch.
    """
    return _merge([
        ((max(lo, 1) - 1) // page_size, (max(hi, 1) - 1) // page_size) for lo, hi in gaps
    ])


# ===========================================================================
# Crawl gate — when not to talk to bama.ir at all
# ===========================================================================
#
# Learned from the 2026-08-16 incident: bama.ir's CDN started answering every
# request with a branded 403 block page. `_retryable` correctly refuses to retry
# a 403 inside a run, but nothing stopped the *scheduler* from launching a fresh
# run every 10-15 minutes, so the stack fired 485 requests into an active ban
# over six hours. Retrying into a WAF block cannot succeed and plausibly renews
# the ban.
#
# This once also enforced an hourly page budget, on the theory that a 286-page
# burst triggered it. That theory was wrong: the block is on our egress IP —
# every path on bama.ir answered 403 from a London datacenter exit (AS202422)
# while divar.ir and google.com answered 200 from the same host. Throttling a
# request rate that was never the problem only slowed the crawl, so the fix
# belongs at the network layer (an Iranian egress), not here.
#
# The breaker needs no new state: every run already persists its outcome, so
# "how many blocks in a row" is a query — which is also correct across a
# container restart, unlike a module-level counter. On a healthy history it is
# completely inert.

# 429 is deliberately NOT here: that is ordinary rate limiting, and
# `fetch_page_with_backoff` already waits it out inside the run.
WAF_STATUS = 403

# First cooldown is one pipeline tick, then it doubles: 15m, 30m, 1h, ... 6h.
BASE_COOLDOWN = timedelta(seconds=int(os.environ.get("BAMA_BLOCK_COOLDOWN", 900)))
MAX_COOLDOWN = timedelta(seconds=int(os.environ.get("BAMA_BLOCK_COOLDOWN_MAX", 21600)))

# Never stop probing entirely. The ban lifts on bama.ir's schedule, not ours,
# and a breaker that latches open needs a human to notice — exactly the failure
# mode that let this run unattended for six hours.
MAX_BACKOFF_DOUBLINGS = 8

# Longer than the max cooldown, so a streak survives its own quiet period.
STREAK_LOOKBACK = timedelta(hours=48)


class CrawlBlocked(RuntimeError):
    """Do not fetch right now. Callers must treat this as a skip, not a failure.

    Recording it as a failure would light up ``failed_runs`` on the health page
    for a cooldown the system chose on purpose, burying the real signal.
    """


class FetchLeaseBusy(CrawlBlocked):
    """Another process already owns the single live-fetch lease."""


def is_waf_block(exc: BaseException) -> bool:
    return (isinstance(exc, requests.HTTPError)
            and getattr(exc.response, "status_code", None) == WAF_STATUS)


def _last_runs(limit: int = 40):
    return list(
        FetchRun.objects.filter(
            source=FetchRun.Source.LIVE_FETCH,
            created_at__gte=djtz.now() - STREAK_LOOKBACK,
        )
        .order_by("-created_at")
        .values("status", "stop_reason", "finished_at", "started_at", "created_at")[:limit]
    )


def consecutive_blocks() -> int:
    """How many runs in a row ended blocked, counting back from the newest.

    Counts across modes: a blocked delta and a blocked backfill are the same ban,
    and separating them would let two schedules each probe at full rate.
    """
    streak = 0
    for run in _last_runs():
        if run["stop_reason"] == FetchRun.StopReason.BLOCKED:
            streak += 1
        elif run["status"] == FetchRun.Status.RUNNING:
            # The in-flight run asking this question. Ignore it rather than
            # letting it break its own streak.
            continue
        else:
            break
    return streak


def cooldown_until():
    """When the breaker reopens, or None if fetching is allowed now."""
    streak = consecutive_blocks()
    if streak == 0:
        return None
    last = next((r for r in _last_runs()
                 if r["stop_reason"] == FetchRun.StopReason.BLOCKED), None)
    if last is None:
        return None
    at = last["finished_at"] or last["started_at"] or last["created_at"]
    doublings = min(streak - 1, MAX_BACKOFF_DOUBLINGS)
    return at + min(BASE_COOLDOWN * (2 ** doublings), MAX_COOLDOWN)


def check_gate() -> None:
    """Raise :class:`CrawlBlocked` if we must not fetch; otherwise return.

    Returning means "fetch whatever you were going to fetch, at whatever rate".
    This gate caps nothing on a healthy history.
    """
    until = cooldown_until()
    if until is not None and djtz.now() < until:
        streak = consecutive_blocks()
        remaining = (until - djtz.now()).total_seconds()
        logger.warning(
            "event=bama_crawl_gated reason=waf_block consecutive=%d "
            "cooldown_remaining_s=%.0f until=%s", streak, remaining, until.isoformat(),
        )
        raise CrawlBlocked(
            f"bama.ir returned {WAF_STATUS} on {streak} consecutive run(s); "
            f"next attempt in {remaining / 60:.0f} min (until {until.isoformat()})"
        )


# ===========================================================================
# HTTP
# ===========================================================================

SEARCH_URL = "https://bama.ir/cad/api/search"
WARMUP_URL = "https://bama.ir/car?image=1&priced=1"

# The population this app collects: listings that have a photo and a price.
#
# These belong on the *data* request, not only on the warm-up page load. For a
# long time they were set only on WARMUP_URL — which just establishes cookies —
# so every sweep actually paged through the unfiltered feed. Measured on page 3:
# without them 4 of 30 ads have no photo, with them 0 of 30. That is where the
# 8,889 photoless rows came from, and why `photo_missing` was still quarantining
# ~2.3k ads a day after the rule shipped.
#
# `1`, not `true` — the API answers 500 to the boolean spelling.
FEED_FILTERS = "image=1&priced=1"

HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa,en;q=0.9",
    "Referer": "https://bama.ir/car",
    "X-Requested-With": "XMLHttpRequest",
}

# Adaptive backoff on 429 / 5xx. Measured over 39 days, 55 runs died on a 503
# and 54 on connection-retry exhaustion: 3 retries capped at 30s gave up after
# about a minute, which is shorter than bama.ir's outages actually last. Five
# retries capped at 120s rides out the common case; anything longer is better
# handled by ending the run and letting gap repair refetch next tick.
MAX_RETRIES = 5
BACKOFF_BASE = 1.0
BACKOFF_CAP = 120.0
# Without jitter every retry after a shared outage fires at the same instant.
BACKOFF_JITTER = 0.25
# A server asking to be left alone for an hour must not park a whole sweep.
RETRY_AFTER_CAP = 120.0

# An empty page is indistinguishable from the end of the feed, so it is always
# confirmed with a second request after this pause before being believed.
EMPTY_PAGE_RECHECK_PAUSE = 2.0

# How much shallower than the last completed sweep an "end of feed" may be
# before it is disbelieved. Deliberately generous — it catches a truncated
# crawl, not market size. The failure it exists for is recorded: a sweep stopped
# at 266 pages against a ~1100-page feed, stamped reached_end, and every
# consumer then treated three quarters of the market as having vanished.
MIN_END_OF_FEED_DEPTH_RATIO = 0.5

# The same question for a run that did NOT start at page 0. A full sweep earns
# the generous bar above by having walked everything before it; a bounded
# backfill has proven only that its own range ended, which the module docstring
# already notes is indistinguishable from a degraded API. So it is asked a
# narrower question: did the feed end roughly where the ceiling said it would?
#
# A backfill that genuinely walks off the end lands within a page or two
# (production: page 1157 against a ceiling of 1159). A spurious empty page
# mid-feed does not. At the 0.5 bar above, a blip at page 700 of a 1133-page
# feed would have been believed and the ceiling would have collapsed by 13,000
# ranks — the 266-page incident in a new costume.
#
# Expressed as a shortfall in pages rather than a bare ratio because a ratio
# alone is wrong at both ends: 5% of a 1,159-page feed is a reasonable 58 pages,
# but 5% of a 4-page feed is 0.2, which would reject a feed that legitimately
# shrank by a single page. The floor exists only so a tiny feed is not judged on
# a proportion; it is deliberately one page, because anything looser lets a
# shallow delta that ran out of new ads pass for the end of the feed.
BOUNDED_END_SHORTFALL_RATIO = 0.05
MIN_BOUNDED_END_SHORTFALL_PAGES = 1

# ...but a shortfall the ratio rejects can still be true, and on 2026-08-25 it
# was: adding `image=1&priced=1` to the data request shortened the feed from
# ~34,500 ads to ~28,710 in one step. That is a 192-page shortfall against a
# 57-page tolerance, so every coverage run walked to page 957, got a confirmed
# empty page, was disbelieved, and recorded nothing — leaving 5,700 ranks
# permanently "uncovered" and freezing removal detection for a day.
#
# The bar was not wrong; nothing could ever clear it. Measured day-over-day feed
# drift is under 1% (29,121 -> 35,000 across August, reversing some days), so
# several independent runs agreeing on the same ending rank cannot be noise —
# whereas one spurious empty page reads exactly like the 2026-08-16 blip the
# ratio exists to reject. Agreement, not size, is what earns belief.
#
# Deliberately narrow: corroboration only *lowers* an existing ceiling. A cold
# database still cannot be taught a depth by empty pages alone.
END_AGREEMENT_RUNS = 3            # including the run asking
END_AGREEMENT_RATIO = 0.01        # "the same place" = within 1% of each other
END_AGREEMENT_WINDOW_HOURS = 6

# Delta depth floor. Measured churn is ~1.5 new ads/min; after downtime we scan
# at least deep enough to cover what landed while we were away.
CHURN_ADS_PER_MIN = 1.5
DELTA_FLOOR_MAX_PAGES = 40
DEFAULT_MAX_STALE_PAGES = 3
MAX_RANK_DRIFT = PAGE_SIZE * 4

# Persist the run row every N ads to bound progress loss on a long run.
SAVE_EVERY = 500
FETCH_LEASE_KEY = 0x42414D41  # "BAMA"


def create_session(cookie: str | None = None) -> requests.Session:
    session = requests.Session()
    session.headers.update({**HEADERS, **({"Cookie": cookie} if cookie else {})})
    return session


def warmup(session: requests.Session, request_timeout: int) -> None:
    """Prime cookies before the first API call; ignore network errors."""
    try:
        response = session.get(WARMUP_URL, timeout=request_timeout)
        logger.info("event=bama_warmup status=%s", getattr(response, "status_code", "unknown"))
    except requests.RequestException as exc:
        logger.warning("event=bama_warmup_failed error=%s", exc)


# Sold-page characterisation, measured 2026-08-26 from a Mac (not the VPS):
# a listing Bama has taken down answers HTTP 410 with the image alt
# "این آگهی فروخته شد!" and a Nuxt payload of "حذف شده است". Live ads answer
# 200. Some rows we already marked removed still answer 200 with a dated
# title — those stay a feed-absence question, not a detail-page one.
SOLD_PAGE_MARKERS = ("این آگهی فروخته شد", "حذف شده است")
DETAIL_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"


def detail_says_sold(status: int, body: str = "") -> bool:
    """True when Bama's own detail page says this listing is gone."""
    if status == 410:
        return True
    text = body or ""
    return any(marker in text for marker in SOLD_PAGE_MARKERS)


def fetch_ad_page(session: requests.Session, url: str, request_timeout: int
                  ) -> tuple[int, str]:
    """One listing page. 410 is a successful observation, not an error.

    Overrides the session's JSON Accept: this is an HTML document. A 403 still
    raises so the crawl gate can see it.
    """
    response = session.get(
        url, timeout=request_timeout, headers={"Accept": DETAIL_ACCEPT},
    )
    if response.status_code == WAF_STATUS:
        response.raise_for_status()
    return response.status_code, response.text or ""


def fetch_page(session: requests.Session, page: int, request_timeout: int) -> list[dict[str, Any]]:
    """One page, with banner rows dropped."""
    response = session.get(f"{SEARCH_URL}?pageIndex={page}&{FEED_FILTERS}",
                           timeout=request_timeout)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        logger.warning("event=bama_http_error page=%d status=%s retryable=%s",
                       page, getattr(response, "status_code", "unknown"), _retryable(exc))
        raise
    ads = response.json().get("data", {}).get("ads", [])
    return [ad for ad in ads if isinstance(ad, dict) and ad.get("type") != "banner"]


def _retryable(exc: Exception) -> bool:
    """429 and 5xx are worth waiting out; 4xx and malformed JSON are not."""
    if isinstance(exc, requests.HTTPError):
        status = getattr(exc.response, "status_code", None)
        return status == 429 or (status is not None and 500 <= status < 600)
    return isinstance(exc, requests.RequestException)


def retry_after_seconds(exc: Exception) -> float | None:
    """The server's own Retry-After in seconds, when it sent a usable one.

    Both RFC forms accepted (a delay, or an HTTP date). Capped, and nonsense is
    ignored rather than raised on — a malformed header must never take down the
    retry path that exists to survive a misbehaving server.
    """
    response = getattr(exc, "response", None)
    raw = (getattr(response, "headers", None) or {}).get("Retry-After")
    if not raw:
        return None
    raw = str(raw).strip()
    try:
        seconds = float(raw)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        seconds = (parsed - datetime.now(timezone.utc)).total_seconds()
    return None if seconds < 0 else min(seconds, RETRY_AFTER_CAP)


def backoff_delay(exc: Exception, attempt_delay: float) -> float:
    """How long to wait before the next attempt.

    Retry-After wins when present: it is the server stating its own terms, and
    guessing an exponential curve against an explicit instruction is how a
    client earns a ban. Jitter is applied either way, so simultaneous clients do
    not resynchronise into a second thundering herd.
    """
    return (retry_after_seconds(exc) or attempt_delay) * (1 + random.uniform(0, BACKOFF_JITTER))


def fetch_page_with_backoff(session, page: int, request_timeout: int) -> list[dict[str, Any]]:
    """``fetch_page`` with Retry-After-aware jittered backoff. Re-raises when spent."""
    delay = BACKOFF_BASE
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fetch_page(session, page, request_timeout)
        except Exception as exc:  # noqa: BLE001
            if attempt >= MAX_RETRIES or not _retryable(exc):
                logger.warning(
                    "event=bama_fetch_failed page=%d attempt=%d status=%s "
                    "retryable=%s error=%s",
                    page, attempt + 1,
                    getattr(getattr(exc, "response", None), "status_code", "n/a"),
                    _retryable(exc), exc,
                )
                raise
            sleep_for = backoff_delay(exc, delay)
            logger.warning("event=bama_fetch_retry page=%d attempt=%d delay_s=%.1f error=%s",
                           page, attempt + 1, sleep_for, exc)
            time.sleep(sleep_for)
            delay = min(delay * 2, BACKOFF_CAP)
    raise AssertionError("unreachable")  # pragma: no cover


def rank_of(ad: dict[str, Any], page: int, offset: int) -> int:
    """The ad's feed rank, falling back to page arithmetic when absent or absurd."""
    fallback = PAGE_SIZE * page + offset
    try:
        rank = int((ad.get("detail") or {})["rank"])
    except (KeyError, TypeError, ValueError):
        return fallback
    if rank < 1 or abs(rank - fallback) > MAX_RANK_DRIFT:
        logger.warning("event=bama_rank_invalid page=%d offset=%d rank=%s fallback=%d",
                       page, offset, rank, fallback)
        return fallback
    return rank


def iter_pages(session, *, max_ads: int, page_pause: float, request_timeout: int,
               start_page: int = FIRST_PAGE,
               end_page: int | None = None) -> Iterator[tuple[int, list[tuple[dict, int]]]]:
    """Yield ``(page_index, [(ad, rank), ...])``. A trailing empty list ends the feed."""
    page = start_page
    yielded = 0
    while yielded < max_ads and (end_page is None or page <= end_page):
        ads = fetch_page_with_backoff(session, page, request_timeout)
        if not ads:
            # A throttled or briefly-degraded API answers 200 with an empty ad
            # list, byte-for-byte identical to the end of the feed. Believing it
            # truncates the sweep *and* stamps reached_end, so the tail is never
            # revisited — and gap repair cannot see the hole either, because its
            # ceiling is the deepest rank actually observed.
            time.sleep(EMPTY_PAGE_RECHECK_PAUSE)
            ads = fetch_page_with_backoff(session, page, request_timeout)
        if not ads:
            yield page, []
            return
        rows: list[tuple[dict, int]] = []
        for offset, ad in enumerate(ads, start=1):
            if yielded >= max_ads:
                break
            rows.append((ad, rank_of(ad, page, offset)))
            yielded += 1
        ranks = [rank for _, rank in rows]
        if len(set(ranks)) != len(ranks):
            logger.warning("event=bama_rank_duplicate page=%d rows=%d; using page arithmetic",
                           page, len(rows))
            rows = [(ad, PAGE_SIZE * page + offset)
                    for offset, (ad, _) in enumerate(rows, start=1)]
        yield page, rows
        page += 1
        time.sleep(page_pause)


# ===========================================================================
# The run
# ===========================================================================


@contextmanager
def _fetch_lease():
    """Serialize delta, full, backfill and admin-triggered fetches.

    ponytail: one PostgreSQL advisory lock; per-source locks only if the
    deployment later needs concurrent fetch classes.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [FETCH_LEASE_KEY])
        acquired = bool(cursor.fetchone()[0])
    if not acquired:
        raise FetchLeaseBusy("another live fetch is already running")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [FETCH_LEASE_KEY])


@contextmanager
def _checkpoint_on_sigterm():
    """Turn SIGTERM into KeyboardInterrupt for the duration of a run.

    Python's default SIGTERM disposition kills the process outright, so
    ``docker compose stop`` mid-sweep skipped every ``except`` clause and the run
    lost its resume checkpoint. ``signal.signal`` only works on the main thread;
    in the admin endpoints' daemon threads the ValueError is expected and this
    is simply a no-op.
    """
    def _raise(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    try:
        previous = signal.signal(signal.SIGTERM, _raise)
    except ValueError:
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def last_completed_sweep_depth() -> int | None:
    """Pages read by the most recent sweep that actually reached the end."""
    return (
        FetchRun.objects.filter(mode=FetchRun.Mode.FULL, reached_end=True,
                                status=FetchRun.Status.SUCCEEDED)
        .order_by("-started_at").values_list("pages_fetched", flat=True).first()
    )


def expected_end_page() -> int | None:
    """Page index the feed is currently believed to end on, from the ratchet.

    The yardstick every mode is judged against, because it needs no run to have
    survived start to finish — unlike ``last_completed_sweep_depth``, which is
    ``None`` for weeks at a time now that rolling coverage replaced the full
    sweep.
    """
    depth = known_feed_depth()
    return None if not depth else (depth - 1) // PAGE_SIZE


def end_of_feed_is_credible(depth_reached: int, expected_depth: int | None,
                            *, bounded: bool = False) -> bool:
    """Is an apparent end-of-feed deep enough to believe?

    ``depth_reached`` is the absolute page index the feed ended at, not the pages
    this run read — a sweep resuming from a checkpoint reads few pages while
    still reaching the true bottom. A first-ever sweep has nothing to compare
    against and is believed.

    ``bounded`` marks a run that did not start at page 0 and so cannot vouch for
    everything above it; it is held to the much tighter page-shortfall bar
    (``BOUNDED_END_SHORTFALL_RATIO`` / ``MIN_BOUNDED_END_SHORTFALL_PAGES``)
    instead of the ratio.
    """
    if not expected_depth:
        return True
    if not bounded:
        return depth_reached >= expected_depth * MIN_END_OF_FEED_DEPTH_RATIO
    tolerance = max(MIN_BOUNDED_END_SHORTFALL_PAGES,
                    expected_depth * BOUNDED_END_SHORTFALL_RATIO)
    return expected_depth - depth_reached <= tolerance


def end_is_corroborated(depth_reached: int) -> bool:
    """Have enough recent runs independently ended at this same rank?

    The escape hatch for a feed that genuinely shrank further than
    ``end_of_feed_is_credible`` will accept on one reading. Kept out of that
    function so it stays pure and unit-testable on plain integers; this one
    needs the database.

    Counts only ``END_UNCONFIRMED`` rows — a run that was already believed moved
    the ratchet, so it would not be asking.
    """
    rank = PAGE_SIZE * depth_reached
    if rank <= 0:
        return False
    since = djtz.now() - timedelta(hours=END_AGREEMENT_WINDOW_HOURS)
    band = max(1, round(rank * END_AGREEMENT_RATIO))
    agreeing = FetchRun.objects.filter(
        source=FetchRun.Source.LIVE_FETCH,
        stop_reason=FetchRun.StopReason.END_UNCONFIRMED,
        started_at__gte=since,
        feed_end_rank__gte=rank - band, feed_end_rank__lte=rank + band,
    ).count()
    # `agreeing` excludes the asking run, whose row is still RUNNING and carries
    # no stop_reason yet — hence the -1.
    return agreeing >= END_AGREEMENT_RUNS - 1


def _delta_floor_pages(now: datetime) -> int:
    """Minimum pages a delta run should read, from the newest coverage row.

    Replaces a wall-clock guess: coverage is a recorded fact, so the floor is
    "how much feed moved since the last page we actually read".
    """
    newest = (
        PageCoverage.objects.order_by("-fetched_at")
        .values_list("fetched_at", flat=True).first()
    )
    if not newest:
        return 0
    minutes = max(0.0, (now - newest).total_seconds() / 60.0)
    return min(DELTA_FLOOR_MAX_PAGES, math.ceil(minutes * CHURN_ADS_PER_MIN / PAGE_SIZE))


def _resume_page(mode: str) -> int | None:
    """Checkpoint left by the *most recent* run of this mode, if any.

    Deliberately the latest run rather than the latest checkpointed one: a clean
    run clears its checkpoint, and that must bury an older abort's checkpoint
    instead of letting it resurrect forever.
    """
    return (
        FetchRun.objects.filter(source=FetchRun.Source.LIVE_FETCH, mode=mode)
        .order_by("-created_at").values_list("resume_from_page", flat=True).first()
    )


def fetch_live(**kwargs) -> FetchRun:
    """Public entry point: the gate, the lease, and a SIGTERM checkpoint guard.

    The gate is checked here rather than in each caller because both the
    pipeline's delta fetch and gap repair reach the network through this one
    function — and during the 2026-08-16 block they each kept probing on their
    own schedule, unaware of the other. Raises :class:`CrawlBlocked`, which
    callers treat as a skip.
    """
    check_gate()
    with _fetch_lease(), _checkpoint_on_sigterm():
        return _fetch_live(**kwargs)


def _fetch_live(*, mode: str = "delta", max_ads: int | None = None,
                page_pause: float | None = None, request_timeout: int | None = None,
                max_stale_pages: int | None = None, start_page: int | None = None,
                end_page: int | None = None) -> FetchRun:
    """Stream live Bama ads into Postgres through the shared ingest pipeline.

    ``delta``    page 0 until ``max_stale_pages`` consecutive pages carry nothing new.
    ``full``     page 0 until the empty page past the last ad (``reached_end``).
    ``backfill`` an explicit ``start_page``..``end_page`` range for gap repair.

    Returns the persisted run with ``affected_model_ids`` set, for downstream
    score refreshes.
    """
    mode = str(mode or FetchRun.Mode.DELTA)
    if mode not in FetchRun.Mode.values:
        raise ValueError(f"unknown fetch mode {mode!r}")
    if mode == FetchRun.Mode.BACKFILL and start_page is None:
        raise ValueError("backfill mode requires start_page")

    # Both ingest caches are module-global and outlive a run; anything they hold
    # may refer to rows a rollback discarded. Start cold.
    reset_price_cache()
    reset_cache()

    max_ads = int(max_ads if max_ads is not None else settings.BAMA_MAX_ADS)
    page_pause = float(page_pause if page_pause is not None else settings.BAMA_PAGE_PAUSE)
    request_timeout = int(
        request_timeout if request_timeout is not None else settings.BAMA_REQUEST_TIMEOUT
    )
    if max_stale_pages is None:
        max_stale_pages = DEFAULT_MAX_STALE_PAGES

    if start_page is None:
        # Delta NEVER resumes. Its whole job is the top of the feed, and
        # starting at a checkpoint would skip the newest ads — precisely the
        # silent loss this design exists to eliminate. Re-reading a few cheap
        # pages is the correct trade; the deep pages an aborted delta missed are
        # recovered by gap repair, which is what that exists for.
        start_page = (FIRST_PAGE if mode == FetchRun.Mode.DELTA
                      else (_resume_page(mode) or FIRST_PAGE))
    start_page = max(int(start_page), 0)
    if mode == FetchRun.Mode.BACKFILL and end_page is None:
        end_page = start_page

    floor_pages = _delta_floor_pages(djtz.now()) if mode == FetchRun.Mode.DELTA else 0
    # Read before the run row exists, so this run cannot be its own yardstick.
    # Every mode is judged now, not just FULL: a bounded backfill that walks off
    # the end of the feed has seen the same fact a full sweep would have.
    expected_depth = expected_end_page()
    if mode == FetchRun.Mode.FULL:
        expected_depth = max(expected_depth or 0, last_completed_sweep_depth() or 0) or None

    run = FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH, status=FetchRun.Status.RUNNING,
        started_at=djtz.now(), max_ads=max_ads, page_pause=page_pause,
        mode=mode, start_page=start_page,
    )
    started = time.monotonic()
    logger.info("event=bama_fetch_started run_id=%s mode=%s start_page=%d end_page=%s max_ads=%d",
                run.pk, mode, start_page, end_page, max_ads)

    affected_model_ids: set[int] = set()
    interrupted = False
    stop_reason: str | None = None
    stale_pages = 0
    total_yielded = 0
    # Where a dying run should restart: the page being processed, bumped only
    # once the current page is fully committed.
    resume_page = start_page

    try:
        try:
            session = create_session(settings.BAMA_COOKIE or None)
            warmup(session, request_timeout)
            for page, rows in iter_pages(
                session, max_ads=max_ads, page_pause=page_pause,
                request_timeout=request_timeout, start_page=start_page, end_page=end_page,
            ):
                if not rows:
                    # Depth, not mode, decides whether this is the end of the
                    # feed. `iter_pages` has already confirmed the empty page
                    # with a second request, and an empty page at index P means
                    # no ad holds a rank above PAGE_SIZE * P — a fact that does
                    # not become truer because a full sweep observed it.
                    #
                    # A wrongly-believed empty page lowers the ceiling, which is
                    # self-correcting: the coverage job probes PROBE_PAGES past
                    # the ceiling every ~10 min, and `known_feed_depth` raises it
                    # again from any page covered since. That is orders of
                    # magnitude faster than the two 24h windows a removal needs,
                    # so a transient empty response cannot delist anything.
                    #
                    # A bounded run additionally needs a ceiling to be measured
                    # against. On a cold database there is nothing to compare
                    # with, and a single backfilled page returning empty says
                    # nothing about the feed; only a full sweep, which starts at
                    # page 0 and therefore walked the whole thing, is believed
                    # without a yardstick.
                    credible = (
                        (expected_depth is not None or mode == FetchRun.Mode.FULL)
                        and (
                            end_of_feed_is_credible(
                                page, expected_depth, bounded=mode != FetchRun.Mode.FULL
                            )
                            # ...or too shallow on its own evidence, but several
                            # recent runs independently found the same ending.
                            or end_is_corroborated(page)
                        )
                    )
                    if credible:
                        run.reached_end = True
                        run.feed_end_rank = PAGE_SIZE * page
                        stop_reason = FetchRun.StopReason.END_OF_FEED
                    elif mode == FetchRun.Mode.FULL:
                        run.error = (
                            f"end-of-feed at page {page}; the feed was believed to "
                            f"reach page {expected_depth}. Treating as truncated, not "
                            "as the end of the feed."
                        )
                        stop_reason = FetchRun.StopReason.ERROR
                    elif mode == FetchRun.Mode.DELTA:
                        # A delta that ran out of new ads. It is bounded by
                        # max_ads far above the feed's end, so its empty page
                        # says nothing about depth. Local stop signal only.
                        stop_reason = FetchRun.StopReason.END_OF_FEED
                    else:
                        # Too shallow to believe on this run's own evidence —
                        # but record *what we saw*, because a feed that really
                        # shrank keeps producing this same reading and
                        # `end_is_corroborated` counts these rows. Discarding
                        # the observation is what made the 2026-08-25 stall
                        # unrecoverable: the ratchet had no route down.
                        #
                        # `reached_end` stays False, so `known_feed_depth` still
                        # ignores this run until agreement promotes a later one.
                        run.feed_end_rank = PAGE_SIZE * page
                        stop_reason = FetchRun.StopReason.END_UNCONFIRMED
                    break

                resume_page = page
                page_new = page_changed = page_content_changed = 0
                skipped_before = run.skipped_count
                ranks: list[int] = []

                # One page, one transaction. A crash between the last ad and the
                # PageCoverage row used to leave a half-written page whose
                # coverage row claimed it was complete — and coverage is what gap
                # repair trusts, so the hole became permanent and invisible.
                with transaction.atomic():
                    for ad, rank in rows:
                        ranks.append(rank)
                        observed_at = datetime.now(timezone.utc)
                        extracted = extract_ad(ad, observed_at)
                        if not extracted:
                            run.skipped_count += 1
                            continue
                        result = ingest_ad(
                            extracted, run=run, observed_at=observed_at,
                            publish_at=parse_publish_time(
                                extracted.get("publish_phrase"), observed_at),
                            dealer=ad.get("dealer"), rank=rank,
                        )
                        # ad is None => a hard rule fired; the payload is
                        # quarantined in IngestReject and no Ad row exists.
                        if result.ad is None:
                            run.skipped_count += 1
                            continue

                        run.fetched_count += 1
                        if result.created:
                            run.created_count += 1
                            page_new += 1
                        else:
                            run.updated_count += 1
                        if result.price_changed:
                            run.price_change_count += 1
                            page_changed += 1
                        if result.version_created:
                            page_content_changed += 1
                        if result.cohort:
                            affected_model_ids.add(result.cohort[0])
                        if run.fetched_count % SAVE_EVERY == 0:
                            run.save()

                    total_yielded += len(rows)
                    run.pages_fetched += 1
                    run.deepest_rank = max(run.deepest_rank or 0, max(ranks))
                    PageCoverage.objects.create(
                        fetch_run=run, page_index=page, rank_lo=min(ranks),
                        rank_hi=max(ranks), ad_count=len(rows), new_count=page_new,
                        changed_count=page_changed, fetched_at=djtz.now(),
                    )
                resume_page = page + 1
                logger.info(
                    "event=bama_fetch_page run_id=%s page=%d ads=%d created=%d "
                    "price_changes=%d content_changes=%d rejected=%d",
                    run.pk, page, len(rows), page_new, page_changed,
                    page_content_changed, run.skipped_count - skipped_before,
                )

                if mode == FetchRun.Mode.DELTA:
                    # Stale = the page carried nothing new at all. Content
                    # counts: a page where sellers rewrote descriptions or added
                    # photos is not stale, and judging on new-ads-and-price-moves
                    # alone stopped the crawl while the feed was still moving.
                    if page_new == 0 and page_changed == 0 and page_content_changed == 0:
                        stale_pages += 1
                        if stale_pages >= max_stale_pages and run.pages_fetched >= floor_pages:
                            stop_reason = FetchRun.StopReason.STALE_PAGES
                            break
                    else:
                        stale_pages = 0

        except (KeyboardInterrupt, SystemExit):
            # Partial state is already in Postgres; flush as SUCCEEDED.
            # SystemExit as well as KeyboardInterrupt: `docker compose stop`
            # sends SIGTERM, and SystemExit is a BaseException that slipped past
            # both this clause and `except Exception` below — so the run flushed
            # with no checkpoint and the next sweep re-walked ~940 pages on every
            # container restart.
            interrupted = True

        run.status = FetchRun.Status.SUCCEEDED
        if interrupted:
            run.error = run.error or "interrupted"
            stop_reason = FetchRun.StopReason.INTERRUPTED
        elif stop_reason is None:
            stop_reason = (FetchRun.StopReason.MAX_ADS if total_yielded >= max_ads
                           else FetchRun.StopReason.MAX_PAGES)
        run.stop_reason = stop_reason
        # Only an abort leaves a checkpoint; any clean stop restarts at page 0.
        run.resume_from_page = (
            resume_page if stop_reason == FetchRun.StopReason.INTERRUPTED else None
        )
    except Exception as exc:  # noqa: BLE001
        run.status = FetchRun.Status.FAILED
        run.error = str(exc)[:4000]
        # BLOCKED, not ERROR, when the CDN refused us: the gate reads this field
        # to decide the cooldown, and a 403 recorded as a generic error is
        # indistinguishable from a parser bug that should be retried at once.
        run.stop_reason = (FetchRun.StopReason.BLOCKED if is_waf_block(exc)
                           else FetchRun.StopReason.ERROR)
        run.resume_from_page = resume_page
        run.finished_at = djtz.now()
        run.save()
        logger.exception(
            "event=bama_fetch_failed run_id=%s mode=%s page=%d error=%s duration_s=%.1f",
            run.pk, mode, resume_page, exc, time.monotonic() - started)
        reset_cache()
        raise
    finally:
        run.finished_at = djtz.now()
        run.save()
        reset_cache()

    run.affected_model_ids = affected_model_ids  # type: ignore[attr-defined]
    logger.info(
        "event=bama_fetch_complete run_id=%s mode=%s status=%s stop_reason=%s pages=%d "
        "fetched=%d created=%d updated=%d rejected=%d price_changes=%d duration_s=%.1f",
        run.pk, mode, run.status, run.stop_reason, run.pages_fetched, run.fetched_count,
        run.created_count, run.updated_count, run.skipped_count, run.price_change_count,
        time.monotonic() - started,
    )
    return run
