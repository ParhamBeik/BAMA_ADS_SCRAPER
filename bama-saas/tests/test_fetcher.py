"""Talking to bama.ir.

Pagination and rank bookkeeping, delta stopping, retry/backoff behaviour, the
crawl gate that refuses to probe an active block, and the interval arithmetic
that turns "which pages did we read" into "what did we miss".

Mostly unit level against a stubbed session: the subject is the loop's decisions,
and a real HTTP call would test bama.ir rather than this code. The coverage and
gate tests need the DB, because both derive their answer from stored rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from django.utils import timezone as djtz

from apps.core.models import AdObservation, FetchRun, PageCoverage
from apps.core.pricing import refresh_cohort_deal_scores
from apps.jobs import fetcher
from apps.jobs import fetcher as F
from apps.jobs import fetcher as crawl_gate
from apps.jobs.fetcher import (
    PAGE_SIZE,
    CrawlBlocked,
    fetch_live,
    find_gaps,
    plan_backfill,
)
from tests.conftest import gallery


def make_ad(code: str, rank: int, price: int = 450_000_000) -> dict:
    return {
        "images": gallery(code),
        "detail": {
            "code": code,
            "rank": rank,
            "title": "پژو، 405",
            "brand_fa": "پژو",
            "year": "1399",
            "mileage": "120,000",
            "type": "car",
            "time": "2 ساعت پیش",
            "url": f"https://bama.ir/cad/{code}",
            "location": "تهران",
            "transmission": "دنده‌ای",
        },
        "price": {
            "price": str(price),
            "type": "lumpsum",
            "payment": "0",
            "prepayment": "0",
            "installments": "0",
        },
    }


def make_feed(n_pages: int, prefix: str, page_size: int = PAGE_SIZE) -> list[list[dict]]:
    """``n_pages`` pages of ads with the feed's real rank arithmetic."""
    # Codes must look like real Bama codes (^[a-z0-9]{6,12}$) or verification
    # hard-rejects every ad and the fetcher has nothing left to assert on.
    return [
        [
            make_ad(
                f"{prefix.lower()}{page * page_size + i:05d}",
                rank=page * page_size + i + 1,
            )
            for i in range(page_size)
        ]
        for page in range(n_pages)
    ]


class FakeResponse:
    def __init__(self, ads, status_code=200):
        self.status_code = status_code
        self._ads = ads

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {self.status_code}", response=self
            )

    def json(self):
        return {"data": {"ads": self._ads}}


def _page_index(url: str) -> int:
    """The requested page, parsed properly rather than off the end of the URL.

    The feed request carries the photo/price filters after ``pageIndex``, so
    splitting on the last ``=`` reads ``0&image=1&priced=1``.
    """
    return int(parse_qs(urlparse(url).query)["pageIndex"][0])


class FakeSession:
    """Records every requested URL; serves ``pages`` by 0-based index."""

    def __init__(self, pages, fail_on=None, fail_status=500):
        self.pages = pages
        self.fail_on = fail_on
        self.fail_status = fail_status
        self.urls: list[str] = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        index = _page_index(url)
        if self.fail_on is not None and index == self.fail_on:
            return FakeResponse([], status_code=self.fail_status)
        ads = self.pages[index] if 0 <= index < len(self.pages) else []
        return FakeResponse(ads)

    @property
    def page_indices(self) -> list[int]:
        return [_page_index(u) for u in self.urls]


def run_with(session, **kwargs) -> FetchRun:
    """Call fetch_live against a fake session, with sleeps stubbed out."""
    with patch.object(fetcher, "create_session", return_value=session), \
         patch.object(fetcher, "warmup"), \
         patch.object(fetcher.time, "sleep"):
        return fetch_live(page_pause=0.0, **kwargs)


@pytest.mark.django_db
def test_first_request_is_page_index_zero():
    """The regression this whole file exists for: pageIndex is 0-based."""
    session = FakeSession(make_feed(3, "A"))
    run_with(session, mode="full")

    assert _page_index(session.urls[0]) == 0


@pytest.mark.django_db
def test_every_feed_request_asks_for_photos_and_prices():
    """The population this app collects is photo-and-price ads only.

    These filters used to sit on the warm-up URL alone, which only sets cookies,
    so every sweep paged the unfiltered feed and pulled in ads with no photo.
    """
    session = FakeSession(make_feed(3, "A"))
    run_with(session, mode="full")

    assert session.urls
    for url in session.urls:
        query = parse_qs(urlparse(url).query)
        assert query["image"] == ["1"], url
        assert query["priced"] == ["1"], url


