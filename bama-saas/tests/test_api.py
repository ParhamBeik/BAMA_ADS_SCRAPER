"""API-level regression tests for the HTTP/view layer.

The rest of the suite covers services/parsing/importer; these tests lock down
the DRF serializers, views, and URL routing so the bugs that previously slipped
through (Count("id") vs Count("code"), redundant `source` kwarg on
ModelSerializer, F()-annotation typing in the markets view) cannot regress.

Conventions follow the rest of the suite: pytest + pytest-django, plain
fixtures, ``@pytest.mark.django_db``, and self-contained ORM fixtures so the
tests do not depend on the seeded 50k-row dev database. The DRF test client
``APIClient`` is used for JWT auth and JSON bodies.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import Ad, Brand, City, Model, Variant
from apps.core.models import (
    AdChangeEvent,
    AdObservation,
    AdVersion,
    FetchRun,
)
from apps.core.models import PriceObservation

UTC = timezone.utc

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

    # Six priced, published ads in the same peer group (enough for every
    # insights endpoint incl. depreciation which needs >=6 mileage points).
    ads = []
    for i in range(6):
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


@pytest.fixture
def authed_client(db) -> tuple[APIClient, User]:
    """An APIClient force-authenticated as a non-staff user + that user."""
    user = User.objects.create_user(email="user@example.com", password="Sup3rSecret!")
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


@pytest.fixture
def staff_client(db) -> tuple[APIClient, User]:
    """An APIClient force-authenticated as an is_staff user + that user."""
    user = User.objects.create_user(
        email="staff@example.com", password="Sup3rSecret!", is_staff=True
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


# ---------------------------------------------------------------------------
# Auth endpoints (/api/auth/*)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_register_creates_user_and_free_subscription(api_client):
    resp = api_client.post(
        "/api/auth/register/",
        {"email": "newbie@example.com", "password": "Sup3rSecret!", "full_name": "New"},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["user"]["email"] == "newbie@example.com"
    # RegisterView creates a free-tier subscription as a side effect.
    assert User.objects.filter(email="newbie@example.com").exists()
    user = User.objects.get(email="newbie@example.com")
    assert user.subscriptions.filter(plan_type="free").exists()


@pytest.mark.django_db
def test_login_returns_access_and_refresh(api_client):
    User.objects.create_user(email="login@example.com", password="Sup3rSecret!")
    resp = api_client.post(
        "/api/auth/login/",
        {"email": "login@example.com", "password": "Sup3rSecret!"},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    tokens = resp.json()
    assert "access" in tokens and "refresh" in tokens


@pytest.mark.django_db
def test_ensure_dev_admin_creates_verified_staff(settings):
    from django.core.management import call_command

    settings.DEBUG = True
    settings.DEV_ADMIN_EMAIL = "admin@bama.local"
    settings.DEV_ADMIN_PASSWORD = "LocalOps-2026"
    call_command("ensure_dev_admin")
    user = User.objects.get(email="admin@bama.local")
    assert user.is_staff
    assert user.email_verified_at is not None
    assert user.check_password("LocalOps-2026")


@pytest.mark.django_db
def test_ensure_dev_admin_refuses_when_not_debug(settings):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    settings.DEBUG = False
    settings.DEV_ADMIN_EMAIL = "admin@bama.local"
    settings.DEV_ADMIN_PASSWORD = "LocalOps-2026"
    with pytest.raises(CommandError):
        call_command("ensure_dev_admin")


@pytest.mark.django_db
def test_refresh_issues_new_access_token(api_client):
    User.objects.create_user(email="refresh@example.com", password="Sup3rSecret!")
    login = api_client.post(
        "/api/auth/login/",
        {"email": "refresh@example.com", "password": "Sup3rSecret!"},
        format="json",
    )
    refresh_token = login.json()["refresh"]
    resp = api_client.post("/api/auth/refresh/", {"refresh": refresh_token}, format="json")
    assert resp.status_code == 200, resp.content
    assert "access" in resp.json()


@pytest.mark.django_db
def test_me_unauthenticated_is_401(api_client):
    resp = api_client.get("/api/auth/me/")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_me_authenticated_returns_user_and_subscription(authed_client):
    client, user = authed_client
    resp = client.get("/api/auth/me/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["user"]["email"] == user.email
    # me returns a subscription key (None when the user has none).
    assert "subscription" in body


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
    # The six priced/published ads appear; the zero-price unpublished one does not.
    assert {f"ad{i}" for i in range(6)} <= codes
    assert "ad_unfiltered" not in codes


@pytest.mark.django_db
def test_ads_list_filters(api_client, catalog):
    model = catalog["model"]
    brand = catalog["brand"]

    # ?model=<pk>
    resp = api_client.get(f"/api/ads/?model={model.id}")
    assert resp.status_code == 200
    codes = {r["code"] for r in resp.json()["results"]}
    assert codes == {f"ad{i}" for i in range(6)}

    # ?brand=<slug>
    resp = api_client.get(f"/api/ads/?brand={brand.slug}")
    assert resp.status_code == 200
    assert {r["code"] for r in resp.json()["results"]} == {
        f"ad{i}" for i in range(6)
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
        f"ad{i}" for i in range(6)
    }


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
    assert row["ad_count"] == 6
    # Regression: F()-annotations on model/brand must not raise (TypeError before).
    assert row["model_name"] == catalog["model"].name_fa
    assert row["brand_slug"] == catalog["brand"].slug
    assert "brand_name" in row and row["brand_name"]
    assert row["min_price"] <= row["avg_price"] <= row["max_price"]


@pytest.mark.django_db
def test_market_true_mean(api_client, catalog):
    model = catalog["model"]
    resp = api_client.get(f"/api/markets/{model.id}/true-mean/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["model_id"] == model.id
    assert body["count_before"] >= 1


@pytest.mark.django_db
def test_market_true_mean_bad_query_is_400(api_client, catalog):
    model = catalog["model"]
    resp = api_client.get(f"/api/markets/{model.id}/true-mean/?year=notanint")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_market_bollinger(api_client, catalog):
    model = catalog["model"]
    resp = api_client.get(f"/api/markets/{model.id}/bollinger/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert "series" in body
    assert body["model_name"] == model.name_fa


@pytest.mark.django_db
def test_market_price_trends(api_client, catalog):
    model = catalog["model"]
    resp = api_client.get(f"/api/markets/{model.id}/price-trends/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["bucket"] == "month"  # default
    assert isinstance(body["series"], list)


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
# History endpoints (/api/changes, /observations, /fetch-runs, per-ad versions/
# changes/timeline)
# ---------------------------------------------------------------------------

@pytest.fixture
def history_for_ad(catalog):
    """Wire up a version, observation, and change event for ads[0]."""
    ad = catalog["ads"][0]
    run = FetchRun.objects.create(source=FetchRun.Source.BULK_IMPORT)
    version = AdVersion.objects.create(
        ad=ad,
        semantic_hash="sem-1",
        raw_hash="raw-1",
        payload={"detail": {"code": ad.code}},
        origin=AdVersion.Origin.BULK_IMPORT,
        first_observed_at=_NOW - timedelta(days=2),
    )
    observation = AdObservation.objects.create(
        ad=ad,
        fetch_run=run,
        version=version,
        observed_at=_NOW - timedelta(days=2),
        raw_hash="raw-1",
        publish_phrase="2 روز پیش",
        rank=1,
    )
    change = AdChangeEvent.objects.create(
        ad=ad,
        observation=observation,
        new_version=version,
        event_type=AdChangeEvent.EventType.CONTENT_CHANGED,
        categories=["price/payment"],
        changed_paths=["/price/price"],
        changes={"price": {"old": 1, "new": 2}},
        origin="bulk_import",
    )
    return {"run": run, "version": version, "observation": observation, "change": change}


@pytest.mark.django_db
def test_changes_list(api_client, catalog, history_for_ad):
    resp = api_client.get("/api/changes/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    ids = {row["id"] for row in body["results"]}
    assert str(history_for_ad["change"].id) in {str(i) for i in ids}


@pytest.mark.django_db
def test_observations_list(api_client, catalog, history_for_ad):
    resp = api_client.get("/api/observations/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert len(body["results"]) >= 1


@pytest.mark.django_db
def test_fetch_runs_list(api_client, catalog, history_for_ad):
    resp = api_client.get("/api/fetch-runs/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert len(body["results"]) >= 1


@pytest.mark.django_db
def test_ad_versions(api_client, catalog, history_for_ad):
    ad = catalog["ads"][0]
    resp = api_client.get(f"/api/ads/{ad.code}/versions/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["semantic_hash"] == "sem-1"


@pytest.mark.django_db
def test_ad_changes(api_client, catalog, history_for_ad):
    ad = catalog["ads"][0]
    resp = api_client.get(f"/api/ads/{ad.code}/changes/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["event_type"] == AdChangeEvent.EventType.CONTENT_CHANGED.value


@pytest.mark.django_db
def test_ad_timeline_merges_observations_and_changes(api_client, catalog, history_for_ad):
    ad = catalog["ads"][0]
    resp = api_client.get(f"/api/ads/{ad.code}/timeline/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    kinds = {entry["kind"] for entry in body}
    assert kinds == {"observation", "change"}


@pytest.mark.django_db
def test_per_ad_history_404_for_missing_ad(api_client):
    for suffix in ("versions", "changes", "timeline"):
        resp = api_client.get(f"/api/ads/missing/{suffix}/")
        assert resp.status_code == 404, suffix


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


# ---------------------------------------------------------------------------
# Admin job-trigger API (/api/admin/jobs/*)
# ---------------------------------------------------------------------------
# These views spawn a background thread running call_command(...). We patch
# apps.jobs.views._spawn with a no-op so only HTTP behavior is exercised.

@pytest.mark.django_db
def test_admin_fetch_anonymous_is_401(api_client):
    with patch("apps.jobs.views._spawn"):
        resp = api_client.post("/api/admin/jobs/fetch/", {}, format="json")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_admin_fetch_authenticated_non_staff_is_403(authed_client):
    client, _ = authed_client
    with patch("apps.jobs.views._spawn"):
        resp = client.post("/api/admin/jobs/fetch/", {}, format="json")
    assert resp.status_code == 403


@pytest.mark.django_db
def test_admin_fetch_staff_is_202(staff_client):
    client, _ = staff_client
    with patch("apps.jobs.views._spawn") as mock_spawn:
        resp = client.post("/api/admin/jobs/fetch/", {"max_ads": 10}, format="json")
    assert resp.status_code == 202, resp.content
    body = resp.json()
    assert body["status"] == "started"
    assert body["command"] == "fetch_live"
    # The command was actually dispatched to the (patched) runner.
    mock_spawn.assert_called_once()
    args, kwargs = mock_spawn.call_args
    assert args[0] == "fetch_live"
    assert kwargs == {"max_ads": 10}


@pytest.mark.django_db
def test_admin_import_staff_is_202(staff_client):
    client, _ = staff_client
    with patch("apps.jobs.views._spawn") as mock_spawn:
        resp = client.post(
            "/api/admin/jobs/import/", {"limit": 5, "batch_size": 100}, format="json"
        )
    assert resp.status_code == 202, resp.content
    body = resp.json()
    assert body["command"] == "import_scraped"
    mock_spawn.assert_called_once_with("import_scraped", limit=5, batch_size=100)


@pytest.mark.django_db
def test_admin_refresh_staff_is_202(staff_client):
    client, _ = staff_client
    with patch("apps.jobs.views._spawn") as mock_spawn:
        resp = client.post("/api/admin/jobs/refresh-analytics/", {}, format="json")
    assert resp.status_code == 202, resp.content
    body = resp.json()
    # The URL is unchanged but the work behind it is not: it used to run
    # refresh_analytics, which rebuilt a table nothing read, so pressing the
    # button had no observable effect. It now rebuilds the real derived
    # analytics. The old ``min_count`` argument was a PriceStatistics concept and
    # no longer exists, so there is no input left to validate.
    assert body["command"] == "run_pipeline"
    mock_spawn.assert_called_once_with("run_pipeline", skip_fetch=True, cadence="full")


@pytest.mark.django_db
def test_admin_fetch_bad_input_is_400(staff_client):
    client, _ = staff_client
    with patch("apps.jobs.views._spawn"):
        resp = client.post(
            "/api/admin/jobs/fetch/", {"max_ads": "not-a-number"}, format="json"
        )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_admin_fetch_concurrency_guard_is_409(staff_client):
    """A RUNNING live-fetch FetchRun blocks a new fetch with 409."""
    client, _ = staff_client
    running = FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH, status=FetchRun.Status.RUNNING
    )
    try:
        with patch("apps.jobs.views._spawn"):
            resp = client.post("/api/admin/jobs/fetch/", {}, format="json")
        assert resp.status_code == 409, resp.content
        assert "detail" in resp.json()
    finally:
        # Clean up so the guard doesn't leak into other tests.
        running.delete()


@pytest.mark.django_db
def test_admin_import_concurrency_guard_is_409(staff_client):
    client, _ = staff_client
    running = FetchRun.objects.create(
        source=FetchRun.Source.BULK_IMPORT, status=FetchRun.Status.RUNNING
    )
    try:
        with patch("apps.jobs.views._spawn"):
            resp = client.post("/api/admin/jobs/import/", {}, format="json")
        assert resp.status_code == 409
    finally:
        running.delete()
