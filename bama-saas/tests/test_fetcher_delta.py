"""Delta-mode stop-reason contract and cohort deal-score refresh.

These stub ``fetch_page`` (page contents only) — deliberately one level above
``tests/test_fetcher_pagination.py``, which mocks ``session.get`` so it can see
the URLs and catch pageIndex bugs. Keep pagination assertions over there.
"""

from unittest.mock import patch

import pytest

from apps.core.models import FetchRun, PageCoverage
from apps.core.pricing import refresh_cohort_deal_scores
from apps.jobs import fetcher
from apps.jobs.fetcher import fetch_live
from apps.jobs.ingest import reset_price_cache


@pytest.fixture(autouse=True)
def _clean_price_cache():
    reset_price_cache()
    yield
    reset_price_cache()


def make_payload(code, price, phrase="2 ساعت پیش", brand="پژو", model="405", rank=None):
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
    page = [make_payload("100001", 450000000, rank=1)]

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
        [make_payload("200001", 450000000, rank=1)],
        [make_payload("200002", 460000000, rank=31)],
        [],
    ]
    run = run_delta(pages, mode="delta", max_stale_pages=2)

    assert run.stop_reason == FetchRun.StopReason.END_OF_FEED
    assert run.reached_end is False
    assert run.pages_fetched == 2


@pytest.mark.django_db
def test_backfill_empty_page_is_range_end_not_global_feed_end():
    run = run_delta([[]], mode="backfill", start_page=20, end_page=20)

    assert run.stop_reason == FetchRun.StopReason.MAX_PAGES
    assert run.reached_end is False


@pytest.mark.django_db
def test_delta_writes_page_coverage_from_observed_ranks():
    pages = [
        [make_payload("300001", 450000000, rank=7)],
        [make_payload("300002", 460000000, rank=44)],
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
    first = [make_payload("400001", 450000000, rank=1)]
    repriced = [make_payload("400001", 430000000, rank=31)]

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
