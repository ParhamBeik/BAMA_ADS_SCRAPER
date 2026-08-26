"""The HTTP surface: routing, serializers, permissions, and what leaks.

API/integration level: the subject is the serialized HTTP response, which is the
only place these facts are observable. Fixtures are self-contained ORM rows so
nothing depends on the seeded 50k-row dev database.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core import images, pricing
from apps.core.models import (
    Ad,
    Brand,
    City,
    Dealer,
    DealScoreCache,
    FetchRun,
    Model,
    PriceObservation,
    Variant,
)
from apps.core.pricing import compute_deal_scores
from tests.conftest import CDN, NOW, UTC

# A fixed "now" so publish_at / observed_at derived from it are deterministic.
_NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client() -> APIClient:
    """Anonymous DRF API client."""
    return APIClient()


@pytest.fixture
def catalog(db):
    """A self-contained slice of the catalog: brand, model, variant, city, ads.

    Returns a namespace-like dict so individual tests can address each piece.
    Ads carry strictly positive prices and a publish_at so they clear the
    publish-complete filter applied by AdViewSet.list / markets.
    """
    brand = Brand.objects.create(slug="testbrand", name_fa="برند تست")
    model = Model.objects.create(brand=brand, name_fa="مدل تست")
    variant = Variant.objects.create(model=model, name_fa="دنده‌ای")
    city = City.objects.create(name_fa="تهران", province="تهران")

    # Eight priced, published ads — enough for fair_price's MIN_PEERS=8.
    ads = []
    for i in range(8):
        ads.append(
            Ad.objects.create(
                code=f"ad{i}",
                brand=brand,
                model=model,
                variant=variant,
                city=city,
                title=f"مدل تست {i}",
                # year is the raw Bama value; year_jalali is the canonical cohort
                # key that filters and peer grouping now use.
                year=1399,
                year_jalali=1399,
                year_gregorian=2020,
                year_calendar=Ad.YearCalendar.JALALI,
                mileage=100_000 + i * 40_000,
                current_price=1_000_000_000 + i * 20_000_000,
                publish_at=_NOW - timedelta(days=i),
                last_seen_at=_NOW,
                first_seen_at=_NOW - timedelta(days=i),
            )
        )
    # One ad missing publish/price: excluded from list/markets but retrievable
    # directly by code (detail view does not apply the publish-complete filter).
    unfiltered = Ad.objects.create(
        code="ad_unfiltered",
        brand=brand,
        model=model,
        current_price=0,
        publish_at=None,
    )

    return {
        "brand": brand,
        "model": model,
        "variant": variant,
        "city": city,
        "ads": ads,
        "unfiltered": unfiltered,
    }


# ---------------------------------------------------------------------------
# Local staff login (Django admin only)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_wipe_users_refuses_without_confirmation():
    from django.core.management import call_command
    from django.core.management.base import CommandError

    User.objects.create_user(email="someone@example.com", password="StrongPass1!")
    with pytest.raises(CommandError):
        call_command("wipe_users")
    assert User.objects.count() == 1


@pytest.mark.django_db
def test_wipe_users_clears_every_account():
    from django.core.management import call_command

    User.objects.create_user(email="a@example.com", password="StrongPass1!")
    User.objects.create_user(email="b@example.com", password="StrongPass1!")
    call_command("wipe_users", yes=True)
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_auth_registration_creates_session_user(api_client):
    response = api_client.post(
        "/api/auth/register/",
        {"email": "new-user@example.com", "password": "StrongPass1!"},
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["email"] == "new-user@example.com"

    me = api_client.get("/api/auth/me/")
    assert me.status_code == 200
    assert me.json()["email"] == "new-user@example.com"


@pytest.mark.django_db
def test_auth_registration_rejects_duplicate_and_weak_password(api_client):
    User.objects.create_user(email="existing@example.com", password="StrongPass1!")

    duplicate = api_client.post(
        "/api/auth/register/",
        {"email": "existing@example.com", "password": "StrongPass1!"},
        format="json",
    )
    assert duplicate.status_code == 400
    assert "email" in duplicate.json()

    weak = api_client.post(
        "/api/auth/register/",
        {"email": "new@example.com", "password": "123"},
        format="json",
    )
    assert weak.status_code == 400
    assert "password" in weak.json()


# ---------------------------------------------------------------------------
# Catalog endpoints (/api/brands, /models, /variants, /ads)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_brands_list(api_client, catalog):
    resp = api_client.get("/api/brands/")
    assert resp.status_code == 200
    body = resp.json()
    # pagination_class = None -> bare list.
    assert isinstance(body, list)
    slugs = [b["slug"] for b in body]
    assert catalog["brand"].slug in slugs
    # regression: BrandSerializer exposes exactly these fields (no `source` leak).
    assert set(body[0].keys()) == {"slug", "name_fa", "name_en", "aliases"}


@pytest.mark.django_db
def test_brand_models_list(api_client, catalog):
    brand = catalog["brand"]
    resp = api_client.get(f"/api/brands/{brand.slug}/models/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert isinstance(body, list)
    assert any(m["id"] == catalog["model"].id for m in body)
    # ModelSerializer fields: id, brand_slug, name_fa.
    assert set(body[0].keys()) == {"id", "brand_slug", "name_fa"}
    assert body[0]["brand_slug"] == brand.slug


@pytest.mark.django_db
def test_model_variants_list(api_client, catalog):
    model = catalog["model"]
    resp = api_client.get(f"/api/models/{model.id}/variants/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert isinstance(body, list)
    assert any(v["id"] == catalog["variant"].id for v in body)
    # VariantSerializer exposes model_id as an integer.
    assert set(body[0].keys()) == {"id", "model_id", "name_fa"}
    assert isinstance(body[0]["model_id"], int)
    assert body[0]["model_id"] == model.id


@pytest.mark.django_db
def test_ads_list_returns_publish_complete_only(api_client, catalog):
    resp = api_client.get("/api/ads/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    codes = {row["code"] for row in body["results"]}
    # The eight priced/published ads appear; the zero-price unpublished one does not.
    assert {f"ad{i}" for i in range(8)} <= codes
    assert "ad_unfiltered" not in codes


@pytest.mark.django_db
def test_ads_list_filters(api_client, catalog):
    model = catalog["model"]
    brand = catalog["brand"]

    # ?model=<pk>
    resp = api_client.get(f"/api/ads/?model={model.id}")
    assert resp.status_code == 200
    codes = {r["code"] for r in resp.json()["results"]}
    assert codes == {f"ad{i}" for i in range(8)}

    # ?brand=<slug>
    resp = api_client.get(f"/api/ads/?brand={brand.slug}")
    assert resp.status_code == 200
    assert {r["code"] for r in resp.json()["results"]} == {
        f"ad{i}" for i in range(8)
    }

    # ?price_min=<lo>&price_max=<hi>  (filter field names, NOT min_price/max_price)
    lo, hi = 1_000_000_000, 1_040_000_000
    resp = api_client.get(f"/api/ads/?price_min={lo}&price_max={hi}")
    assert resp.status_code == 200
    codes = {r["code"] for r in resp.json()["results"]}
    assert codes == {"ad0", "ad1", "ad2"}  # 1.00, 1.02, 1.04 billion

    # ?year_min=&year_max=  (filter uses year_min/year_max, not bare year)
    resp = api_client.get("/api/ads/?year_min=1399&year_max=1399")
    assert resp.status_code == 200
    assert {r["code"] for r in resp.json()["results"]} == {
        f"ad{i}" for i in range(8)
    }


@pytest.mark.django_db
def test_ads_list_seller_type(api_client, catalog):
    """dealer_name/seller_type on AdSerializer: "dealer" when dealer_id is set,
    "private" otherwise. select_related("dealer") is already on the viewset
    queryset, so this adds no extra query."""
    dealer = Dealer.objects.create(id=1, name="نمایشگاه تست")
    dealer_ad = catalog["ads"][0]
    dealer_ad.dealer = dealer
    dealer_ad.save(update_fields=["dealer"])
    private_ad = catalog["ads"][1]

    resp = api_client.get("/api/ads/")
    assert resp.status_code == 200, resp.content
    rows = {r["code"]: r for r in resp.json()["results"]}
    assert rows[dealer_ad.code]["seller_type"] == "dealer"
    assert rows[dealer_ad.code]["dealer_name"] == "نمایشگاه تست"
    assert rows[private_ad.code]["seller_type"] == "private"
    assert rows[private_ad.code]["dealer_name"] is None

    # ?seller_type=dealer / private filter (dealer__isnull under the hood).
    resp = api_client.get("/api/ads/?seller_type=dealer")
    assert {r["code"] for r in resp.json()["results"]} == {dealer_ad.code}
    resp = api_client.get("/api/ads/?seller_type=private")
    codes = {r["code"] for r in resp.json()["results"]}
    assert dealer_ad.code not in codes
    assert private_ad.code in codes


@pytest.mark.django_db
def test_ads_list_hides_overpriced_outliers_by_default(api_client, catalog):
    """without_high_outliers() gates the default /api/ads/ list; a listing the
    cohort pass flagged is still a real ad, so ?include_outliers=true must
    restore it rather than the flag deleting it outright."""
    outlier = catalog["ads"][0]
    outlier.cohort_flags = ["price_outlier_high"]
    outlier.save(update_fields=["cohort_flags"])

    resp = api_client.get("/api/ads/")
    assert resp.status_code == 200, resp.content
    assert outlier.code not in {r["code"] for r in resp.json()["results"]}

    resp = api_client.get("/api/ads/?include_outliers=true")
    assert resp.status_code == 200, resp.content
    assert outlier.code in {r["code"] for r in resp.json()["results"]}


@pytest.mark.django_db
def test_overview_priced_count_matches_the_explorer_population(api_client, catalog):
    """Both screens must exclude the same absurd high-price rows."""
    outlier = catalog["ads"][0]
    outlier.cohort_flags = ["price_outlier_high"]
    outlier.save(update_fields=["cohort_flags"])

    explorer = api_client.get("/api/ads/").json()
    overview = api_client.get("/api/analytics/overview/").json()

    assert overview["priced_listings"] == explorer["count"] == 7


@pytest.mark.django_db
def test_ads_list_hides_removed_ads_but_detail_keeps_saved_history(api_client, catalog):
    removed = catalog["ads"][0]
    removed.status = Ad.Status.REMOVED
    removed.save(update_fields=["status"])

    list_codes = {row["code"] for row in api_client.get("/api/ads/").json()["results"]}

    assert removed.code not in list_codes
    assert api_client.get(f"/api/ads/{removed.code}/").status_code == 200


@pytest.mark.django_db
def test_ad_detail_existing_and_404(api_client, catalog):
    resp = api_client.get("/api/ads/ad0/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["code"] == "ad0"


@pytest.mark.django_db
def test_ad_detail_404_for_missing_code(api_client):
    resp = api_client.get("/api/ads/does-not-exist/")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Market endpoints (/api/markets, /markets/<id>/true-mean|bollinger|price-trends,
# /api/ads/<code>/price-history)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_markets_landing_200_with_ad_count(api_client, catalog):
    """Regression: Ad PK is `code`, not `id`. Count("id") raised FieldError."""
    resp = api_client.get("/api/markets/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    row = next(r for r in body if r["model_id"] == catalog["model"].id)
    assert row["ad_count"] == 8
    # Regression: F()-annotations on model/brand must not raise (TypeError before).
    assert row["model_name"] == catalog["model"].name_fa
    assert row["brand_slug"] == catalog["brand"].slug
    assert "brand_name" in row and row["brand_name"]
    assert row["min_price"] <= row["median_price"] <= row["max_price"]


@pytest.mark.django_db
def test_deleted_market_routes_are_gone(api_client, catalog):
    model = catalog["model"]
    for suffix in ("true-mean", "bollinger", "price-trends"):
        assert api_client.get(f"/api/markets/{model.id}/{suffix}/").status_code == 404


@pytest.mark.django_db
def test_ad_price_history(api_client, catalog):
    ad = catalog["ads"][0]
    # Seed a couple of change-only price observations.
    run = FetchRun.objects.create(source=FetchRun.Source.BULK_IMPORT)
    for i, price in enumerate((900_000_000, 950_000_000)):
        PriceObservation.objects.create(
            ad=ad,
            fetch_run=run,
            observed_at=_NOW - timedelta(days=5 - i),
            price=price,
            fingerprint=f"fp{i}",
        )
    resp = api_client.get(f"/api/ads/{ad.code}/price-history/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["code"] == ad.code
    assert body["current_price"] == ad.current_price
    assert len(body["series"]) == 2


@pytest.mark.django_db
def test_ad_price_history_404_for_missing_ad(api_client):
    resp = api_client.get("/api/ads/nope/price-history/")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Insights endpoints were collapsed into /api/research/...
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_legacy_insights_path_is_gone(api_client, catalog):
    model = catalog["model"]
    resp = api_client.get(f"/api/insights/{model.id}/liquidity/")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Dead history / inspect routes
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_deleted_history_routes_are_gone(api_client):
    for path in (
        "/api/changes/",
        "/api/observations/",
        "/api/fetch-runs/",
        "/api/ads/ad0/versions/",
        "/api/ads/ad0/changes/",
        "/api/ads/ad0/timeline/",
    ):
        assert api_client.get(path).status_code == 404, path


# ---------------------------------------------------------------------------
# Regression: AdSerializer exposes *_id as integers, no redundant `source`.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ad_serializer_id_fields_are_integers(api_client, catalog):
    """Regression: AdSerializer/VariantSerializer previously raised AssertionError
    when a `source` kwarg collided with a declared field. The *_id fields must
    also serialize as integers (not nested objects)."""
    resp = api_client.get("/api/ads/ad0/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert isinstance(body["model_id"], int)
    assert isinstance(body["variant_id"], int)
    assert isinstance(body["city_id"], int)
    assert body["model_id"] == catalog["model"].id
    assert body["variant_id"] == catalog["variant"].id
    assert body["city_id"] == catalog["city"].id


@pytest.mark.django_db
def test_deal_scores_return_envelope_with_evidence(api_client, catalog):
    ad = catalog["ads"][0]
    DealScoreCache.objects.create(
        ad=ad,
        score=12.5,
        discount_pct=12.5,
        peer_median=1_200_000_000,
        components={
            "peer_count": 11,
            "confidence": "low",
            "age_days": 3,
        },
    )
    # band=all still recency-windows; pin this ad to now so the envelope
    # test is not hostage to the fixture's July dates.
    from django.utils import timezone
    ad.publish_at = timezone.now()
    ad.save(update_fields=["publish_at"])
    resp = api_client.get("/api/analytics/deal-scores/?band=all&limit=10")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert "results" in body
    assert "as_of" in body
    assert "coverage" in body
    assert "methodology_version" in body
    assert body["window"]["ceiling_pct"] == pricing.TRUSTED_MAX_DISCOUNT
    row = body["results"][0]
    assert row["code"] == ad.code
    assert row["peer_count"] == 11
    assert row["confidence"] == "low"
    assert row["age_days"] == 3
    assert row["price"] < row["peer_median"]


@pytest.mark.django_db
def test_deal_scores_price_bounds(api_client, catalog):
    from django.utils import timezone

    cheap, pricey = catalog["ads"][0], catalog["ads"][7]  # 1.00bn vs 1.14bn
    Ad.objects.filter(code__in=(cheap.code, pricey.code)).update(
        publish_at=timezone.now(),
    )
    for ad, score in ((cheap, 10.0), (pricey, 20.0)):
        DealScoreCache.objects.create(
            ad=ad, score=score, discount_pct=score, peer_median=1_200_000_000,
            components={"peer_count": 11, "confidence": "low", "age_days": 3},
        )

    resp = api_client.get(
        f"/api/analytics/deal-scores/?band=all&price_min={cheap.current_price}"
        f"&price_max={cheap.current_price}"
    )
    assert resp.status_code == 200, resp.content
    codes = {r["code"] for r in resp.json()["results"]}
    assert codes == {cheap.code}


@pytest.mark.django_db
def test_deal_scores_exclude_unclear_price_basis(api_client, catalog):
    """A deposit is not a discounted car, even when it has a valid numeric price."""
    deposit = catalog["ads"][0]
    deposit.description = "مبلغ فوق پیش پرداخت است"
    deposit.save(update_fields=["description"])

    compute_deal_scores()

    rows = api_client.get(
        "/api/analytics/deal-scores/?band=all&limit=200"
    ).json()["results"]
    assert deposit.code not in {row["code"] for row in rows}


@pytest.mark.django_db
def test_deal_score_pagination_is_stable_when_scores_tie(api_client, catalog):
    from django.utils import timezone

    Ad.objects.filter(code__in=[ad.code for ad in catalog["ads"]]).update(
        publish_at=timezone.now(),
    )
    for ad in catalog["ads"]:
        DealScoreCache.objects.create(
            ad=ad,
            score=10.0,
            discount_pct=10.0,
            peer_median=1_200_000_000,
            components={"peer_count": 11, "confidence": "low", "age_days": 3},
        )

    first = api_client.get("/api/analytics/deal-scores/?band=all&limit=3&offset=0").json()
    second = api_client.get("/api/analytics/deal-scores/?band=all&limit=3&offset=3").json()

    assert first["count"] == 8
    assert len(first["results"]) == len(second["results"]) == 3
    assert not {
        row["code"] for row in first["results"]
    } & {row["code"] for row in second["results"]}


# ---------------------------------------------------------------------------
# The board's three bands
# ---------------------------------------------------------------------------
#
# Integration level, not unit: the thing being asserted is which listings a
# reader is shown under each tab, and the banding, the window and the ordering
# only combine at the HTTP boundary.


@pytest.fixture
def dated_deals(catalog):
    """Deals at known ages and discounts, relative to the real clock.

    Ages are anchored on ``timezone.now()`` rather than the module's fixed
    ``_NOW`` because the window is measured against the real clock — a fixture
    pinned to a date in the past drops out of every window as the suite ages.
    """
    from django.utils import timezone

    now = timezone.now()
    made = {}
    for ad, days, discount in zip(
        catalog["ads"],
        (0, 0, 2, 2, 5, 5, 40, 40),
        (8.0, 30.0, 12.0, 6.0, 20.0, 9.0, 22.0, 11.0),
        strict=True,
    ):
        ad.publish_at = now - timedelta(days=days)
        ad.save(update_fields=["publish_at"])
        made[ad.code] = DealScoreCache.objects.create(
            ad=ad, score=discount, discount_pct=discount,
            peer_median=1_200_000_000,
            components={"peer_count": 11, "confidence": "low", "age_days": days},
        )
    return made


@pytest.mark.django_db
def test_review_band_holds_everything_above_the_ceiling(api_client, dated_deals):
    """A 30% gap is not hidden — it is moved off the page that recommends."""
    rows = api_client.get("/api/analytics/deal-scores/?band=review").json()["results"]
    assert {r["code"] for r in rows} == {"ad1"}  # the only one over 25%
    assert all(r["discount_pct"] > pricing.TRUSTED_MAX_DISCOUNT for r in rows)


@pytest.mark.django_db
def test_trusted_bands_never_show_a_listing_above_the_ceiling(api_client, dated_deals):
    for band in ("top", "all"):
        rows = api_client.get(f"/api/analytics/deal-scores/?band={band}").json()["results"]
        assert all(r["discount_pct"] <= pricing.TRUSTED_MAX_DISCOUNT for r in rows), band


@pytest.mark.django_db
def test_board_ranks_freshness_before_discount(api_client, dated_deals):
    """A fresh 8% outranks a five-day-old 20%. That is the whole point."""
    rows = api_client.get("/api/analytics/deal-scores/?band=all").json()["results"]
    order = [r["code"] for r in rows]
    assert order.index("ad0") < order.index("ad4")   # today's 8% before day-5's 20%
    assert order.index("ad2") < order.index("ad4")   # day-2's 12% before day-5's 20%
    # ...but within one band the discount still decides.
    assert order.index("ad2") < order.index("ad3")   # 12% before 6%, both day 2
    assert order.index("ad4") < order.index("ad5")   # 20% before 9%, both day 5


@pytest.mark.django_db
def test_top_band_excludes_listings_older_than_the_window(api_client, dated_deals):
    body = api_client.get("/api/analytics/deal-scores/?band=top").json()
    codes = {r["code"] for r in body["results"]}
    # The 40-day-old rows are past MAX_WINDOW_DAYS however good they look.
    assert not codes & {"ad6", "ad7"}
    assert body["window"]["window_days"] <= pricing.MAX_WINDOW_DAYS
    assert all(
        r["discount_pct"] >= body["window"]["min_discount_pct"] for r in body["results"]
    )


@pytest.mark.django_db
def test_rows_carry_the_band_they_were_ordered_by(api_client, dated_deals):
    """The client must not re-derive the band from `days_listed`.

    `days_listed` floors to whole days, so recomputing lands on the wrong side
    of every edge: an ad aged 3.5 days floors to 3, reads as the 1-3 band, and
    is drawn under a heading the SQL ordering had already moved it out of.
    """
    rows = api_client.get("/api/analytics/deal-scores/?band=all").json()["results"]
    assert rows

    for row in rows:
        assert row["freshness"] is not None

    # Whatever the bands are, the ordering must be non-decreasing in them — that
    # is the property the grid's headings depend on to stay contiguous.
    bands = [r["freshness"] for r in rows]
    assert bands == sorted(bands)

    # And the band must agree with the age it was computed from.
    for row in rows:
        edges = [days for days, _ in pricing.FRESHNESS_BANDS]
        expected = next(
            (i for i, edge in enumerate(edges) if row["days_listed"] < edge), len(edges)
        )
        assert row["freshness"] == expected, row


@pytest.mark.django_db
def test_unknown_band_is_rejected_rather_than_silently_defaulted(api_client, catalog):
    resp = api_client.get("/api/analytics/deal-scores/?band=everything")
    assert resp.status_code == 400
    assert "band" in resp.json()["detail"]


@pytest.mark.django_db
def test_row_carries_a_link_that_resolves_on_bama(api_client, dated_deals, catalog):
    ad = catalog["ads"][0]
    ad.url = "/car/detail-dr769ivm-zamyad-pickup-cng-1394"
    ad.save(update_fields=["url"])

    rows = api_client.get("/api/analytics/deal-scores/?band=all").json()["results"]
    row = next(r for r in rows if r["code"] == ad.code)
    assert row["bama_url"] == "https://bama.ir/car/detail-dr769ivm-zamyad-pickup-cng-1394"


@pytest.mark.django_db
def test_favorite_response_matches_saved_screen_contract(api_client, catalog):
    user = User.objects.create_user(email="demo@example.com", password="StrongPass1!")
    api_client.force_authenticate(user=user)
    ad = catalog["ads"][0]

    resp = api_client.post("/api/favorites/", {"code": ad.code}, format="json")

    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert set(body) == {
        "code", "ad_title", "ad_price", "previous_price", "price_changed_at", "created_at",
    }
    assert body["code"] == ad.code
    assert body["ad_title"] == ad.title
    assert body["ad_price"] == ad.current_price


# ---------------------------------------------------------------------------
# Admin job-trigger API (/api/admin/jobs/*)
# ---------------------------------------------------------------------------
# These views spawn a background thread. Patching apps.jobs.views._spawn keeps
# the test to HTTP behaviour — and stops a real thread writing JobRun rows
# outside the test transaction, where they survive into every later test.

@pytest.mark.django_db
def test_admin_fetch_is_202(api_client):
    user = User.objects.create_superuser(
        email="admin@example.com", password="StrongPass1!"
    )
    api_client.force_authenticate(user=user)
    with patch("apps.jobs.views._spawn") as mock_spawn:
        resp = api_client.post("/api/admin/jobs/fetch/", {"max_ads": 10}, format="json")
    assert resp.status_code == 202, resp.content
    body = resp.json()
    assert body["status"] == "started"
    assert body["command"] == "fetch"
    mock_spawn.assert_called_once()


@pytest.mark.django_db
def test_admin_import_route_is_gone(api_client):
    with patch("apps.jobs.views._spawn"):
        resp = api_client.post(
            "/api/admin/jobs/import/", {"limit": 5, "batch_size": 100}, format="json"
        )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_admin_refresh_is_202(api_client):
    user = User.objects.create_superuser(
        email="admin@example.com", password="StrongPass1!"
    )
    api_client.force_authenticate(user=user)
    with patch("apps.jobs.views._spawn") as mock_spawn:
        resp = api_client.post("/api/admin/jobs/refresh-analytics/", {}, format="json")
    assert resp.status_code == 202, resp.content
    body = resp.json()
    assert body["command"] == "refresh-analytics"
    mock_spawn.assert_called_once()


@pytest.mark.django_db
def test_admin_fetch_bad_input_is_400(api_client):
    user = User.objects.create_superuser(
        email="admin@example.com", password="StrongPass1!"
    )
    api_client.force_authenticate(user=user)
    with patch("apps.jobs.views._spawn"):
        resp = api_client.post(
            "/api/admin/jobs/fetch/", {"max_ads": "not-a-number"}, format="json"
        )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_admin_fetch_rejects_unsafe_bounds(api_client, settings):
    user = User.objects.create_superuser(
        email="admin@example.com", password="StrongPass1!"
    )
    api_client.force_authenticate(user=user)
    settings.BAMA_MAX_ADS = 500

    with patch("apps.jobs.views._spawn") as mock_spawn:
        resp = api_client.post(
            "/api/admin/jobs/fetch/", {"max_ads": 501}, format="json"
        )

    assert resp.status_code == 400
    mock_spawn.assert_not_called()


@pytest.mark.django_db
def test_admin_fetch_concurrency_guard_is_409(api_client):
    """A RUNNING live-fetch FetchRun blocks a new fetch with 409."""
    user = User.objects.create_superuser(
        email="admin@example.com", password="StrongPass1!"
    )
    api_client.force_authenticate(user=user)
    running = FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH, status=FetchRun.Status.RUNNING
    )
    try:
        with patch("apps.jobs.views._spawn"):
            resp = api_client.post("/api/admin/jobs/fetch/", {}, format="json")
        assert resp.status_code == 409, resp.content
        assert "detail" in resp.json()
    finally:
        running.delete()


@pytest.mark.django_db
def test_a_non_staff_user_can_save_ads_but_not_trigger_admin_jobs(api_client, catalog):
    user = User.objects.create_user(email="normal@example.com", password="StrongPass1!")
    api_client.force_authenticate(user=user)
    ad = catalog["ads"][0]

    favorite = api_client.post("/api/favorites/", {"code": ad.code}, format="json")
    denied = api_client.post("/api/admin/jobs/fetch/", {}, format="json")

    assert favorite.status_code == 201, favorite.content
    assert denied.status_code == 403, denied.content


@pytest.mark.django_db
def test_favorites_are_isolated_between_users(api_client, catalog):
    first = User.objects.create_user(email="first@example.com", password="StrongPass1!")
    second = User.objects.create_user(email="second@example.com", password="StrongPass1!")
    ad = catalog["ads"][0]

    api_client.force_authenticate(user=first)
    assert api_client.post("/api/favorites/", {"code": ad.code}, format="json").status_code == 201
    api_client.force_authenticate(user=second)
    body = api_client.get("/api/favorites/").json()
    assert body["results"] == []


# ==========================================================================
# What the API is allowed to hand out, and to whom
# ==========================================================================

@pytest.fixture
def exposure_catalog(db):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    model = Model.objects.create(brand=brand, name_fa="405", is_confirmed=True)
    variant = Variant.objects.create(model=model, name_fa="GLX")
    city = City.objects.create(name_fa="تهران")
    return {"brand": brand, "model": model, "variant": variant, "city": city}


def make_ad(exposure_catalog, code, **overrides):
    fields = dict(
        code=code,
        brand=exposure_catalog["brand"], model=exposure_catalog["model"],
        variant=exposure_catalog["variant"], city=exposure_catalog["city"],
        year_jalali=1399, mileage=100_000, current_price=500_000_000,
        status=Ad.Status.ACTIVE, first_seen_at=NOW, last_seen_at=NOW,
        publish_at=NOW,
        raw_payload={"detail": {"code": code, "dealer_phone": "0912..."}},
    )
    fields.update(overrides)
    return Ad.objects.create(**fields)


# --- the scraped payload is not public -------------------------------------

@pytest.mark.django_db
def test_ad_list_does_not_leak_the_raw_payload(exposure_catalog):
    make_ad(exposure_catalog, "leak0001")
    body = APIClient().get("/api/ads/").json()

    assert body["results"], "fixture should be listed"
    assert "raw_payload" not in body["results"][0]


@pytest.mark.django_db
def test_ad_detail_does_not_leak_the_raw_payload(exposure_catalog):
    make_ad(exposure_catalog, "leak0002")
    body = APIClient().get("/api/ads/leak0002/").json()

    assert body["code"] == "leak0002"
    assert "raw_payload" not in body


@pytest.mark.django_db
def test_provenance_is_staff_only(exposure_catalog):
    make_ad(exposure_catalog, "prov0001")
    assert APIClient().get("/api/admin/ads/prov0001/provenance/").status_code == 403


@pytest.mark.django_db
def test_provenance_returns_the_full_record_to_staff(exposure_catalog):
    from apps.accounts.models import User

    make_ad(exposure_catalog, "prov0002")
    client = APIClient()
    client.force_authenticate(
        User.objects.create_superuser(email="ops@example.com", password="StrongPass1!")
    )
    body = client.get("/api/admin/ads/prov0002/provenance/").json()

    assert body["raw_payload"]["detail"]["dealer_phone"], (
        "removing it from the public serializer must not lose operator access"
    )


# --- exposure_catalog and statistics describe the same population --------------------

@pytest.mark.django_db
def test_a_hard_failed_ad_is_not_listed(exposure_catalog):
    """It was listed and filterable while every analytical read excluded it, so
    a user could find an ad the market summary insisted did not exist."""
    make_ad(exposure_catalog, "good0001")
    make_ad(exposure_catalog, "bad00001", quality_flags=["price_too_low"])

    codes = {r["code"] for r in APIClient().get("/api/ads/").json()["results"]}

    assert codes == {"good0001"}


@pytest.mark.django_db
def test_inspect_routes_are_gone(exposure_catalog):
    make_ad(exposure_catalog, "hid00001")
    assert APIClient().get("/api/admin/inspect/ads/").status_code == 404
    assert APIClient().get("/api/admin/inspect/fetch-runs/").status_code == 404


@pytest.mark.django_db
def test_an_underpriced_outlier_is_never_hidden_from_browsing(exposure_catalog):
    """The asymmetry the browse filter turns on.

    A listing priced far *below* its peers is the underpriced car this product
    exists to find; hiding it to tidy the list would delete the product's whole
    point. It stays, carrying its flag so the reader can judge it.
    """
    make_ad(exposure_catalog, "odd00001", cohort_flags=["price_outlier_low"])

    rows = APIClient().get("/api/ads/").json()["results"]

    assert [r["code"] for r in rows] == ["odd00001"]
    assert rows[0]["cohort_flags"] == ["price_outlier_low"]


@pytest.mark.django_db
def test_an_absurdly_overpriced_listing_is_hidden_by_default(exposure_catalog):
    """The other half: a 206 was live at 5.8 trillion toman. That is noise in
    every list it appears in, and nobody browsing is looking for it — but
    ?include_outliers=true still returns it rather than pretending it is gone."""
    make_ad(exposure_catalog, "odd00002", cohort_flags=["price_outlier_high"])

    assert APIClient().get("/api/ads/").json()["results"] == []

    rows = APIClient().get("/api/ads/?include_outliers=true").json()["results"]
    assert [r["code"] for r in rows] == ["odd00002"]


@pytest.mark.django_db
def test_newest_route_is_gone(exposure_catalog):
    make_ad(exposure_catalog, "new00001")
    assert APIClient().get("/api/analytics/newest/").status_code == 404


# ---------------------------------------------------------------------------
# Signup, sessions, and the token path
#
# API level: every fact here is about an HTTP response or a cookie, which is
# where the contract actually lives.
# ---------------------------------------------------------------------------

def _register(client, email, password="StrongPass1!"):
    return client.post("/api/auth/register/", {"email": email, "password": password},
                       format="json")


@pytest.mark.django_db
def test_signup_never_grants_staff(api_client):
    """Public signup creates a regular user even on an empty table."""
    assert _register(api_client, "owner@example.com").status_code == 201
    owner = User.objects.get(email="owner@example.com")
    assert owner.is_staff is False and owner.is_superuser is False

    api_client.post("/api/auth/logout/")
    assert _register(api_client, "guest@example.com").status_code == 201
    guest = User.objects.get(email="guest@example.com")
    assert guest.is_staff is False and guest.is_superuser is False


@pytest.mark.django_db
def test_signup_after_a_wipe_is_still_a_regular_user(api_client):
    """Wiping accounts is not a way back into staff. Use createsuperuser."""
    from django.core.management import call_command

    _register(api_client, "owner@example.com")
    call_command("wipe_users", yes=True)

    assert _register(api_client, "owner@example.com").status_code == 201
    owner = User.objects.get(email="owner@example.com")
    assert owner.is_staff is False and owner.is_superuser is False


@pytest.mark.django_db
def test_an_existing_staff_user_stays_staff_when_someone_else_signs_up(api_client):
    User.objects.create_user(
        email="owner@example.com", password="StrongPass1!",
        is_staff=True, is_superuser=True,
    )
    assert _register(api_client, "guest@example.com").status_code == 201
    assert User.objects.get(email="owner@example.com").is_staff is True
    guest = User.objects.get(email="guest@example.com")
    assert guest.is_staff is False and guest.is_superuser is False


@pytest.mark.django_db
def test_the_session_cookie_is_not_readable_by_script(api_client):
    """The reason the SPA holds no token: an XSS bug cannot read this cookie."""
    _register(api_client, "owner@example.com")
    cookie = api_client.cookies["sessionid"]

    assert cookie["httponly"] is True
    assert cookie["samesite"] == "Lax"


@pytest.mark.django_db
def test_logging_out_ends_the_session(api_client):
    _register(api_client, "owner@example.com")
    assert api_client.get("/api/auth/me/").status_code == 200

    assert api_client.post("/api/auth/logout/").status_code == 204
    assert api_client.get("/api/auth/me/").status_code == 401


@pytest.mark.django_db
def test_logout_everywhere_kills_other_devices(catalog):
    """A password change is not a revocation; this is."""
    from rest_framework.test import APIClient

    laptop, phone = APIClient(), APIClient()
    _register(laptop, "owner@example.com")
    phone.post("/api/auth/login/",
               {"email": "owner@example.com", "password": "StrongPass1!"}, format="json")
    assert phone.get("/api/auth/me/").status_code == 200

    assert laptop.post("/api/auth/logout-everywhere/").json()["sessions_ended"] >= 2
    assert phone.get("/api/auth/me/").status_code == 401


@pytest.mark.django_db
def test_email_availability_is_reported_before_submit(api_client):
    User.objects.create_user(email="taken@example.com", password="StrongPass1!")

    taken = api_client.get("/api/auth/email-available/?email=TAKEN@example.com")
    free = api_client.get("/api/auth/email-available/?email=free@example.com")

    assert taken.json()["available"] is False   # case-insensitive
    assert free.json()["available"] is True
    assert api_client.get("/api/auth/email-available/").status_code == 400


@pytest.mark.django_db
def test_a_weak_password_is_refused_by_the_server_not_only_the_form(api_client):
    """The client-side checklist mirrors these rules; the server owns them."""
    for password in ("short1A", "12345678", "password"):
        response = _register(api_client, "weak@example.com", password)
        assert response.status_code == 400, password
        assert "password" in response.json()
    assert not User.objects.filter(email="weak@example.com").exists()


@pytest.mark.django_db
def test_an_api_client_can_get_and_refresh_a_token(api_client, catalog):
    """JWT exists for clients with nowhere to keep a cookie. It authenticates
    the same users against the same permissions as the session path."""
    from rest_framework.test import APIClient

    User.objects.create_user(email="robot@example.com", password="StrongPass1!")

    tokens = api_client.post("/api/auth/token/",
                             {"email": "robot@example.com", "password": "StrongPass1!"},
                             format="json").json()
    assert "access" in tokens and "refresh" in tokens

    bearer = APIClient()
    bearer.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    assert bearer.get("/api/auth/me/").json()["email"] == "robot@example.com"

    refreshed = api_client.post("/api/auth/token/refresh/",
                                {"refresh": tokens["refresh"]}, format="json")
    assert refreshed.status_code == 200 and "access" in refreshed.json()

    verified = api_client.post("/api/auth/token/verify/",
                               {"token": tokens["access"]}, format="json")
    assert verified.status_code == 200


@pytest.mark.django_db
def test_a_rotated_refresh_token_cannot_be_reused(api_client):
    """A stolen refresh token is good for exactly one use, and the moment the
    real client refreshes, the thief's copy is dead."""
    User.objects.create_user(email="robot@example.com", password="StrongPass1!")
    tokens = api_client.post("/api/auth/token/",
                             {"email": "robot@example.com", "password": "StrongPass1!"},
                             format="json").json()

    assert api_client.post("/api/auth/token/refresh/",
                           {"refresh": tokens["refresh"]}, format="json").status_code == 200
    replay = api_client.post("/api/auth/token/refresh/",
                             {"refresh": tokens["refresh"]}, format="json")
    assert replay.status_code == 401