def test_403_is_logged_once_without_retry(caplog):
    """A rejected session must be visible and must not hammer the upstream."""
    session = FakeSession([], fail_on=0, fail_status=403)

    with pytest.raises(requests.HTTPError):
        fetcher.fetch_page_with_backoff(session, 0, request_timeout=5)

    assert session.page_indices == [0]
    assert "event=bama_http_error page=0 status=403 retryable=False" in caplog.text
    assert "event=bama_fetch_failed page=0 attempt=1 status=403 retryable=False" in caplog.text


@pytest.mark.django_db
def test_pages_are_contiguous_with_no_gap():
    session = FakeSession(make_feed(4, "B"))
    run_with(session, mode="full")

    # 4 real pages, plus the empty page that proves the end of the feed —
    # requested twice, because an empty page is always confirmed before it is
    # believed (a throttled API returns 200 + [] exactly like a real end).
    assert session.page_indices == [0, 1, 2, 3, 4, 4]


@pytest.mark.django_db
def test_full_mode_reaches_end_of_feed():
    session = FakeSession(make_feed(3, "C"))
    run = run_with(session, mode="full")

    assert run.status == FetchRun.Status.SUCCEEDED
    assert run.reached_end is True
    assert run.stop_reason == FetchRun.StopReason.END_OF_FEED
    assert run.pages_fetched == 3
    assert run.deepest_rank == 3 * PAGE_SIZE
    assert run.created_count == 3 * PAGE_SIZE
    assert run.resume_from_page is None


@pytest.mark.django_db
def test_page_coverage_matches_pages_read():
    session = FakeSession(make_feed(3, "D"))
    run = run_with(session, mode="full")

    rows = list(
        PageCoverage.objects.filter(fetch_run=run)
        .order_by("page_index")
        .values_list("page_index", "rank_lo", "rank_hi", "ad_count", "new_count")
    )
    assert rows == [
        (0, 1, 30, 30, 30),
        (1, 31, 60, 30, 30),
        (2, 61, 90, 30, 30),
    ]
    # The empty end-of-feed page is not a covered page.
    assert PageCoverage.objects.filter(fetch_run=run, page_index=3).count() == 0


@pytest.mark.django_db
def test_ad_observation_rank_is_populated_from_the_feed():
    session = FakeSession(make_feed(2, "E"))
    run = run_with(session, mode="full")

    ranks = sorted(
        AdObservation.objects.filter(fetch_run=run).values_list("rank", flat=True)
    )
    assert ranks == list(range(1, 2 * PAGE_SIZE + 1))


@pytest.mark.django_db
def test_rank_falls_back_to_page_arithmetic_when_absent():
    pages = make_feed(1, "F")
    for ad in pages[0]:
        ad["detail"].pop("rank")
    session = FakeSession(pages)
    run = run_with(session, mode="full")

    assert run.deepest_rank == PAGE_SIZE
    cov = PageCoverage.objects.get(fetch_run=run, page_index=0)
    assert (cov.rank_lo, cov.rank_hi) == (1, PAGE_SIZE)


def test_invalid_rank_falls_back_to_page_arithmetic():
    ad = make_ad("invalidrank", rank=9999)
    assert fetcher.rank_of(ad, page=0, offset=1) == 1


@pytest.mark.django_db
def test_duplicate_page_ranks_use_page_arithmetic_for_coverage():
    page = make_feed(1, "R")[0]
    page[1]["detail"]["rank"] = page[0]["detail"]["rank"]
    session = FakeSession([page])
    run = run_with(session, mode="backfill", start_page=0, end_page=0)

    coverage = PageCoverage.objects.get(fetch_run=run)
    assert (coverage.rank_lo, coverage.rank_hi) == (1, PAGE_SIZE)


