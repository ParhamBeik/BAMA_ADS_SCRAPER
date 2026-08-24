"""Matched-cohort market index.

Unit tests: the index is pure arithmetic over ``DailyInventorySnapshot`` rows —
no network, no HTTP, no derived-service chain — so the cheapest test that can
fail when the logic breaks constructs those rows directly and reads the series
back. The one integration-level case (the API endpoint) is at the bottom, since
serialization and the scope/param contract cross a real boundary.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import (
    Brand,
    DailyInventorySnapshot,
    MarketIndex,
    Model,
    Variant,
)
from apps.core.research import (
    BASE_VALUE,
    MIN_COHORT_ADS,
    build_index,
    cohort_series,
    compute_index,
)

D0 = date(2026, 8, 1)
D1 = D0 + timedelta(days=1)


@pytest.fixture
def cohorts(db):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو")
    model = Model.objects.create(brand=brand, name_fa="پژو ۲۰۶")
    cheap = Variant.objects.create(model=model, name_fa="تیپ ۱")
    dear = Variant.objects.create(model=model, name_fa="تیپ ۵")
    return {"brand": brand, "model": model, "cheap": cheap, "dear": dear}


def _snap(model, variant, on, *, price, count, year=1399):
    return DailyInventorySnapshot.objects.create(
        model=model, variant=variant, year_jalali=year, date=on,
        ad_count=count, new_count=0, median_price=price,
        mean_price=price, min_price=price, max_price=price,
    )


# ---------------------------------------------------------------------------
# The property the whole feature exists for
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_index_ignores_composition_shift(cohorts):
    """A flood of cheap listings must move the median but NOT the index.

    Both cohorts hold their price exactly. Only the *mix* changes: the cheap
    cohort triples in size. A naive median over all listings falls hard; the
    index, which only ever compares a cohort against itself, must stay flat.
    """
    model, cheap, dear = cohorts["model"], cohorts["cheap"], cohorts["dear"]

    # Day 0: 10 cheap cars at 500M, 10 expensive at 1500M.
    _snap(model, cheap, D0, price=500_000_000, count=10)
    _snap(model, dear, D0, price=1_500_000_000, count=10)
    # Day 1: identical prices, but 30 cheap cars now vs the same 10 expensive.
    _snap(model, cheap, D1, price=500_000_000, count=30)
    _snap(model, dear, D1, price=1_500_000_000, count=10)

    series = compute_index(cohort_series(MarketIndex.Scope.MARKET))
    assert len(series) == 2
    assert series[0]["index_value"] == BASE_VALUE
    # No cohort changed price, so the market did not move.
    assert series[1]["return_pct"] == pytest.approx(0.0, abs=1e-6)
    assert series[1]["index_value"] == pytest.approx(BASE_VALUE, abs=1e-6)

    # ...while the ad-count-weighted median across all listings drops sharply,
    # which is exactly the misleading number the index replaces.
    def naive_median(rows):
        prices = []
        for price, count in rows:
            prices.extend([price] * count)
        return statistics.median(prices)

    before = naive_median([(500_000_000, 10), (1_500_000_000, 10)])
    after = naive_median([(500_000_000, 30), (1_500_000_000, 10)])
    assert after < before


@pytest.mark.django_db
def test_index_tracks_genuine_price_move(cohorts):
    """When cohorts actually reprice, the index must follow — size-weighted."""
    model, cheap, dear = cohorts["model"], cohorts["cheap"], cohorts["dear"]

    _snap(model, cheap, D0, price=500_000_000, count=30)
    _snap(model, dear, D0, price=1_000_000_000, count=10)
    # Cheap cohort +10%, expensive flat. Weights 30 vs 10 → +7.5% overall.
    _snap(model, cheap, D1, price=550_000_000, count=30)
    _snap(model, dear, D1, price=1_000_000_000, count=10)

    series = compute_index(cohort_series(MarketIndex.Scope.MARKET))
    assert series[1]["return_pct"] == pytest.approx(7.5, abs=1e-6)
    assert series[1]["index_value"] == pytest.approx(107.5, abs=1e-6)
    assert series[1]["cohort_count"] == 2


@pytest.mark.django_db
def test_index_skips_cohorts_below_min_ads(cohorts):
    """A cohort too thin to have a meaningful median contributes no return."""
    model, cheap, dear = cohorts["model"], cohorts["cheap"], cohorts["dear"]

    thin = MIN_COHORT_ADS - 1
    _snap(model, cheap, D0, price=500_000_000, count=thin)
    _snap(model, dear, D0, price=1_000_000_000, count=10)
    # The thin cohort halves in price; it must be ignored entirely.
    _snap(model, cheap, D1, price=250_000_000, count=thin)
    _snap(model, dear, D1, price=1_000_000_000, count=10)

    series = compute_index(cohort_series(MarketIndex.Scope.MARKET))
    assert series[1]["cohort_count"] == 1  # only the fat cohort counted
    assert series[1]["return_pct"] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.django_db
def test_index_clips_absurd_cohort_moves(cohorts):
    """A single cohort cannot drag the headline with an implausible jump."""
    model, cheap = cohorts["model"], cohorts["cheap"]

    _snap(model, cheap, D0, price=100_000_000, count=10)
    _snap(model, cheap, D1, price=1_000_000_000, count=10)  # 10x overnight

    series = compute_index(cohort_series(MarketIndex.Scope.MARKET))
    # Clipped to MAX_COHORT_RETURN (+50%), not +900%.
    assert series[1]["return_pct"] == pytest.approx(50.0, abs=1e-6)


@pytest.mark.django_db
def test_new_cohort_contributes_no_return(cohorts):
    """A cohort that did not exist yesterday changes weights, never the index."""
    model, cheap, dear = cohorts["model"], cohorts["cheap"], cohorts["dear"]

    _snap(model, cheap, D0, price=500_000_000, count=10)
    _snap(model, cheap, D1, price=500_000_000, count=10)
    # Brand-new cohort, wildly different price level, appears on day 1 only.
    _snap(model, dear, D1, price=9_000_000_000, count=50)

    series = compute_index(cohort_series(MarketIndex.Scope.MARKET))
    assert series[1]["cohort_count"] == 1
    assert series[1]["return_pct"] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.django_db
def test_index_carries_level_when_nothing_matches(cohorts):
    """No cohort on both dates → hold the level, do not invent a move."""
    model, cheap, dear = cohorts["model"], cohorts["cheap"], cohorts["dear"]

    _snap(model, cheap, D0, price=500_000_000, count=10)
    _snap(model, dear, D1, price=900_000_000, count=10)

    series = compute_index(cohort_series(MarketIndex.Scope.MARKET))
    assert series[1]["return_pct"] is None
    assert series[1]["index_value"] == pytest.approx(BASE_VALUE)


@pytest.mark.django_db
def test_build_index_is_idempotent(cohorts):
    """Rebuilds replace, never accumulate — the series is chained, so a stale
    suffix would corrupt every later value."""
    model, cheap = cohorts["model"], cohorts["cheap"]
    _snap(model, cheap, D0, price=500_000_000, count=10)
    _snap(model, cheap, D1, price=550_000_000, count=10)

    first = build_index(MarketIndex.Scope.MARKET, None)
    second = build_index(MarketIndex.Scope.MARKET, None)
    assert first == second == 2
    assert MarketIndex.objects.filter(scope=MarketIndex.Scope.MARKET).count() == 2


@pytest.mark.django_db
def test_brand_scope_isolates_its_own_cohorts(cohorts):
    """A brand series must not be moved by another brand's prices."""
    model, cheap = cohorts["model"], cohorts["cheap"]
    other_brand = Brand.objects.create(slug="pride", name_fa="پراید")
    other_model = Model.objects.create(brand=other_brand, name_fa="پراید ۱۳۱")
    other_variant = Variant.objects.create(model=other_model, name_fa="ساده")

    _snap(model, cheap, D0, price=500_000_000, count=10)
    _snap(model, cheap, D1, price=500_000_000, count=10)      # peugeot flat
    _snap(other_model, other_variant, D0, price=200_000_000, count=10)
    _snap(other_model, other_variant, D1, price=240_000_000, count=10)  # +20%

    build_index(MarketIndex.Scope.BRAND, "peugeot")
    build_index(MarketIndex.Scope.BRAND, "pride")

    peugeot = MarketIndex.objects.get(
        scope=MarketIndex.Scope.BRAND, scope_id="peugeot", date=D1
    )
    pride = MarketIndex.objects.get(
        scope=MarketIndex.Scope.BRAND, scope_id="pride", date=D1
    )
    assert peugeot.return_pct == pytest.approx(0.0, abs=1e-6)
    assert pride.return_pct == pytest.approx(20.0, abs=1e-6)


@pytest.mark.django_db
def test_market_index_endpoint(cohorts):
    """Integration: the endpoint's scope contract and change_pct summary."""
    model, cheap = cohorts["model"], cohorts["cheap"]
    _snap(model, cheap, D0, price=500_000_000, count=10)
    _snap(model, cheap, D1, price=550_000_000, count=10)
    build_index(MarketIndex.Scope.MARKET, None)

    client = APIClient()
    url = reverse("core:market-index")

    res = client.get(url)
    assert res.status_code == 200
    body = res.json()
    assert body["scope"] == "market"
    assert len(body["series"]) == 2
    assert body["latest_index"] == pytest.approx(110.0, abs=1e-6)
    assert body["change_pct"] == pytest.approx(10.0, abs=1e-6)
    assert body["window"]["requested_days"] == 90
    assert body["window"]["days"] == 2
    assert body["window"]["clamped"] is True

    # A non-market scope without ?id is a client error, not an empty series.
    assert client.get(url, {"scope": "brand"}).status_code == 400
    assert client.get(url, {"scope": "nonsense"}).status_code == 400
