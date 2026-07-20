"""Unit tests for the analytics services (small ORM fixtures, no full dataset)."""

from datetime import datetime, timedelta, timezone

import pytest

from apps.core.models import Ad, Brand, Model, Variant
from apps.core.models import PriceObservation
from apps.core.services import insights
from apps.core.services.bollinger import bollinger
from apps.core.services.truemean import true_mean

UTC = timezone.utc


@pytest.fixture
def model():
    brand = Brand.objects.create(slug="testbrand", name_fa="برند تست")
    model = Model.objects.create(brand=brand, name_fa="مدل تست")
    Variant.objects.create(model=model, name_fa="default")
    return model


def _mk_ad(code, model, price, *, mileage=None, year=1399, days_ago=0):
    return Ad.objects.create(
        code=code, model=model, brand=model.brand,
        current_price=price, mileage=mileage, year=year,
        publish_at=datetime(2026, 7, 1, tzinfo=UTC) - timedelta(days=days_ago),
    )


@pytest.mark.django_db
def test_true_mean_trims_outlier(model):
    for i in range(10):
        _mk_ad(f"a{i}", model, 1_000_000_000 + i * 10_000_000)
    _mk_ad("out", model, 5_000_000_000)  # clear high outlier

    res = true_mean(model.id, method="zscore", z=2.0)
    assert res["count_before"] == 11
    assert res["count_after"] < res["count_before"]
    assert res["mean"] < 1_500_000_000  # not pulled up by the outlier
    assert 0 < res["cv"] < 1
    assert res["fair_min"] <= res["mean"] <= res["fair_max"]


@pytest.mark.django_db
def test_true_mean_percentile_method(model):
    for i in range(20):
        _mk_ad(f"p{i}", model, 1_000_000_000 + i * 5_000_000)
    res = true_mean(model.id, method="percentile")
    assert res["count_after"] <= res["count_before"]


@pytest.mark.django_db
def test_bollinger_band_ordering(model):
    ad = _mk_ad("b1", model, 1_000_000_000)
    base = datetime(2026, 6, 1, tzinfo=UTC)
    for day in range(8):
        PriceObservation.objects.create(
            ad=ad, observed_at=base + timedelta(days=day),
            price=1_000_000_000 + day * 20_000_000, fingerprint=f"fp{day}",
        )
    res = bollinger(model.id, window=4)
    assert len(res["series"]) == 8
    for point in res["series"]:
        assert point["upper"] >= point["mean"] >= point["lower"]


@pytest.mark.django_db
def test_market_depth_percentiles_ordered(model):
    for i in range(20):
        _mk_ad(f"d{i}", model, 1_000_000_000 + i * 50_000_000)
    res = insights.market_depth(model.id)
    assert res["count"] == 20
    assert res["p10"] <= res["p25"] <= res["median"] <= res["p75"] <= res["p90"]
    assert res["market_depth"] > 0


@pytest.mark.django_db
def test_undervalued_finds_below_median(model):
    for i in range(8):
        _mk_ad(f"u{i}", model, 1_000_000_000)
    _mk_ad("cheap", model, 800_000_000)  # 20% below median
    res = insights.undervalued(model.id, min_discount_pct=10.0)
    codes = [l["code"] for l in res["listings"]]
    assert "cheap" in codes
    assert res["listings"][0]["discount_pct"] >= 10.0


@pytest.mark.django_db
def test_depreciation_negative_slope(model):
    # Higher mileage → lower price.
    for i in range(8):
        _mk_ad(f"m{i}", model, 1_000_000_000 - i * 30_000_000, mileage=100_000 + i * 50_000)
    res = insights.depreciation(model.id)
    assert res["available"] is True
    assert res["slope_per_km"] < 0
    assert res["delta_per_10k_km"] < 0