@pytest.mark.django_db
def test_delta_stops_after_k_stale_pages():
    """Pages 1+ repeat page 0's ads: nothing created, no price change."""
    pages = make_feed(1, "G")
    feed = [pages[0]] + [
        [
            make_ad(ad["detail"]["code"], rank=PAGE_SIZE * page + i + 1)
            for i, ad in enumerate(pages[0])
        ]
        for page in range(1, 6)
    ]
    session = FakeSession(feed)
    run = run_with(session, mode="delta", max_stale_pages=2)

    assert run.stop_reason == FetchRun.StopReason.STALE_PAGES
    assert run.reached_end is False
    # page 0 fresh, pages 1 and 2 stale -> stop without touching page 3.
    assert run.pages_fetched == 3
    assert session.page_indices == [0, 1, 2]
    assert run.resume_from_page is None


@pytest.mark.django_db
def test_delta_keeps_going_while_pages_yield_new_ads():
    session = FakeSession(make_feed(3, "H"))
    run = run_with(session, mode="delta", max_stale_pages=2)

    assert run.stop_reason == FetchRun.StopReason.END_OF_FEED
    assert run.pages_fetched == 3


@pytest.mark.django_db
def test_error_on_page_three_checkpoints_resume_from_page():
    session = FakeSession(make_feed(6, "I"), fail_on=3, fail_status=500)

    with pytest.raises(requests.HTTPError):
        run_with(session, mode="full")

    run = FetchRun.objects.filter(source=FetchRun.Source.LIVE_FETCH).first()
    assert run.status == FetchRun.Status.FAILED
    assert run.stop_reason == FetchRun.StopReason.ERROR
    assert run.resume_from_page == 3
    assert run.pages_fetched == 3
    # Pages 0-2 once each, then page 3 retried until the budget is spent.
    assert session.page_indices[:3] == [0, 1, 2]
    assert session.page_indices[3:] == [3] * (fetcher.MAX_RETRIES + 1)


@pytest.mark.django_db
def test_client_error_is_not_retried():
    session = FakeSession(make_feed(2, "J"), fail_on=1, fail_status=404)

    with pytest.raises(requests.HTTPError):
        run_with(session, mode="full")

    assert session.page_indices == [0, 1]


@pytest.mark.django_db
def test_next_run_resumes_from_the_checkpoint():
    failing = FakeSession(make_feed(6, "K"), fail_on=3, fail_status=500)
    with pytest.raises(requests.HTTPError):
        run_with(failing, mode="full")

    resumed = FakeSession(make_feed(6, "K"))
    run = run_with(resumed, mode="full")

    assert run.start_page == 3
    # Page 6 is the empty end-of-feed page, requested twice for confirmation.
    assert resumed.page_indices == [3, 4, 5, 6, 6]

    # The clean run buried the checkpoint: the run after it starts over at 0.
    after = FakeSession(make_feed(2, "K"))
    assert run_with(after, mode="full").start_page == 0
    assert after.page_indices[0] == 0


@pytest.mark.django_db
def test_delta_never_resumes_from_a_checkpoint():
    """A delta must always restart at page 0, even after an aborted delta.

    Resuming a delta at a checkpoint would skip pages 0..N-1 — the newest ads —
    which is the exact class of silent loss this module exists to prevent.
    Re-reading a few cheap top pages is the correct trade; the deep pages the
    aborted run missed are recovered by the full sweep and crawl_gaps.
    """
    failing = FakeSession(make_feed(8, "R"), fail_on=4, fail_status=500)
    with pytest.raises(requests.HTTPError):
        run_with(failing, mode="delta", max_stale_pages=99)

    resumed = FakeSession(make_feed(8, "R"))
    run = run_with(resumed, mode="delta", max_stale_pages=99)

    assert run.start_page == 0, "delta resumed from a checkpoint instead of page 0"
    assert resumed.page_indices[0] == 0


@pytest.mark.django_db
def test_backfill_fetches_only_the_requested_range():
    session = FakeSession(make_feed(10, "L"))
    run = run_with(session, mode="backfill", start_page=4, end_page=6)

    assert session.page_indices == [4, 5, 6]
    assert run.mode == FetchRun.Mode.BACKFILL
    assert run.start_page == 4
    assert run.stop_reason == FetchRun.StopReason.MAX_PAGES
    assert run.reached_end is False
    assert list(
        PageCoverage.objects.filter(fetch_run=run)
        .order_by("page_index")
        .values_list("page_index", "rank_lo", "rank_hi")
    ) == [(4, 121, 150), (5, 151, 180), (6, 181, 210)]


