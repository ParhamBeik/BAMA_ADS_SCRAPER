"""What the API is allowed to hand out, and to whom.

Test type: API/integration — the subject is the serialized HTTP response, which
is the only place these facts are observable.

Two separate concerns share this file because they share a cause: the read side
had drifted away from the write side. The scraped payload was being served to
anyone, and the catalog listed rows that every analytical query excluded, so the
same ad could be simultaneously visible and uncounted.
"""

from datetime import datetime, timezone

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import Ad, AdVersion, Brand, City, FetchRun, Model, Variant

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def catalog(db):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    model = Model.objects.create(brand=brand, name_fa="405", is_confirmed=True)
    variant = Variant.objects.create(model=model, name_fa="GLX")
    city = City.objects.create(name_fa="تهران")
    return {"brand": brand, "model": model, "variant": variant, "city": city}


def make_ad(catalog, code, **overrides):
    fields = dict(
        code=code,
        brand=catalog["brand"], model=catalog["model"],
        variant=catalog["variant"], city=catalog["city"],
        year_jalali=1399, mileage=100_000, current_price=500_000_000,
        status=Ad.Status.ACTIVE, first_seen_at=NOW, last_seen_at=NOW,
        publish_at=NOW,
        raw_payload={"detail": {"code": code, "dealer_phone": "0912..."}},
    )
    fields.update(overrides)
    return Ad.objects.create(**fields)


# --- the scraped payload is not public -------------------------------------

@pytest.mark.django_db
def test_ad_list_does_not_leak_the_raw_payload(catalog):
    make_ad(catalog, "leak0001")
    body = APIClient().get("/api/ads/").json()

    assert body["results"], "fixture should be listed"
    assert "raw_payload" not in body["results"][0]


@pytest.mark.django_db
def test_ad_detail_does_not_leak_the_raw_payload(catalog):
    make_ad(catalog, "leak0002")
    body = APIClient().get("/api/ads/leak0002/").json()

    assert body["code"] == "leak0002"
    assert "raw_payload" not in body


@pytest.mark.django_db
def test_version_timeline_does_not_leak_payloads(catalog):
    ad = make_ad(catalog, "leak0003")
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    AdVersion.objects.create(
        ad=ad, semantic_hash="a" * 8, raw_hash="b" * 8,
        payload={"detail": {"secret": 1}}, origin=run.source, first_observed_at=NOW,
    )

    body = APIClient().get("/api/ads/leak0003/versions/").json()
    rows = body["results"] if isinstance(body, dict) else body

    assert rows
    assert "payload" not in rows[0]


@pytest.mark.django_db
def test_provenance_is_staff_only(catalog):
    make_ad(catalog, "prov0001")
    plain = User.objects.create_user(email="u1@example.com", password="pw")
    client = APIClient()
    client.force_authenticate(plain)

    assert client.get("/api/admin/ads/prov0001/provenance/").status_code == 403


@pytest.mark.django_db
def test_staff_can_still_read_the_full_record(catalog):
    make_ad(catalog, "prov0002")
    staff = User.objects.create_user(
        email="s1@example.com", password="pw", is_staff=True
    )
    client = APIClient()
    client.force_authenticate(staff)

    body = client.get("/api/admin/ads/prov0002/provenance/").json()

    assert body["raw_payload"]["detail"]["dealer_phone"], (
        "removing it from the public serializer must not lose operator access"
    )


# --- catalog and statistics describe the same population --------------------

@pytest.mark.django_db
def test_a_hard_failed_ad_is_not_listed(catalog):
    """It was listed and filterable while every analytical read excluded it, so
    a user could find an ad the market summary insisted did not exist."""
    make_ad(catalog, "good0001")
    make_ad(catalog, "bad00001", quality_flags=["price_too_low"])

    codes = {r["code"] for r in APIClient().get("/api/ads/").json()["results"]}

    assert codes == {"good0001"}


@pytest.mark.django_db
def test_a_cohort_outlier_is_still_listed_and_carries_its_flag(catalog):
    """The opposite decision, deliberately: "not believable as a market price" is
    a warning to show, not a reason to hide the listing a buyer came to find."""
    make_ad(catalog, "odd00001", cohort_flags=["price_outlier_low"])

    rows = APIClient().get("/api/ads/").json()["results"]

    assert [r["code"] for r in rows] == ["odd00001"]
    assert rows[0]["cohort_flags"] == ["price_outlier_low"]


@pytest.mark.django_db
def test_newest_listings_exclude_hard_failed_ads(catalog):
    make_ad(catalog, "new00001")
    make_ad(catalog, "new00002", quality_flags=["brand_missing"])

    body = APIClient().get("/api/analytics/newest/").json()
    codes = {r["code"] for r in (body if isinstance(body, list) else body.get("results", body))}

    assert codes == {"new00001"}
