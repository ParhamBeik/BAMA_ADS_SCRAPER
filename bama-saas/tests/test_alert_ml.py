"""Alert rules can require the model's residual, not only the cohort discount.

Integration: matching and delivery read DealScoreCache plus AdPrediction together.
A unit test of the filter expression would not see the join, the UNDERPRICED
gate, or the snapshot copied onto AlertDelivery.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import AlertDelivery, AlertRule, User
from apps.core.models import Ad, Brand, City, DealScoreCache, Model, Variant
from apps.core.notify import deliver_alerts, matching_deals
from apps.ml.models import AdPrediction
from tests.conftest import UTC

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _ad(suffix: str, *, brand, model, variant, city, price: int) -> Ad:
    return Ad.objects.create(
        code=f"ml{suffix}",
        brand=brand,
        model=model,
        variant=variant,
        city=city,
        title=f"مدل تست {suffix}",
        year=1399,
        year_jalali=1399,
        year_gregorian=2020,
        year_calendar=Ad.YearCalendar.JALALI,
        mileage=80_000,
        current_price=price,
        publish_at=NOW - timedelta(days=1),
        last_seen_at=NOW,
        first_seen_at=NOW - timedelta(days=1),
    )


def _score(ad: Ad, *, discount: float) -> DealScoreCache:
    return DealScoreCache.objects.create(
        ad=ad,
        score=discount,
        discount_pct=discount,
        peer_median=ad.current_price + 100_000_000,
        components={"peer_count": 12},
        needs_review=False,
    )


@pytest.fixture
def board(db):
    brand = Brand.objects.create(slug="mlalert", name_fa="برند مدل")
    model = Model.objects.create(brand=brand, name_fa="مدل مدل")
    variant = Variant.objects.create(model=model, name_fa="دنده‌ای")
    city = City.objects.create(name_fa="تهران", province="تهران")
    cohort_only = _ad("cohort", brand=brand, model=model, variant=variant, city=city,
                      price=900_000_000)
    underpriced = _ad("under", brand=brand, model=model, variant=variant, city=city,
                      price=800_000_000)
    odd_record = _ad("odd", brand=brand, model=model, variant=variant, city=city,
                     price=700_000_000)
    _score(cohort_only, discount=18.0)
    _score(underpriced, discount=18.0)
    _score(odd_record, discount=18.0)
    AdPrediction.objects.create(
        ad=underpriced,
        residual_pct=22.0,
        anomaly_kind=AdPrediction.Anomaly.UNDERPRICED,
        price_p50=1_000_000_000,
    )
    AdPrediction.objects.create(
        ad=odd_record,
        residual_pct=40.0,
        anomaly_kind=AdPrediction.Anomaly.DATA,
        price_p50=1_200_000_000,
    )
    return {
        "cohort_only": cohort_only,
        "underpriced": underpriced,
        "odd_record": odd_record,
    }


@pytest.fixture
def member(db):
    client = APIClient()
    user = User.objects.create_user(email="ml-alert@example.com", password="StrongPass1!")
    client.force_authenticate(user)
    return client, user


@pytest.mark.django_db
def test_a_residual_rule_rejects_a_discount_that_the_model_did_not_confirm(board):
    codes = {row.ad_id for row in matching_deals(
        min_discount_pct=10.0, min_peers=8, min_residual_pct=15.0,
    )}
    assert board["underpriced"].code in codes
    assert board["cohort_only"].code not in codes
    assert board["odd_record"].code not in codes


@pytest.mark.django_db
def test_a_cohort_only_rule_still_matches_without_a_prediction(board):
    codes = {row.ad_id for row in matching_deals(min_discount_pct=10.0, min_peers=8)}
    assert board["cohort_only"].code in codes
    assert board["underpriced"].code in codes
    assert board["odd_record"].code in codes


@pytest.mark.django_db
def test_delivery_snapshots_the_residual_at_fire_time(member, board):
    _, user = member
    AlertRule.objects.create(
        user=user, min_discount_pct=10.0, min_peers=8, min_residual_pct=15.0,
    )
    deliver_alerts()
    delivery = AlertDelivery.objects.get(user=user, ad_id=board["underpriced"].code)
    assert delivery.residual_pct == pytest.approx(22.0)
    assert AlertDelivery.objects.filter(user=user).count() == 1


@pytest.mark.django_db
def test_the_rule_api_accepts_a_residual_floor_and_rejects_zero(member):
    client, user = member
    bad = client.post(
        "/api/alert-rules/",
        {"min_discount_pct": 12, "min_peers": 8, "min_residual_pct": 0},
        format="json",
    )
    assert bad.status_code == 400
    assert "min_residual_pct" in bad.json()

    ok = client.post(
        "/api/alert-rules/",
        {"min_discount_pct": 12, "min_peers": 8, "min_residual_pct": 15,
         "name": "مدل"},
        format="json",
    )
    assert ok.status_code == 201, ok.content
    assert ok.json()["min_residual_pct"] == 15
    assert AlertRule.objects.get(user=user).min_residual_pct == 15