@pytest.mark.django_db
def test_max_ads_cap_stops_mid_feed():
    session = FakeSession(make_feed(5, "M"))
    run = run_with(session, mode="full", max_ads=45)

    assert run.stop_reason == FetchRun.StopReason.MAX_ADS
    assert run.fetched_count == 45
    assert session.page_indices == [0, 1]


def feed_ad(code, price, phrase="2 ساعت پیش", brand="پژو", model="405", rank=None):
    detail = {
        "code": code,
        "title": f"{brand}، {model}",
        "brand_fa": brand,
        "year": "1399",
        "mileage": "120,000",
        "type": "car",
        "time": phrase,
        "url": f"https://bama.ir/cad/{code}",
        "location": "تهران",
        "transmission": "دنده‌ای",
    }
    if rank is not None:
        detail["rank"] = rank
    return {
        "images": gallery(code),
        "detail": detail,
        "price": {
            "price": str(price),
            "type": "lumpsum",
            "payment": "0",
            "prepayment": "0",
            "installments": "0",
        },
    }


def run_delta(pages, **kwargs):
    """Drive fetch_live over a canned page sequence.

    Past the end of ``pages`` the feed keeps answering empty, which is what a
    real exhausted feed does — and what lets the fetcher issue its confirming
    re-request for an empty page without the stub running dry.
    """
    remaining = list(pages)

    def next_page(session, page, request_timeout):
        return remaining.pop(0) if remaining else []

    with patch.object(fetcher, "create_session"), \
         patch.object(fetcher, "warmup"), \
         patch.object(fetcher.time, "sleep"), \
         patch.object(fetcher, "fetch_page") as mock_fetch_page:
        mock_fetch_page.side_effect = next_page
        return fetch_live(page_pause=0.0, **kwargs)


@pytest.mark.django_db
def test_fetch_live_delta_mode_stale_pages_early_stopping():
    """Repeating the same ad makes every page after the first stale."""
    page = [feed_ad("100001", 450000000, rank=1)]

    run = run_delta([page, page, page, []], mode="delta", max_stale_pages=2)

    assert run.status == FetchRun.Status.SUCCEEDED
    assert run.stop_reason == FetchRun.StopReason.STALE_PAGES
    assert run.mode == FetchRun.Mode.DELTA
    assert run.reached_end is False
    assert run.pages_fetched == 3
    assert run.created_count == 1
    assert run.fetched_count == 3
    assert run.resume_from_page is None
    assert hasattr(run, "affected_model_ids")


@pytest.mark.django_db
def test_delta_that_runs_out_of_feed_reports_end_of_feed():
    pages = [
        [feed_ad("200001", 450000000, rank=1)],
        [feed_ad("200002", 460000000, rank=31)],
        [],
    ]
    run = run_delta(pages, mode="delta", max_stale_pages=2)

    assert run.stop_reason == FetchRun.StopReason.END_OF_FEED
    assert run.reached_end is False
    assert run.pages_fetched == 2


@pytest.mark.django_db
def test_backfill_empty_page_is_recorded_but_not_believed():
    """An unbelievable empty page is still evidence; it just is not proof yet."""
    run = run_delta([[]], mode="backfill", start_page=20, end_page=20)

    assert run.stop_reason == FetchRun.StopReason.END_UNCONFIRMED
    assert run.reached_end is False
    # Refetched, not the in-memory object: `end_is_corroborated` reads this back
    # out of Postgres on a *later* run, so an unsaved field would be a silent
    # no-op that an in-memory assertion happily passes.
    assert FetchRun.objects.get(pk=run.pk).feed_end_rank == 30 * 20


@pytest.mark.django_db
def test_delta_writes_page_coverage_from_observed_ranks():
    pages = [
        [feed_ad("300001", 450000000, rank=7)],
        [feed_ad("300002", 460000000, rank=44)],
        [],
    ]
    run = run_delta(pages, mode="delta", max_stale_pages=2)

    assert list(
        PageCoverage.objects.filter(fetch_run=run)
        .order_by("page_index")
        .values_list("page_index", "rank_lo", "rank_hi", "new_count")
    ) == [(0, 7, 7, 1), (1, 44, 44, 1)]
    assert run.deepest_rank == 44


