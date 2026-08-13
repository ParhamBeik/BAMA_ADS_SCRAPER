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


def _ing(*args, **kwargs):
    """``ingest_ad`` returning just the ad.

    ``ingest_ad`` returns an ``IngestResult`` rather than the old
    ``(ad, created, price_changed)`` tuple, because the delta fetcher needs to
    know whether a new *version* appeared and the cohort pass needs the affected
    cohort. These two shims keep the tests that only care about the old three
    facts readable.
    """
    return ingest_ad(*args, **kwargs).ad


def _ing3(*args, **kwargs):
    """``ingest_ad`` as the legacy ``(ad, created, price_changed)`` triple."""
    r = ingest_ad(*args, **kwargs)
    return r.ad, r.created, r.price_changed


@pytest.fixture(autouse=True)
def _clear_caches():
    reset_cache()
    reset_price_cache()
    yield
    reset_cache()
    reset_price_cache()


@pytest.fixture
def known_catalog(db):
    """The brand/model ``make_payload`` uses, already on record and confirmed.

    Ingestion flags any ad that brings a new Brand/Model into existence
    (``unknown_dimension``, see tests/test_catalog_guard.py). On an empty test
    database that is *every* ad, which would drown the assertions below that care
    about verification rules rather than catalog novelty.
    """
    from apps.core.models import Brand, Model

    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    Model.objects.create(brand=brand, name_fa="405", is_confirmed=True)
    reset_cache()
    return brand


@pytest.mark.django_db
def test_ingest_normalizes_year_and_zero_mileage(known_catalog):
    """The two corruption bugs, pinned at the one place every import path shares.

    Bama sends model years in either calendar (this ad is Gregorian 2025) and
    sends "صفر کیلومتر" for brand-new cars, which the old parse_int(positive=True)
    turned into NULL for ~33% of all ads.
    """
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    observed = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    payload = make_payload("zerokm1", 15_000_000_000)
    payload["detail"]["year"] = "2025"
    payload["detail"]["mileage"] = "صفر کیلومتر"
    extracted = extract_ad(payload, observed)

    ad = _ing(extracted, run=run, observed_at=observed, publish_at=observed)
    ad.refresh_from_db()

    assert (ad.year_jalali, ad.year_gregorian, ad.year_calendar) == (1404, 2025, "gregorian")
    assert ad.year == 2025, "the raw value stays untouched for provenance"
    assert ad.mileage == 0, "zero-km must be 0, never NULL"
    assert ad.canonical_path
    assert ad.quality_flags == []


@pytest.mark.django_db
def test_ingest_never_persists_a_hard_rejected_ad():
    """A lump-sum ad with no price is unusable and unrepairable, so it must not
    reach the Ad table at all — but the payload stays in IngestReject so the rule
    remains replayable if it turns out to be wrong."""
    from apps.core.models import IngestReject

    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    observed = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    payload = make_payload("badprice1", 0)  # lumpsum + price 0 -> hard failure
    extracted = extract_ad(payload, observed)

    ad, created, price_changed = _ing3(
        extracted, run=run, observed_at=observed, publish_at=observed
    )

    assert (ad, created, price_changed) == (None, False, False)
    assert not Ad.objects.filter(code="badprice1").exists()
    reject = IngestReject.objects.get(code="badprice1")
    assert reject.rule == "price_missing_for_lumpsum"
    assert reject.raw_payload, "the payload is retained so the rule can be replayed"


@pytest.mark.django_db
def test_ad_that_turns_bad_is_removed_not_left_stale():
    """The re-insertion trap: purging a bad row is pointless if the next fetch
    puts it back, and equally pointless if a row that WAS good and has since gone
    bad keeps its old clean values. Both must resolve to "not in the table"."""
    observed = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    run1 = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    good = extract_ad(make_payload("flip123", 1_000_000_000), observed)
    ad, created, _ = _ing3(good, run=run1, observed_at=observed, publish_at=observed)
    assert created and ad is not None

    run2 = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    reset_price_cache()
    bad = extract_ad(make_payload("flip123", 1_500_000), observed)  # below the floor
    ad2 = _ing(bad, run=run2, observed_at=observed, publish_at=observed)

    assert ad2 is None
    ad_stored = Ad.objects.get(code="flip123")
    assert "price_too_low" in ad_stored.quality_flags
    assert PriceObservation.objects.filter(ad__code="flip123").count() > 0


