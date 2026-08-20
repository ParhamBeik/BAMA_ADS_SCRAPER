"""The guard that keeps us from arguing with bama.ir's CDN.

Integration over stored ``FetchRun`` rows, because the breaker is deliberately
derived from run history rather than from in-process counters — that is what
makes it survive a container restart, and an in-memory test would not exercise
the property that matters.

Regression suite for 2026-08-16: the CDN began answering every request with a
403 block page, and because nothing tracked the streak *across* runs the stack
fired 485 requests into an active ban over six hours while two schedules probed
independently.

The breaker briefly shipped alongside an hourly page budget, aimed at a
request-rate trigger that turned out not to exist — the block was on our egress
IP. The budget is gone, and ``test_a_healthy_history_does_not_cap_the_range``
below is what keeps it gone: this gate must never slow a crawl that is working.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import requests
from django.utils import timezone

from apps.core.models import FetchRun
from apps.jobs import fetcher as crawl_gate
from apps.jobs.fetcher import CrawlBlocked

NOW = timezone.now


def make_run(*, blocked=False, failed=False, pages=0, ago_minutes=1, mode="delta"):
    at = NOW() - timedelta(minutes=ago_minutes)
    if blocked:
        status, reason = FetchRun.Status.FAILED, FetchRun.StopReason.BLOCKED
    elif failed:
        status, reason = FetchRun.Status.FAILED, FetchRun.StopReason.ERROR
    else:
        status, reason = FetchRun.Status.SUCCEEDED, FetchRun.StopReason.STALE_PAGES
    run = FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH, mode=mode, status=status,
        stop_reason=reason, pages_fetched=pages,
    )
    # created_at is auto_now_add; started/finished drive the cooldown clock.
    FetchRun.objects.filter(pk=run.pk).update(
        created_at=at, started_at=at, finished_at=at
    )
    return run


def http_error(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"{status} error", response=resp)


# --- classifying the refusal ---------------------------------------------------

def test_403_is_a_waf_block_and_429_is_not():
    """429 is ordinary rate limiting; `fetch_page_with_backoff` waits it out
    inside the run honouring Retry-After, so it must not trip the breaker."""
    assert crawl_gate.is_waf_block(http_error(403))
    assert not crawl_gate.is_waf_block(http_error(429))
    assert not crawl_gate.is_waf_block(http_error(503))
    assert not crawl_gate.is_waf_block(ValueError("bad json"))


# --- the circuit breaker -------------------------------------------------------

@pytest.mark.django_db
def test_a_clean_history_does_not_gate():
    make_run(pages=5, ago_minutes=5)
    assert crawl_gate.consecutive_blocks() == 0
    assert crawl_gate.cooldown_until() is None
    assert crawl_gate.check_gate() is None  # returns without raising


@pytest.mark.django_db
def test_one_block_costs_one_tick_then_reopens():
    """The first cooldown is a single pipeline tick: cheap probe, no latching."""
    make_run(blocked=True, ago_minutes=1)
    with pytest.raises(CrawlBlocked):
        crawl_gate.check_gate()

    # Same single block, but long enough ago that the cooldown has expired.
    FetchRun.objects.all().delete()
    make_run(blocked=True, ago_minutes=20)
    assert crawl_gate.check_gate() is None


@pytest.mark.django_db
def test_the_cooldown_doubles_with_the_streak():
    for i in range(3):
        make_run(blocked=True, ago_minutes=3 - i)
    assert crawl_gate.consecutive_blocks() == 3
    # 3 blocks -> 2 doublings -> 4x base (60 min), measured from the newest.
    until = crawl_gate.cooldown_until()
    assert until is not None
    minutes = (until - NOW()).total_seconds() / 60
    assert 55 < minutes < 65


@pytest.mark.django_db
def test_the_cooldown_is_capped():
    for i in range(30):
        make_run(blocked=True, ago_minutes=30 - i)
    until = crawl_gate.cooldown_until()
    hours = (until - NOW()).total_seconds() / 3600
    assert hours <= crawl_gate.MAX_COOLDOWN.total_seconds() / 3600 + 0.1
    # And it never latches shut permanently.
    assert hours > 0


@pytest.mark.django_db
def test_a_success_clears_the_streak():
    """Recovery must be immediate. A breaker that stays warm after the ban lifts
    keeps the catalog frozen for no reason."""
    make_run(blocked=True, ago_minutes=10)
    make_run(blocked=True, ago_minutes=9)
    make_run(pages=3, ago_minutes=1)

    assert crawl_gate.consecutive_blocks() == 0
    assert crawl_gate.check_gate() is None


@pytest.mark.django_db
def test_blocks_are_counted_across_modes():
    """Delta and backfill hit the same CDN. Counting them separately would let
    each schedule probe at full rate, which is what actually happened."""
    make_run(blocked=True, mode="delta", ago_minutes=2)
    make_run(blocked=True, mode="backfill", ago_minutes=1)
    assert crawl_gate.consecutive_blocks() == 2


@pytest.mark.django_db
def test_an_ordinary_failure_does_not_trip_the_breaker():
    """A parser bug should be retried on the next tick, not cooled down for hours."""
    make_run(failed=True, ago_minutes=1)
    assert crawl_gate.consecutive_blocks() == 0
    assert crawl_gate.check_gate() is None


# --- the gate at the fetch boundary --------------------------------------------

@pytest.mark.django_db
def test_a_gated_fetch_raises_before_touching_the_network(monkeypatch):
    """`fetch_live` must not reach `_fetch_live` while the breaker is open —
    otherwise it writes another FAILED row and another blocked request."""
    from apps.jobs import fetcher

    make_run(blocked=True, ago_minutes=1)
    called = []
    monkeypatch.setattr(fetcher, "_fetch_live", lambda **kw: called.append(kw))

    with pytest.raises(CrawlBlocked):
        fetcher.fetch_live(mode="delta")
    assert called == []


@pytest.mark.django_db
def test_a_healthy_history_does_not_cap_the_range(monkeypatch):
    """The gate must hand the requested range through untouched.

    It once trimmed `end_page` to an hourly page budget, which quietly halved
    coverage chunks in exchange for nothing: the 403 was on our egress IP, not on
    our request rate. Whatever the caller asked for is what gets fetched.
    """
    from apps.jobs import fetcher

    make_run(pages=280, ago_minutes=5)   # a heavy hour is not a reason to slow down
    seen = {}
    monkeypatch.setattr(fetcher, "_fetch_live", lambda **kw: seen.update(kw))

    fetcher.fetch_live(mode="backfill", start_page=100, end_page=199)

    assert seen["start_page"] == 100
    assert seen["end_page"] == 199