@pytest.mark.django_db
def test_price_change_keeps_a_page_fresh():
    """A page with no new ads but a price change is not stale."""
    first = [feed_ad("400001", 450000000, rank=1)]
    repriced = [feed_ad("400001", 430000000, rank=31)]

    run = run_delta(
        [first, repriced, repriced, repriced, []], mode="delta", max_stale_pages=2
    )

    assert run.price_change_count == 2
    assert run.stop_reason == FetchRun.StopReason.STALE_PAGES
    # pages 0 and 1 fresh, pages 2 and 3 stale.
    assert run.pages_fetched == 4


@pytest.mark.django_db
def test_refresh_cohort_deal_scores():
    """Verify refresh_cohort_deal_scores runs without error on empty or valid model sets."""
    res = refresh_cohort_deal_scores({1, 2, None})
    assert "refreshed_models" in res
    assert "total_scored" in res
    assert res["refreshed_models"] == 2


@pytest.mark.django_db
def test_the_turnover_scan_happens_once_per_pass_not_once_per_model():
    """The cost of the hot tick's incremental rescore.

    Turnover is a scan of every clean episode joined to its ad and does not vary
    with the model being rescored, so the answer is identical for all of them.
    Measuring it inside the per-model loop ran that same scan once for each of
    the ~200 models a fetch touches — every 15 minutes, and again on each
    coverage tick.
    """
    with patch("apps.core.pricing._turnover_rates", return_value={}) as turnover:
        refresh_cohort_deal_scores({1, 2, 3, 4, 5})

    assert turnover.call_count == 1


@pytest.mark.django_db
def test_an_empty_rescore_does_no_work_at_all():
    """Nothing to rescore must not still pay for the episode scan."""
    with patch("apps.core.pricing._turnover_rates", return_value={}) as turnover:
        res = refresh_cohort_deal_scores({None})

    assert turnover.call_count == 0
    assert res["refreshed_models"] == 0


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


NOW = djtz.now


def make_run(*, blocked=False, failed=False, pages=0, ago_minutes=1, mode="delta",
             source=FetchRun.Source.LIVE_FETCH):
    at = NOW() - timedelta(minutes=ago_minutes)
    if blocked:
        status, reason = FetchRun.Status.FAILED, FetchRun.StopReason.BLOCKED
    elif failed:
        status, reason = FetchRun.Status.FAILED, FetchRun.StopReason.ERROR
    else:
        status, reason = FetchRun.Status.SUCCEEDED, FetchRun.StopReason.STALE_PAGES
    run = FetchRun.objects.create(
        source=source, mode=mode, status=status,
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
def test_a_blocked_sold_probe_counts_toward_the_shared_cooldown():
    make_run(blocked=True, source=FetchRun.Source.SOLD_PROBE, ago_minutes=1)

    assert crawl_gate.consecutive_blocks() == 1


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


def _run():
    return FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH,
        status=FetchRun.Status.SUCCEEDED,
        mode=FetchRun.Mode.DELTA,
    )


def _end_unconfirmed(rank, *, started_at=None):
    """A run that saw the feed end at ``rank`` and was not believed."""
    return FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH,
        status=FetchRun.Status.SUCCEEDED,
        mode=FetchRun.Mode.BACKFILL,
        stop_reason=FetchRun.StopReason.END_UNCONFIRMED,
        started_at=started_at or djtz.now(),
        feed_end_rank=rank,
    )


def _cover(run, pages, *, page_size=30, fetched_at=None):
    """Write one PageCoverage row per page index in ``pages``."""
    at = fetched_at or djtz.now()
    for page in pages:
        PageCoverage.objects.create(
            fetch_run=run,
            page_index=page,
            rank_lo=page_size * page + 1,
            rank_hi=page_size * (page + 1),
            ad_count=page_size,
            fetched_at=at,
        )


@pytest.mark.django_db
def test_no_gap_when_pages_are_contiguous():
    _cover(_run(), range(0, 5))
    assert find_gaps() == []


@pytest.mark.django_db
def test_single_hole_is_found_and_planned():
    # Pages 0,1 and 4,5 read; pages 2 and 3 (ranks 61..120) never were.
    _cover(_run(), [0, 1, 4, 5])
    assert find_gaps() == [(61, 120)]
    assert plan_backfill(find_gaps()) == [(2, 3)]


@pytest.mark.django_db
def test_leading_hole_when_page_zero_was_skipped():
    """The 0-based-pageIndex bug in raw form: page 0 never read."""
    _cover(_run(), [1, 2, 3])
    assert find_gaps() == [(1, 30)]
    assert plan_backfill(find_gaps()) == [(0, 0)]


