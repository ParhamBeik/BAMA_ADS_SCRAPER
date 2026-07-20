"""Integration tests for the per-ad ingest pipeline (needs the DB)."""

from datetime import datetime, timezone

import pytest

from apps.core.models import Ad
from apps.core.models import FetchRun
from apps.jobs.services.dimensions import reset_cache
from apps.jobs.services.ingest import ingest_ad, reset_price_cache
from apps.core.models import PriceObservation
from apps.parsing import extract_ad, parse_publish_time


def make_payload(code, price, phrase="2 ساعت پیش", brand="پژو", model="405", trim="دنده‌ای"):
    return {
        "detail": {
            "code": code,
            "title": f"{brand}، {model}",
            "brand_fa": brand,
            "trim": trim,
            "year": "1399",
            "mileage": "120,000",
            "type": "car",
            "time": phrase,
            "url": f"https://bama.ir/cad/{code}",
            "location": "تهران - مرکز",
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


@pytest.fixture(autouse=True)
def _clear_caches():
    reset_cache()
    reset_price_cache()
    yield
    reset_cache()
    reset_price_cache()


@pytest.mark.django_db
def test_ingest_creates_ad_version_and_price():
    run = FetchRun.objects.create(source=FetchRun.Source.BULK_IMPORT)
    observed = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    extracted = extract_ad(make_payload("abc1", 1_000_000_000), observed)
    publish_at = parse_publish_time(extracted["publish_phrase"], observed)

    ad, created, price_changed = ingest_ad(
        extracted, run=run, observed_at=observed, publish_at=publish_at
    )

    assert created and price_changed
    assert ad.current_price == 1_000_000_000
    assert ad.first_seen_at == observed
    assert Ad.objects.count() == 1
    assert ad.versions.count() == 1
    assert PriceObservation.objects.count() == 1


@pytest.mark.django_db
def test_ingest_is_idempotent_unchanged_price():
    run = FetchRun.objects.create(source=FetchRun.Source.BULK_IMPORT)
    observed = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    payload = make_payload("abc2", 1_000_000_000)
    extracted = extract_ad(payload, observed)
    publish_at = parse_publish_time(extracted["publish_phrase"], observed)

    ingest_ad(extracted, run=run, observed_at=observed, publish_at=publish_at)
    # Re-ingest the identical payload under a new run.
    run2 = FetchRun.objects.create(source=FetchRun.Source.BULK_IMPORT)
    reset_price_cache()
    _, created, price_changed = ingest_ad(
        extracted, run=run2, observed_at=observed, publish_at=publish_at
    )

    assert created is False  # snapshot upserted, not created
    assert price_changed is False  # change-only price dedup
    assert Ad.objects.count() == 1
    assert PriceObservation.objects.count() == 1


@pytest.mark.django_db
def test_ingest_records_price_change():
    observed = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)

    run1 = FetchRun.objects.create(source=FetchRun.Source.BULK_IMPORT)
    e1 = extract_ad(make_payload("abc3", 1_000_000_000), observed)
    ingest_ad(e1, run=run1, observed_at=observed, publish_at=parse_publish_time(e1["publish_phrase"], observed))

    run2 = FetchRun.objects.create(source=FetchRun.Source.BULK_IMPORT)
    later = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    reset_price_cache()
    e2 = extract_ad(make_payload("abc3", 1_200_000_000), later)
    _, created, price_changed = ingest_ad(
        e2, run=run2, observed_at=later, publish_at=parse_publish_time(e2["publish_phrase"], later)
    )

    assert created is False
    assert price_changed is True
    assert Ad.objects.count() == 1
    prices = list(PriceObservation.objects.values_list("price", flat=True).order_by("observed_at"))
    assert prices == [1_000_000_000, 1_200_000_000]
