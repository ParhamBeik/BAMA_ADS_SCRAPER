"""Integration tests for the Bama SaaS rollout audit fixes.

This integration test fits the audit fixes because it exercises the database boundary and components
(ingestion logic, health check view, metrics calculations) to verify correct end-to-end integration and response formats.
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from django.utils import timezone as django_timezone

from apps.core.models import Ad, Brand, Model, Variant, City, Dealer, DealScoreCache, DailyInventorySnapshot, FetchRun
from apps.jobs.services.ingest import ingest_ad
from apps.jobs.services.verify import Rejection
from apps.core.services.deal_score import compute_deal_scores
from apps.core.services.metrics import inventory_trend
from apps.parsing import extract_ad

from tests.test_importer import _ing, _ing3


@pytest.mark.django_db
def test_genuine_db_health_check(client):
    """The /api/db/health/ endpoint must genuinely query the DB and return success."""
    url = reverse("db-health")
    response = client.get(url)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_ingest_hard_reject_preserves_ad_history():
    """Ingesting a payload that fails a hard rule on an existing ad must preserve the ad and flag it."""
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو")
    model = Model.objects.create(brand=brand, name_fa="پژو ۲۰۶")
    variant = Variant.objects.create(model=model, name_fa="تیپ ۵")
    city = City.objects.create(name_fa="تهران")

    observed = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)

    # 1. Create a clean ad using standard Bama payload structure
    payload = {
        "price": {
            "type": "lumpsum",
            "price": "1,000,000,000",
            "prepayment": "0",
        },
        "detail": {
            "code": "preserve123",
            "year": "1399",
            "mileage": "20,000",
            "url": "/detail-preserve123",
            "title": "پژو، ۲۰۶ تیپ ۵",
            "brand_fa": "پژو",
            "location": "تهران",
            "trim": "تیپ ۵",
        },
    }
    extracted = extract_ad(payload, observed)
    assert extracted is not None
    ad, created, _ = _ing3(extracted, run=run, observed_at=observed, publish_at=observed)
    assert created
    assert Ad.objects.filter(code="preserve123").exists()

    # 2. Ingest an update that triggers a hard rule (price too low)
    payload_bad = {
        "price": {
            "type": "lumpsum",
            "price": "5,000,000",  # below 10M floor
            "prepayment": "0",
        },
        "detail": {
            "code": "preserve123",
            "year": "1399",
            "mileage": "20,000",
            "url": "/detail-preserve123",
            "title": "پژو، ۲۰۶ تیپ ۵",
            "brand_fa": "پژو",
            "location": "تهران",
            "trim": "تیپ ۵",
        },
    }
    extracted_bad = extract_ad(payload_bad, observed)
    assert extracted_bad is not None

    ad2, created2, _ = _ing3(extracted_bad, run=run, observed_at=observed, publish_at=observed)
    assert ad2 is None
    assert not created2

    # The ad must still exist in the DB (no CASCADE deletion!) and have the flag set
    ad_stored = Ad.objects.get(code="preserve123")
    assert "price_too_low" in ad_stored.quality_flags


@pytest.mark.django_db
def test_deal_scoring_segmentation_by_cohort():
    """Deal scores must be computed relative to (model, variant, year_jalali) cohorts."""
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو")
    model = Model.objects.create(brand=brand, name_fa="پژو ۲۰۶")
    variant1 = Variant.objects.create(model=model, name_fa="تیپ ۵")
    variant2 = Variant.objects.create(model=model, name_fa="تیپ ۲")
    city = City.objects.create(name_fa="تهران")

    # Cohort 1: (model, variant1, 1399) - 3 ads
    for i in range(3):
        Ad.objects.create(
            code=f"c1_{i}", brand=brand, model=model, variant=variant1, city=city,
            year_jalali=1399, current_price=1_000_000_000, publish_at=django_timezone.now(),
            first_seen_at=django_timezone.now(), status=Ad.Status.ACTIVE
        )
    # Cohort 2: (model, variant2, 1399) - 3 ads
    for i in range(3):
        Ad.objects.create(
            code=f"c2_{i}", brand=brand, model=model, variant=variant2, city=city,
            year_jalali=1399, current_price=500_000_000, publish_at=django_timezone.now(),
            first_seen_at=django_timezone.now(), status=Ad.Status.ACTIVE
        )

    # An ad priced below cohort 1 median (1B) but above cohort 2 median (500M)
    cheap_c1 = Ad.objects.create(
        code="cheap_c1", brand=brand, model=model, variant=variant1, city=city,
        year_jalali=1399, current_price=800_000_000, publish_at=django_timezone.now(),
        first_seen_at=django_timezone.now(), status=Ad.Status.ACTIVE
    )

    res = compute_deal_scores(min_peers=3)
    assert res["scored"] == 1
    scored_ad = DealScoreCache.objects.get(ad_id="cheap_c1")
    assert scored_ad.peer_median == 1_000_000_000
    assert scored_ad.components["variant_id"] == variant1.id


@pytest.mark.django_db
def test_inventory_trend_uses_weighted_median():
    """inventory_trend must compute the true weighted median across slices."""
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو")
    model = Model.objects.create(brand=brand, name_fa="پژو ۲۰۶")
    v1 = Variant.objects.create(model=model, name_fa="تیپ ۵")
    v2 = Variant.objects.create(model=model, name_fa="تیپ ۲")
    v3 = Variant.objects.create(model=model, name_fa="تیپ ۱")

    # Mock daily inventory snapshots with different medians and weights
    today = django_timezone.now().date()
    DailyInventorySnapshot.objects.create(
        model_id=model.id, variant_id=v1.id, year_jalali=1399, date=today,
        ad_count=5, new_count=0, median_price=10_000_000,
        min_price=10_000_000, max_price=10_000_000, mean_price=10_000_000
    )
    DailyInventorySnapshot.objects.create(
        model_id=model.id, variant_id=v2.id, year_jalali=1399, date=today,
        ad_count=10, new_count=0, median_price=20_000_000,
        min_price=20_000_000, max_price=20_000_000, mean_price=20_000_000
    )
    DailyInventorySnapshot.objects.create(
        model_id=model.id, variant_id=v3.id, year_jalali=1399, date=today,
        ad_count=2, new_count=0, median_price=100_000_000,
        min_price=100_000_000, max_price=100_000_000, mean_price=100_000_000
    )

    trend = inventory_trend(model_id=model.id)
    # The true weighted median of [(10M, 5), (20M, 10), (100M, 2)] is 20,000,000.
    # The weighted mean would have been 26,470,588.
    assert trend["series"][0]["median_price"] == 20_000_000


from unittest.mock import patch
from django.contrib.auth import get_user_model
from apps.accounts.models import Notification
from apps.accounts.notifications import deliver

@pytest.mark.django_db
def test_smtp_failure_sets_failed_status():
    """If send_mail raises an exception, send_email must return False and deliver must mark it FAILED."""
    User = get_user_model()
    user = User.objects.create_user(email="test@example.com", password="testpassword")

    notification = Notification.objects.create(
        user=user,
        channel=Notification.Channel.EMAIL,
        subject="Test Subject",
        body="Test Body"
    )

    with patch("apps.accounts.notifications.send_mail", side_effect=Exception("SMTP Connection Refused")):
        deliver(notification)

    notification.refresh_from_db()
    assert notification.status == Notification.Status.FAILED
    assert notification.error == "transport returned failure"