@pytest.mark.django_db
def test_a_sliver_of_a_gap_does_not_switch_removal_detection_off():
    """Zero uncovered ranks was a bar the live feed can never hold.

    The feed is strictly recency-ordered and shifts continuously, so two pages
    read seconds apart can leave a handful of ranks neither one claimed. One
    such hole — six ranks against a ~28,700-rank feed — switched removal
    detection off entirely, and production flip-flopped between "986 ads marked
    REMOVED" and "cannot prove an ad is gone" inside sixteen minutes.

    The gap is still *reported*, so the coverage job still closes it; what
    changed is only whether it invalidates the window.
    """
    from apps.jobs.fetcher import COVERAGE_GAP_TOLERANCE_RANKS, coverage_is_complete

    window = djtz.now() - timedelta(hours=24)
    run = _run()
    _cover(run, [0, 1, 2, 3])
    # Six ranks in the middle that no page claimed.
    PageCoverage.objects.filter(fetch_run=run, page_index=1).update(rank_lo=37)

    gaps = find_gaps()
    assert gaps == [(31, 36)]                     # still visible to gap repair
    assert sum(hi - lo + 1 for lo, hi in gaps) <= COVERAGE_GAP_TOLERANCE_RANKS
    assert coverage_is_complete(since=window) is True

    # A real multi-page hole is still exactly what the check exists to catch.
    PageCoverage.objects.filter(fetch_run=run, page_index__in=[1, 2]).delete()
    assert sum(hi - lo + 1 for lo, hi in find_gaps()) > COVERAGE_GAP_TOLERANCE_RANKS
    assert coverage_is_complete(since=window) is False


@pytest.mark.django_db
def test_two_holes_stay_separate():
    _cover(_run(), [0, 2, 4])
    assert find_gaps() == [(31, 60), (91, 120)]
    assert plan_backfill(find_gaps()) == [(1, 1), (3, 3)]


@pytest.mark.django_db
def test_adjacent_gaps_merge_into_one_page_range():
    """Two rank holes landing on neighbouring pages collapse to one refetch."""
    run = _run()
    _cover(run, [0])
    # Hand-rolled partial coverage: ranks 31..40 and 51..60 seen, 41..50 and
    # 61..90 missing -> two rank gaps that are adjacent once mapped to pages.
    PageCoverage.objects.create(
        fetch_run=run, page_index=1, rank_lo=31, rank_hi=40, ad_count=10,
        fetched_at=djtz.now(),
    )
    PageCoverage.objects.create(
        fetch_run=run, page_index=3, rank_lo=91, rank_hi=120, ad_count=30,
        fetched_at=djtz.now(),
    )
    assert find_gaps() == [(41, 90)]
    # ranks 41..90 span pages 1 and 2 -> a single contiguous refetch range.
    assert plan_backfill(find_gaps()) == [(1, 2)]

    # Separate gaps mapping onto adjacent pages merge too.
    assert plan_backfill([(31, 40), (61, 70)]) == [(1, 2)]


@pytest.mark.django_db
def test_since_window_excludes_stale_coverage():
    old = _run()
    _cover(old, [0, 1, 2], fetched_at=djtz.now() - timedelta(hours=48))
    fresh = _run()
    _cover(fresh, [0, 2])

    since = djtz.now() - timedelta(hours=24)
    assert find_gaps(since=since) == [(31, 60)]
    # Without the window everything is covered.
    assert find_gaps() == []


@pytest.mark.django_db
def test_max_rank_demands_tail_coverage():
    _cover(_run(), [0, 1])
    assert find_gaps() == []
    assert find_gaps(max_rank=150) == [(61, 150)]


@pytest.mark.django_db
def test_empty_coverage():
    assert find_gaps() == []
    assert find_gaps(max_rank=60) == [(1, 60)]
    assert plan_backfill([]) == []



# --- the depth ratchet, and what is allowed to lower it -------------------------
#
# Integration level against a stubbed session, because the bug being pinned here
# only exists in the handoff between three units: the fetch loop decides whether
# an empty page is the end of the feed, `known_feed_depth` decides which runs it
# will believe, and `find_gaps` turns that ceiling into work. Each was correct
# alone. Together they disabled removal detection for over a day.


