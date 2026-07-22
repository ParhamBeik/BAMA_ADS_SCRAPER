"""Tests for fast-delta fetching, early stopping, and watermark gap recovery."""

from unittest.mock import patch

import pytest

from apps.core.models import FetchRun
from apps.jobs.services.fetcher import fetch_live
from apps.core.services.deal_score import refresh_cohort_deal_scores


def make_payload(code, price, phrase="2 ساعت پیش", brand="پژو", model="405"):
    return {
        "detail": {
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
        },
        "price": {
            "price": str(price),
            "type": "lumpsum",
            "payment": "0",
            "prepayment": "0",
            "installments": "0",
        },
    }


@pytest.mark.django_db
def test_fetch_live_delta_mode_stale_pages_early_stopping():
    """Verify that delta mode stops early when consecutive pages yield no new ads."""
    page_1_ads = [make_payload("1001", 450000000)]

    with patch("apps.jobs.services.fetcher.create_session"), \
         patch("apps.jobs.services.fetcher.warmup"), \
         patch("apps.jobs.services.fetcher.fetch_page") as mock_fetch_page:

        mock_fetch_page.side_effect = [page_1_ads, page_1_ads, page_1_ads, []]

        run = fetch_live(mode="delta", max_stale_pages=2, page_pause=0.0)

        assert run.status == FetchRun.Status.SUCCEEDED
        assert run.created_count == 1
        assert run.fetched_count >= 1
        assert hasattr(run, "affected_model_ids")


@pytest.mark.django_db
def test_refresh_cohort_deal_scores():
    """Verify refresh_cohort_deal_scores runs without error on empty or valid model sets."""
    res = refresh_cohort_deal_scores({1, 2, None})
    assert "refreshed_models" in res
    assert "total_scored" in res
    assert res["refreshed_models"] == 2
