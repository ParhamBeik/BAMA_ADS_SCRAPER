"""Pagination / coverage tests at the HTTP boundary.

INTEGRATION tests: they mock ``session.get`` rather than ``fetch_page``, so the
real URL construction, banner filtering, retry loop and page walk all execute.
Mocking ``fetch_page`` is exactly why the previous suite could not see that
``pageIndex`` is 0-based and the fetcher started at 1 — the off-by-one lived in
the code being stubbed out.
"""

from unittest.mock import patch

import pytest
import requests

from apps.core.models import AdObservation, FetchRun, PageCoverage
from apps.jobs import fetcher
from apps.jobs.fetcher import PAGE_SIZE, fetch_live
from apps.jobs.ingest import reset_price_cache


@pytest.fixture(autouse=True)
def _clean_price_cache():
    """ingest_ad's price fingerprint cache is module-global and outlives a test."""
    reset_price_cache()
    yield
    reset_price_cache()


def make_ad(code: str, rank: int, price: int = 450_000_000) -> dict:
    return {
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


class FakeSession:
    """Records every requested URL; serves ``pages`` by 0-based index."""

    def __init__(self, pages, fail_on=None, fail_status=500):
        self.pages = pages
        self.fail_on = fail_on
        self.fail_status = fail_status
        self.urls: list[str] = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        index = int(url.rsplit("pageIndex=", 1)[1])
        if self.fail_on is not None and index == self.fail_on:
            return FakeResponse([], status_code=self.fail_status)
        ads = self.pages[index] if 0 <= index < len(self.pages) else []
        return FakeResponse(ads)

    @property
    def page_indices(self) -> list[int]:
        return [int(u.rsplit("pageIndex=", 1)[1]) for u in self.urls]


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

    assert session.urls[0].endswith("pageIndex=0")
    assert "pageIndex=1" not in session.urls[0]


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