@pytest.mark.django_db
def test_a_deep_backfill_that_walks_off_the_feed_lowers_the_ceiling():
    """The production incident, reproduced.

    The ratchet is a one-way high-water mark, but the feed shrinks as ads are
    deleted. Only a `mode=FULL` run used to be allowed to lower it, and rolling
    coverage retired the full sweep entirely — so in one week production logged
    624 backfills that reached the real end of the feed, 0 full sweeps, and a
    ceiling stuck ~60 ranks above anything that still existed.
    """
    # A deeper feed, observed three days ago: inside the 30-day depth window, so
    # it still sets the ceiling, but outside the 24h coverage window.
    _cover(_run(), [4], fetched_at=djtz.now() - timedelta(days=3))
    assert F.known_feed_depth() == 150

    # The feed now ends after page 3. A bounded backfill walks into the empty page.
    run = run_with(FakeSession(make_feed(3, "R")), mode="backfill",
                   start_page=3, end_page=5)

    assert run.reached_end is True
    assert run.stop_reason == FetchRun.StopReason.END_OF_FEED
    # An empty page at index 3 says no ad holds a rank above 30 * 3. Recorded
    # as feed_end_rank, not deepest_rank: this run observed no ads at all.
    assert run.feed_end_rank == 90
    assert run.deepest_rank is None
    assert F.known_feed_depth() == 90


@pytest.mark.django_db
def test_a_shallow_empty_page_never_lowers_the_ceiling():
    """A delta that runs out of new ads is not evidence about the whole feed.

    This is the guard that makes the change above safe: believing every empty
    page would let one throttled response retire most of the market.
    """
    _cover(_run(), [4], fetched_at=djtz.now() - timedelta(days=3))

    # One page of ads, then empty — far too shallow against a ceiling of page 4.
    run = run_with(FakeSession(make_feed(1, "S")), mode="delta")

    assert run.reached_end is False
    assert F.known_feed_depth() == 150


@pytest.mark.django_db
def test_retiring_the_phantom_tail_completes_coverage():
    """Why any of this matters: `mark_inactive` refuses to act without this.

    Ranks 91..150 belong to ads that no longer exist, so no fetch could ever
    cover them and `coverage_is_complete` stayed False forever.
    """
    stale = djtz.now() - timedelta(days=3)
    _cover(_run(), [4], fetched_at=stale)
    _cover(_run(), [0, 1, 2], fetched_at=djtz.now())
    window = djtz.now() - timedelta(hours=F.COVERAGE_WINDOW_HOURS)

    assert F.find_gaps(since=window, max_rank=F.known_feed_depth()) == [(91, 150)]
    assert F.coverage_is_complete(since=window) is False

    run_with(FakeSession(make_feed(3, "T")), mode="backfill", start_page=3, end_page=5)

    assert F.known_feed_depth() == 90
    assert F.find_gaps(since=window, max_rank=F.known_feed_depth()) == []
    assert F.coverage_is_complete(since=window) is True


@pytest.mark.django_db
def test_a_backfill_range_that_simply_ends_claims_nothing():
    """Reaching `end_page` is not the same as reaching the end of the feed."""
    _cover(_run(), [4], fetched_at=djtz.now() - timedelta(days=3))

    run = run_with(FakeSession(make_feed(6, "U")), mode="backfill",
                   start_page=1, end_page=2)

    assert run.reached_end is False
    assert run.stop_reason == FetchRun.StopReason.MAX_PAGES
    assert F.known_feed_depth() == 150


def test_a_bounded_run_is_held_to_a_tighter_bar_than_a_full_sweep():
    """The 266-page incident, in its new costume.

    A full sweep walked everything above it and earns the generous bar. A
    backfill starting mid-feed proves only that its own range ended, which is
    indistinguishable from a degraded API — so a blip at page 700 of a
    1133-page feed must not be believed, even though it clears 50%.
    """
    assert F.end_of_feed_is_credible(700, 1133) is True                   # full
    assert F.end_of_feed_is_credible(700, 1133, bounded=True) is False    # backfill
    # A backfill that genuinely walks off the end lands next to the ceiling;
    # production saw 1157 against 1159.
    assert F.end_of_feed_is_credible(1157, 1159, bounded=True) is True
    # A real but large shrink is still allowed: 5% of the ceiling.
    assert F.end_of_feed_is_credible(1105, 1159, bounded=True) is True
    # A tiny feed must not be judged on a proportion alone — one page of
    # shrinkage is 25% of a four-page feed and entirely normal.
    assert F.end_of_feed_is_credible(3, 4, bounded=True) is True