@pytest.mark.django_db
def test_negotiable_zero_price_is_not_quarantined(known_catalog):
    """21.6% of real ads are negotiable with price "0" — the single most
    important false positive to avoid."""
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    observed = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    payload = make_payload("nego123", 0)
    payload["price"]["type"] = "negotiable"
    extracted = extract_ad(payload, observed)

    ad = _ing(extracted, run=run, observed_at=observed, publish_at=observed)
    ad.refresh_from_db()

    assert ad.quality_flags == []


@pytest.mark.django_db
def test_ingest_creates_ad_version_and_price():
    run = FetchRun.objects.create(source=FetchRun.Source.BULK_IMPORT)
    observed = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    extracted = extract_ad(make_payload("abc001", 1_000_000_000), observed)
    publish_at = parse_publish_time(extracted["publish_phrase"], observed)

    ad, created, price_changed = _ing3(
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
    payload = make_payload("abc002", 1_000_000_000)
    extracted = extract_ad(payload, observed)
    publish_at = parse_publish_time(extracted["publish_phrase"], observed)

    ingest_ad(extracted, run=run, observed_at=observed, publish_at=publish_at)
    # Re-ingest the identical payload under a new run.
    run2 = FetchRun.objects.create(source=FetchRun.Source.BULK_IMPORT)
    reset_price_cache()
    _, created, price_changed = _ing3(
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
    e1 = extract_ad(make_payload("abc003", 1_000_000_000), observed)
    ingest_ad(e1, run=run1, observed_at=observed, publish_at=parse_publish_time(e1["publish_phrase"], observed))

    run2 = FetchRun.objects.create(source=FetchRun.Source.BULK_IMPORT)
    later = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    reset_price_cache()
    e2 = extract_ad(make_payload("abc003", 1_200_000_000), later)
    _, created, price_changed = _ing3(
        e2, run=run2, observed_at=later, publish_at=parse_publish_time(e2["publish_phrase"], later)
    )

    assert created is False
    assert price_changed is True
    assert Ad.objects.count() == 1
    prices = list(PriceObservation.objects.values_list("price", flat=True).order_by("observed_at"))
    assert prices == [1_000_000_000, 1_200_000_000]


# --- listing-presentation fields --------------------------------------------

@pytest.mark.django_db
def test_presentation_fields_are_promoted_from_the_payload(known_catalog):
    """These are the evidence behind an outlier explanation: "priced far under
    its cohort, one photo, a two-line description, unverified seller" is an
    answer; the price alone is only a number."""
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    observed = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    payload = make_payload("present1", 15_000_000_000)
    payload["detail"].update({
        "image_count": "7",
        "description": "x" * 120,
        "authenticated": True,
        "modified_date": "2026-05-13T12:30:58.32",
    })

    ad = _ing(extract_ad(payload, observed), run=run, observed_at=observed, publish_at=observed)
    ad.refresh_from_db()

    assert ad.image_count == 7
    assert ad.description_length == 120
    assert ad.seller_authenticated is True
    assert ad.source_modified_at is not None


@pytest.mark.django_db
def test_missing_presentation_fields_stay_null(known_catalog):
    """None means "not stated", which is a different fact from zero — an ad with
    no photos and an ad that did not report a photo count must stay
    distinguishable or every average over them is wrong."""
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    observed = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)

    ad = _ing(
        extract_ad(make_payload("present2", 15_000_000_000), observed),
        run=run, observed_at=observed, publish_at=observed,
    )
    ad.refresh_from_db()

    assert ad.image_count is None
    assert ad.description_length is None
    assert ad.seller_authenticated is None
    assert ad.source_modified_at is None


@pytest.mark.django_db
def test_a_malformed_timestamp_never_costs_us_the_ad(known_catalog):
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    observed = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    payload = make_payload("present3", 15_000_000_000)
    payload["detail"]["modified_date"] = "yesterday-ish"

    ad = _ing(extract_ad(payload, observed), run=run, observed_at=observed, publish_at=observed)

    assert ad is not None
    assert ad.source_modified_at is None


def test_source_timestamps_are_read_as_tehran_local():
    """Bama sends a bare local timestamp with no offset. Reading it as UTC would
    shift every value by Tehran's offset — an error that never surfaces because
    the result still looks like a plausible date."""
    from apps.jobs.services.ingest import parse_source_datetime

    parsed = parse_source_datetime("2026-05-13T12:30:58.32")

    assert parsed.tzinfo is not None
    assert parsed.hour != 12, "a bare local time must not be taken for UTC"
