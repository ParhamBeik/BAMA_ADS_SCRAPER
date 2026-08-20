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

from apps.core.models import Ad, Brand, City, Model, Variant

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
def test_provenance_is_staff_only(catalog):
    make_ad(catalog, "prov0001")
    assert APIClient().get("/api/admin/ads/prov0001/provenance/").status_code == 403


@pytest.mark.django_db
def test_provenance_returns_the_full_record_to_staff(catalog):
    from apps.accounts.models import User

    make_ad(catalog, "prov0002")
    client = APIClient()
    client.force_authenticate(
        User.objects.create_superuser(email="ops@example.com", password="StrongPass1!")
    )
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
def test_inspect_routes_are_gone(catalog):
    make_ad(catalog, "hid00001")
    assert APIClient().get("/api/admin/inspect/ads/").status_code == 404
    assert APIClient().get("/api/admin/inspect/fetch-runs/").status_code == 404


@pytest.mark.django_db
def test_an_underpriced_outlier_is_never_hidden_from_browsing(catalog):
    """The asymmetry the browse filter turns on.

    A listing priced far *below* its peers is the underpriced car this product
    exists to find; hiding it to tidy the list would delete the product's whole
    point. It stays, carrying its flag so the reader can judge it.
    """
    make_ad(catalog, "odd00001", cohort_flags=["price_outlier_low"])

    rows = APIClient().get("/api/ads/").json()["results"]

    assert [r["code"] for r in rows] == ["odd00001"]
    assert rows[0]["cohort_flags"] == ["price_outlier_low"]


@pytest.mark.django_db
def test_an_absurdly_overpriced_listing_is_hidden_by_default(catalog):
    """The other half: a 206 was live at 5.8 trillion toman. That is noise in
    every list it appears in, and nobody browsing is looking for it — but
    ?include_outliers=true still returns it rather than pretending it is gone."""
    make_ad(catalog, "odd00002", cohort_flags=["price_outlier_high"])

    assert APIClient().get("/api/ads/").json()["results"] == []

    rows = APIClient().get("/api/ads/?include_outliers=true").json()["results"]
    assert [r["code"] for r in rows] == ["odd00002"]


@pytest.mark.django_db
def test_newest_route_is_gone(catalog):
    make_ad(catalog, "new00001")
    assert APIClient().get("/api/analytics/newest/").status_code == 404
