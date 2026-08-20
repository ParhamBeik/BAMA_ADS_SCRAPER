"""Crawl politeness and end-of-feed sanity.

Test type: unit for the header/backoff arithmetic (pure functions over a fake
response) and integration for the end-of-feed rule, which reads FetchRun history.

The end-of-feed rule exists because of a recorded incident: a sweep stopped at
266 pages against a ~1100-page feed, stamped reached_end, and every downstream
consumer concluded that three quarters of the market had vanished.
"""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
import requests

from apps.core.models import FetchRun
from apps.jobs import fetcher as F


class _Resp:
    def __init__(self, headers=None):
        self.headers = headers or {}


def _http_error(headers=None):
    exc = requests.HTTPError("429")
    exc.response = _Resp(headers)
    return exc


# --- Retry-After ------------------------------------------------------------

def test_no_header_means_no_server_instruction():
    assert F.retry_after_seconds(_http_error()) is None


def test_delay_seconds_form():
    assert F.retry_after_seconds(_http_error({"Retry-After": "12"})) == 12


def test_http_date_form():
    when = datetime.now(timezone.utc) + timedelta(seconds=30)
    seconds = F.retry_after_seconds(_http_error({"Retry-After": format_datetime(when)}))
    assert 25 <= seconds <= 35


def test_a_hostile_value_is_capped():
    """The header is the server's own instruction and is honoured — but a
    misconfigured or malicious hour-long value must not park a sweep."""
    assert F.retry_after_seconds(_http_error({"Retry-After": "99999"})) == F.RETRY_AFTER_CAP


def test_garbage_is_ignored_rather_than_raised_on():
    assert F.retry_after_seconds(_http_error({"Retry-After": "soon-ish"})) is None


def test_a_past_date_is_ignored():
    when = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert F.retry_after_seconds(_http_error({"Retry-After": format_datetime(when)})) is None


def test_retry_after_overrides_the_exponential_guess():
    """Guessing a backoff curve against an explicit instruction is how a client
    earns a ban."""
    delay = F.backoff_delay(_http_error({"Retry-After": "20"}), attempt_delay=1.0)
    assert 20 <= delay <= 20 * (1 + F.BACKOFF_JITTER)


def test_jitter_is_applied_without_a_header():
    """Without jitter every client that backed off together retries at the same
    instant, giving a struggling server a synchronised second wave."""
    delays = {F.backoff_delay(_http_error(), attempt_delay=4.0) for _ in range(40)}
    assert len(delays) > 1
    assert all(4.0 <= d <= 4.0 * (1 + F.BACKOFF_JITTER) for d in delays)


# --- end-of-feed credibility ------------------------------------------------

def test_a_first_ever_sweep_is_believed():
    """Nothing to compare against; refusing here would mean never bootstrapping."""
    assert F.end_of_feed_is_credible(266, None) is True


def test_a_truncated_sweep_is_disbelieved():
    """The recorded incident: 266 pages against a feed known to be ~1100 deep."""
    assert F.end_of_feed_is_credible(266, 1100) is False


def test_a_full_depth_sweep_is_believed():
    assert F.end_of_feed_is_credible(1100, 1100) is True


def test_a_genuinely_shrinking_feed_is_still_believed():
    """The market really does shrink. The bar is deliberately generous — it is
    here to catch a truncated crawl, not to police market size."""
    assert F.end_of_feed_is_credible(700, 1100) is True


def test_a_resumed_sweep_is_judged_on_depth_not_pages_read():
    """A sweep resuming from a checkpoint at page 619 reads only a few hundred
    pages while still reaching the true bottom. Judging it on pages read would
    reject every resumed sweep."""
    assert F.end_of_feed_is_credible(1105, 1100) is True


@pytest.mark.django_db
def test_expected_depth_uses_the_last_completed_sweep_only():
    FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH, mode=FetchRun.Mode.FULL,
        status=FetchRun.Status.SUCCEEDED, reached_end=True, pages_fetched=1100,
        started_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    # A later run that failed part-way must not become the yardstick, or one bad
    # sweep would permanently lower the bar for the next one.
    FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH, mode=FetchRun.Mode.FULL,
        status=FetchRun.Status.FAILED, reached_end=False, pages_fetched=266,
        started_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )

    assert F.last_completed_sweep_depth() == 1100


@pytest.mark.django_db
def test_no_history_yields_no_expected_depth():
    assert F.last_completed_sweep_depth() is None
