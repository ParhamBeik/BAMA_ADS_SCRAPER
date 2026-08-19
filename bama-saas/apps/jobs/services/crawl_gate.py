"""When not to talk to bama.ir.

One guard, learned from the 2026-08-16 incident. bama.ir's CDN (Sotoon) started
answering every request — API *and* plain HTML — with a branded 403 block page,
«دسترسی امکان‌پذیر نیست». ``fetcher._retryable`` correctly refuses to retry a 403
inside a run, but nothing stopped the *scheduler* from launching a fresh run every
10–15 minutes, so the stack fired 485 requests into an active ban over six hours.
Retrying into a WAF block cannot succeed and plausibly renews the ban, which is
the worst of both.

This module used to also enforce an hourly page budget, on the theory that a
286-page burst shortly before the first 403 had triggered it. That theory was
wrong and the budget is gone. The block is on our egress IP: every path on
bama.ir answered 403 from a datacenter/VPN exit in London (AS202422), while
divar.ir and google.com answered 200 from the same host, and the ban never
expired the way a rate-limit ban does. Throttling a request rate that was never
the problem only slowed the crawl down, so the crawl rate is back to what it was
and the fix belongs at the network layer (an Iranian egress), not here.

The breaker needs no new state. Every run already persists its outcome to
``FetchRun``, so "how many blocks in a row" is a query — which also makes it
correct across a container restart, unlike a module-level counter. On a healthy
history it is completely inert: no blocks means no cooldown and no gating.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

import requests
from django.utils import timezone

from apps.core.models import FetchRun

logger = logging.getLogger("bama.worker")

# The status the CDN uses to refuse us. 429 is deliberately *not* here: that is
# ordinary rate limiting and `fetch_page_with_backoff` already waits it out
# inside the run, honouring Retry-After.
WAF_STATUS = 403

# First cooldown is one pipeline tick, so a single blocked run costs one skipped
# fetch and nothing more. Then it doubles: 15m, 30m, 1h, 2h, 4h, 6h.
BASE_COOLDOWN = timedelta(seconds=int(os.environ.get("BAMA_BLOCK_COOLDOWN", 900)))
MAX_COOLDOWN = timedelta(seconds=int(os.environ.get("BAMA_BLOCK_COOLDOWN_MAX", 21600)))

# Never stop probing entirely. The ban lifts on bama.ir's schedule, not ours, and
# a breaker that latches open needs a human to notice — which is exactly the
# failure mode that let this run unattended for six hours.
MAX_BACKOFF_DOUBLINGS = 8

# How far back to look for consecutive blocks. Longer than the max cooldown so
# the streak survives its own quiet period.
STREAK_LOOKBACK = timedelta(hours=48)


class CrawlBlocked(RuntimeError):
    """Do not fetch right now. Callers must treat this as a skip, not a failure.

    Recording it as a failure would light up ``failed_runs`` on the health page
    for a cooldown the system chose on purpose, burying the real signal.
    """


def is_waf_block(exc: BaseException) -> bool:
    """True when this exception is the CDN refusing us outright."""
    if not isinstance(exc, requests.HTTPError):
        return False
    return getattr(exc.response, "status_code", None) == WAF_STATUS


def _last_runs(limit: int = 40):
    """Most recent live-fetch outcomes, newest first."""
    return list(
        FetchRun.objects.filter(
            source=FetchRun.Source.LIVE_FETCH,
            created_at__gte=timezone.now() - STREAK_LOOKBACK,
        )
        .order_by("-created_at")
        .values("status", "stop_reason", "finished_at", "started_at", "created_at")[:limit]
    )


def consecutive_blocks() -> int:
    """How many runs in a row ended blocked, counting back from the newest.

    Counts across modes: a blocked delta and a blocked backfill are the same ban,
    and treating them separately would let two schedules each probe at full rate.
    """
    streak = 0
    for run in _last_runs():
        if run["stop_reason"] == FetchRun.StopReason.BLOCKED:
            streak += 1
            continue
        # A RUNNING row is the in-flight run asking this question. Ignore it
        # rather than letting it break its own streak.
        if run["status"] == FetchRun.Status.RUNNING:
            continue
        break
    return streak


def cooldown_until():
    """When the breaker reopens, or ``None`` if fetching is allowed now."""
    streak = consecutive_blocks()
    if streak == 0:
        return None
    last = next(
        (
            r for r in _last_runs()
            if r["stop_reason"] == FetchRun.StopReason.BLOCKED
        ),
        None,
    )
    if last is None:
        return None
    at = last["finished_at"] or last["started_at"] or last["created_at"]
    doublings = min(streak - 1, MAX_BACKOFF_DOUBLINGS)
    return at + min(BASE_COOLDOWN * (2 ** doublings), MAX_COOLDOWN)


def check() -> None:
    """Raise :class:`CrawlBlocked` if we must not fetch; otherwise return.

    Returning means "fetch whatever you were going to fetch, at whatever rate you
    were going to fetch it". This gate caps nothing on a healthy history.
    """
    until = cooldown_until()
    if until is not None and timezone.now() < until:
        streak = consecutive_blocks()
        remaining = (until - timezone.now()).total_seconds()
        logger.warning(
            "event=bama_crawl_gated reason=waf_block consecutive=%d "
            "cooldown_remaining_s=%.0f until=%s",
            streak,
            remaining,
            until.isoformat(),
        )
        raise CrawlBlocked(
            f"bama.ir returned {WAF_STATUS} on {streak} consecutive run(s); "
            f"next attempt in {remaining / 60:.0f} min (until {until.isoformat()})"
        )