# ---------------------------------------------------------------------------
# Photo addressing
# ---------------------------------------------------------------------------
#
# Unit level, not API: these are pure functions of an ad's two image columns, so
# an unsaved instance exercises every branch without a database or a socket.
# They earn a test because the two columns hold *different files* — Bama's
# `resize,w_450` thumbnail and its `w_600` gallery photo — and an addressing
# scheme that conflates them silently serves the large file to every card.

_SMALL = f"{CDN}/uploads/resize,w_450/thumb.jpg"
_LARGE = [f"{CDN}/uploads/w_600/{i}.jpg" for i in range(3)]


def test_thumbnail_and_gallery_resolve_to_different_files():
    ad = Ad(code="pic00001", primary_image_url=_SMALL, image_urls=list(_LARGE))
    thumb, gallery = images.ad_image_paths(ad)

    assert thumb == "/api/img/pic00001/thumb/"
    assert gallery == [f"/api/img/pic00001/{i}/" for i in range(3)]
    # The point of the split: the card does not fetch the 600px file.
    assert images.source_url(ad, None) == _SMALL
    assert images.source_url(ad, 0) == _LARGE[0]


def test_thumbnail_falls_back_to_the_gallery_when_unset():
    ad = Ad(code="pic00002", primary_image_url="", image_urls=list(_LARGE))
    assert images.ad_image_paths(ad)[0] == "/api/img/pic00002/thumb/"
    assert images.source_url(ad, None) == _LARGE[0]


def test_a_lone_thumbnail_is_still_addressable():
    """Rows whose gallery was never filled have one photo and no gallery."""
    ad = Ad(code="pic00003", primary_image_url=_SMALL, image_urls=[])
    assert images.ad_image_paths(ad) == ("/api/img/pic00003/thumb/", [])
    assert images.source_url(ad, None) == _SMALL
    assert images.source_url(ad, 0) == _SMALL


def test_a_photoless_ad_has_no_address_at_all():
    ad = Ad(code="pic00004", primary_image_url="", image_urls=[])
    assert images.ad_image_paths(ad) == ("", [])
    assert images.source_url(ad, None) == ""
    assert images.source_url(ad, 7) == ""


def test_out_of_range_and_non_cdn_urls_resolve_to_nothing():
    """The allowlist is re-applied on read, so a poisoned row cannot make the
    proxy fetch from somewhere else."""
    ad = Ad(code="pic00005", primary_image_url="https://evil.example/x.jpg",
            image_urls=["https://bama.ir@evil.example/x.jpg"])
    assert images.source_url(ad, None) == ""
    assert images.source_url(ad, 0) == ""
    assert images.source_url(ad, 99) == ""
    assert images.source_url(ad, -1) == ""