@pytest.mark.django_db
def test_a_spurious_mid_feed_empty_page_cannot_collapse_the_ceiling():
    """The failure this guards: a wrongly-lowered ceiling stops demanding
    coverage of the deep feed, and mark_inactive is rank-blind — it would
    retire live ads nobody had re-verified."""
    _cover(_run(), [40], fetched_at=djtz.now() - timedelta(days=3))
    assert F.known_feed_depth() == 1230

    # Feed "ends" at page 20 — half the ceiling, and a lie.
    run = run_with(FakeSession(make_feed(20, "V")), mode="backfill",
                   start_page=20, end_page=25)

    assert run.reached_end is False
    assert F.known_feed_depth() == 1230
    # The reading is *recorded* — one disagreement is evidence, not proof — but
    # `reached_end` stays False, so the ratchet does not see it.
    assert run.feed_end_rank == 600
    assert run.stop_reason == FetchRun.StopReason.END_UNCONFIRMED


@pytest.mark.django_db
def test_one_disagreeing_run_is_not_enough_to_lower_the_ceiling():
    _cover(_run(), [40], fetched_at=djtz.now() - timedelta(days=3))
    _end_unconfirmed(600)

    assert F.end_is_corroborated(20) is False


@pytest.mark.django_db
def test_repeated_agreement_lowers_the_ceiling():
    """2026-08-25: the feed really did shrink 17% in one step and every run
    said so, but each was judged alone and disbelieved forever."""
    _cover(_run(), [40], fetched_at=djtz.now() - timedelta(days=3))
    assert F.known_feed_depth() == 1230
    assert F.end_of_feed_is_credible(20, 40, bounded=True) is False

    # Three *real* runs against a feed that truly ends at page 20, so the whole
    # loop is exercised: each run must persist its own disbelieved observation
    # for the next one to be able to count it.
    def sweep():
        return run_with(FakeSession(make_feed(20, "W")), mode="backfill",
                        start_page=20, end_page=25)

    first, second = sweep(), sweep()
    assert [r.stop_reason for r in (first, second)] == [
        FetchRun.StopReason.END_UNCONFIRMED] * 2
    assert F.known_feed_depth() == 1230, "two runs must not be enough"

    third = sweep()
    assert third.reached_end is True
    assert third.stop_reason == FetchRun.StopReason.END_OF_FEED
    assert F.known_feed_depth() == 600


@pytest.mark.django_db
def test_runs_ending_at_different_places_do_not_corroborate_each_other():
    """Agreement means the same rank, not merely repeated failure."""
    _cover(_run(), [40], fetched_at=djtz.now() - timedelta(days=3))
    _end_unconfirmed(600)
    _end_unconfirmed(900)      # a different page: no agreement

    assert F.end_is_corroborated(20) is False


@pytest.mark.django_db
def test_stale_agreement_expires():
    """A feed that shrank yesterday and recovered must not still be believed."""
    _cover(_run(), [40], fetched_at=djtz.now() - timedelta(days=3))
    old = djtz.now() - timedelta(hours=F.END_AGREEMENT_WINDOW_HOURS + 1)
    _end_unconfirmed(600, started_at=old)
    _end_unconfirmed(600, started_at=old)

    assert F.end_is_corroborated(20) is False


def test_a_410_detail_page_is_sold():
    """Measured: Bama answers 410 with this alt text for a sold listing."""
    assert F.detail_says_sold(410, 'alt="این آگهی فروخته شد!"')
    assert F.detail_says_sold(410, "")
    assert F.detail_says_sold(200, "این آگهی فروخته شد!")
    assert not F.detail_says_sold(200, "چری آریزو 5T IE فروشی امروز چهارشنبه")


def test_403_on_a_detail_page_is_not_retried():
    """A WAF block on a sold probe must trip the gate, not hammer the listing."""
    class DetailSession:
        def __init__(self):
            self.n = 0

        def get(self, url, timeout=None, headers=None):
            self.n += 1
            resp = requests.Response()
            resp.status_code = 403
            return resp

    session = DetailSession()
    with pytest.raises(requests.HTTPError):
        F.fetch_ad_page_with_backoff(session, "https://bama.ir/cad/x", 5)
    assert session.n == 1
