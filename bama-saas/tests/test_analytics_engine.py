"""Tests for Phase 4 — analytics engine (deal scores, metrics, endpoints).

Self-contained ORM fixtures (same pattern as tests/test_api.py and
tests/test_analytics.py): brand/model/variant/city + several priced published
ads, a below-median "cheap" ad, a removed ad, and a PriceDropEvent. Covers the
deal-score service, the market_snapshot command, the metrics functions, and a
couple of HTTP endpoints via DRF's APIClient.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone as django_timezone
from rest_framework.test import APIClient

from apps.core.models import (
    DealScoreCache,
    DailyInventorySnapshot,
    MarketSnapshot,
)
from apps.core.services import metrics
from apps.core.services.deal_score import compute_deal_scores
from apps.core.models import Ad, Brand, City, Dealer, Model, Variant
from apps.core.models import PriceDropEvent

UTC = timezone.utc
_NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def catalog(db):
    """A priced peer group + a cheap outlier + a removed ad + dealer/city."""
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو")
    model = Model.objects.create(brand=brand, name_fa="پژو ۲۰۶")
    variant = Variant.objects.create(model=model, name_fa="تیپ ۵")
    city = City.objects.create(name_fa="تهران", province="تهران")
    dealer = Dealer.objects.create(id=1001, name="نمایشگاه تست")

    # Six ads at the same price → median == price, so discount_pct == 0 and
    # none score. The cheap ad below the median is the one that should score.
    ads = []
    for i in range(6):
        ads.append(
            Ad.objects.create(
                code=f"a{i}", brand=brand, model=model, variant=variant,
                city=city, dealer=dealer, title=f"۲۰۶ شماره {i}",
                year=1399, year_jalali=1399, year_gregorian=2020,
                year_calendar="jalali",
                mileage=100_000 + i * 10_000,
                current_price=1_000_000_000,
                publish_at=_NOW - timedelta(days=i),
                first_seen_at=_NOW - timedelta(days=i),
                last_seen_at=_NOW,
            )
        )
    # Below the median: 20% discount → scores highest. Fresh (age 0).
    cheap = Ad.objects.create(
        code="cheap", brand=brand, model=model, variant=variant, city=city,
        dealer=dealer, title="۲۰۶ ارزان", year=1399, year_jalali=1399,
        year_gregorian=2020, year_calendar="jalali", mileage=20_000,
        current_price=800_000_000,
        publish_at=_NOW, first_seen_at=_NOW, last_seen_at=_NOW,
    )
    # Removed ad — sold after 5 days. Used by time_on_market / fast_sellers.
    removed = Ad.objects.create(
        code="sold", brand=brand, model=model, variant=variant, city=city,
        dealer=dealer, title="۲۰۶ فروخته شده", year=1398, year_jalali=1398,
        year_gregorian=2019, year_calendar="jalali", mileage=150_000,
        current_price=950_000_000,
        publish_at=_NOW - timedelta(days=20),
        first_seen_at=_NOW - timedelta(days=20),
        last_seen_at=_NOW - timedelta(days=5),
        status=Ad.Status.REMOVED,
        removed_at=_NOW - timedelta(days=15),
    )
    return {
        "brand": brand, "model": model, "variant": variant,
        "city": city, "dealer": dealer,
        "ads": ads, "cheap": cheap, "removed": removed,
    }


# ---------------------------------------------------------------------------
# compute_deal_scores
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_compute_deal_scores_scores_only_below_median(catalog):
    res = compute_deal_scores(min_peers=3)
    assert res["scored"] == 1  # only the cheap ad is below the median
    cheap = DealScoreCache.objects.get(ad_id="cheap")
    assert cheap.score > 0
    # discount_pct ~ (1e9 - 8e8)/1e9 * 100 = 20
    assert abs(cheap.discount_pct - 20.0) < 0.1
    # Fresh listing (age 0): score is not decayed → close to the raw discount.
    assert cheap.score <= 20.0
    assert cheap.peer_median == 1_000_000_000
    assert cheap.components["peer_count"] == 7  # 6 + the cheap ad


@pytest.mark.django_db
def test_compute_deal_scores_orders_cheap_first(catalog):
    compute_deal_scores(min_peers=3)
    ranked = list(DealScoreCache.objects.order_by("-score"))
    assert ranked[0].ad_id == "cheap"


@pytest.mark.django_db
def test_compute_deal_scores_skips_small_peer_group(catalog):
    """A separate model with only 2 ads is below min_peers → not scored."""
    other_model = Model.objects.create(brand=catalog["brand"], name_fa="پژو ۲۰۷")
    other_variant = Variant.objects.create(model=other_model, name_fa="تیپ ۲")
    for i in range(2):
        Ad.objects.create(
            code=f"other{i}", brand=catalog["brand"], model=other_model,
            variant=other_variant, year_jalali=1399, year_calendar="jalali",
            current_price=500_000_000, publish_at=_NOW, first_seen_at=_NOW,
        )
    res = compute_deal_scores(min_peers=3)
    assert not DealScoreCache.objects.filter(ad__model=other_model).exists()
    # The original model still scored its cheap ad.
    assert DealScoreCache.objects.filter(ad_id="cheap").exists()
    assert res["scored"] == 1


@pytest.mark.django_db
def test_compute_deal_scores_per_model_refresh(catalog):
    compute_deal_scores(min_peers=3)
    assert DealScoreCache.objects.count() == 1
    # Per-model refresh wipes only that model's rows.
    compute_deal_scores(min_peers=3, model_id=catalog["model"].id)
    assert DealScoreCache.objects.filter(ad_id="cheap").exists()


@pytest.mark.django_db
def test_compute_deal_scores_idempotent(catalog):
    compute_deal_scores(min_peers=3)
    compute_deal_scores(min_peers=3)
    assert DealScoreCache.objects.count() == 1  # not 2


# ---------------------------------------------------------------------------
# market_snapshot command
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_market_snapshot_builds_row(catalog):
    """No --date: the live path, reading the current Ad table.

    Deliberately not `--date <a past day>`. `Ad.status` describes what is live
    *now*, so the command only reads it for today; a past date is rebuilt from
    that day's DailyInventorySnapshot rows instead.
    """
    today = django_timezone.now().date()
    call_command("market_snapshot")
    snap = MarketSnapshot.objects.get(date=today)
    # 6 equal-priced + the cheap one = 7 active priced.
    assert snap.active_count == 7
    assert snap.median_price == 1_000_000_000
    assert snap.removed_count == 0  # the removed ad was removed on a different date
    # brand_breakdown maps brand slug → count of active ads.
    assert snap.brand_breakdown.get("peugeot") == 7


@pytest.mark.django_db
def test_market_snapshot_counts_removed_on_date(db):
    """An ad removed today counts toward removed_count for today."""
    now = django_timezone.now()
    brand = Brand.objects.create(slug="x", name_fa="ایکس")
    Ad.objects.create(
        code="rm", brand=brand, current_price=0, publish_at=now,
        status=Ad.Status.REMOVED, removed_at=now,
    )
    call_command("market_snapshot")
    snap = MarketSnapshot.objects.get(date=now.date())
    assert snap.removed_count == 1


@pytest.mark.django_db
def test_market_snapshot_idempotent(catalog):
    today = django_timezone.now().date()
    call_command("market_snapshot")
    call_command("market_snapshot")
    assert MarketSnapshot.objects.filter(date=today).count() == 1


@pytest.mark.django_db
def test_market_snapshot_refuses_past_date_without_snapshots(catalog):
    """Backfilling a past date must not stamp today's numbers onto it.

    Reading `Ad` for a past date wrote the *current* active_count under every
    historical row, which silently flattened the whole inventory series.
    """
    past = django_timezone.now().date() - timedelta(days=3)
    with pytest.raises(CommandError):
        call_command("market_snapshot", "--date", past.isoformat())


@pytest.mark.django_db
def test_market_snapshot_reconstructs_past_date_from_snapshots(catalog):
    """With cohort rows present, a past date is rebuilt as of that day."""
    past = django_timezone.now().date() - timedelta(days=3)
    DailyInventorySnapshot.objects.create(
        model=catalog["model"], variant=catalog["variant"], year_jalali=1399,
        date=past, ad_count=4, new_count=0, median_price=900_000_000,
        mean_price=900_000_000, min_price=900_000_000, max_price=900_000_000,
    )
    call_command("market_snapshot", "--date", past.isoformat())
    snap = MarketSnapshot.objects.get(date=past)
    # 4 ads as of that day — not the 7 currently live.
    assert snap.active_count == 4
    assert snap.median_price == 900_000_000
    assert snap.brand_breakdown.get("peugeot") == 4


# ---------------------------------------------------------------------------
# metrics service functions
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_metrics_rankings_brands(catalog):
    rows = metrics.rankings("brands", limit=10)
    assert rows, "rankings should return at least one brand"
    top = next(r for r in rows if r["id"] == "peugeot")
    assert top["ad_count"] == 7  # 6 equal + the cheap ad (removed excluded)
    assert top["min_price"] == 800_000_000
    assert top["max_price"] == 1_000_000_000


@pytest.mark.django_db
def test_metrics_rankings_bad_dim_returns_empty():
    assert metrics.rankings("nonsense") == []


@pytest.mark.django_db
def test_metrics_regional(catalog):
    rows = metrics.regional(limit=10)
    assert rows
    tehran = next(r for r in rows if r["city_id"] == catalog["city"].id)
    assert tehran["city_name"] == catalog["city"].name_fa
    assert tehran["ad_count"] == 7


@pytest.mark.django_db
def test_metrics_dealer_stats(catalog):
    rows = metrics.dealer_stats(limit=10)
    assert rows
    d = next(r for r in rows if r["dealer_id"] == 1001)
    assert d["name"] == "نمایشگاه تست"
    assert d["ad_count"] == 7


@pytest.mark.django_db
def test_metrics_inventory_trend(catalog):
    """Seed two days of DailyInventorySnapshot rows for the model."""
    model = catalog["model"]
    today = _NOW.date()
    yesterday = today - timedelta(days=1)
    DailyInventorySnapshot.objects.create(
        model=model, variant=catalog["variant"], year_jalali=1399, date=yesterday,
        ad_count=5, new_count=0, median_price=1_000_000_000,
    )
    DailyInventorySnapshot.objects.create(
        model=model, variant=catalog["variant"], year_jalali=1399, date=today,
        ad_count=7, new_count=2, median_price=1_000_000_000,
    )
    res = metrics.inventory_trend(model.id, days=90)
    assert len(res["series"]) == 2
    assert res["series"][-1]["ad_count"] == 7
    assert res["growth"] == 2  # 7 - 5
    assert res["growth_pct"] == 40.0  # 2/5*100


@pytest.mark.django_db
def test_metrics_market_overview(catalog):
    today = _NOW.date()
    MarketSnapshot.objects.create(
        date=today, active_count=7, new_count=0, removed_count=0,
        median_price=1_000_000_000,
    )
    rows = metrics.market_overview(days=90)
    assert rows
    assert rows[-1]["active_count"] == 7


@pytest.mark.django_db
def test_metrics_time_on_market(catalog):
    model = catalog["model"]
    res = metrics.time_on_market(model.id)
    # 6 ads at publish ages 0..5 days + the cheap ad at age 0.
    assert res["active_count"] == 7
    assert res["median_days_listed"] >= 0
    # One removed ad left the feed after 5 days (first seen 20 → removed 15
    # days ago). "Delisted", not "sold": the feed cannot tell us which.
    assert res["removed_count"] == 1
    assert res["median_days_to_delist"] == 5


@pytest.mark.django_db
def test_metrics_fast_movers(catalog):
    model = catalog["model"]
    rows = metrics.fast_movers(model.id)
    assert rows
    delisted = rows[0]
    assert delisted["code"] == "sold"
    assert delisted["days_to_delist"] == 5
    assert delisted["last_price"] == 950_000_000


@pytest.mark.django_db
def test_metrics_price_drops(catalog):
    ad = catalog["ads"][0]
    PriceDropEvent.objects.create(
        ad=ad, old_price=1_200_000_000, new_price=1_000_000_000,
        drop_amount=200_000_000, drop_pct=16.67, observed_at=_NOW,
    )
    rows = metrics.price_drops()
    assert rows
    drop = rows[0]
    assert drop["code"] == ad.code
    assert drop["drop_pct"] == pytest.approx(16.67, abs=0.01)

    # min_pct filter excludes it.
    assert metrics.price_drops(min_pct=20.0) == []


# ---------------------------------------------------------------------------
# HTTP endpoints (DRF APIClient)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_endpoint_deal_scores_list(catalog):
    compute_deal_scores(min_peers=3)
    client = APIClient()
    resp = client.get("/api/analytics/deal-scores/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert isinstance(body, list)
    assert body[0]["code"] == "cheap"
    assert body[0]["score"] > 0
    assert body[0]["model_name"] == catalog["model"].name_fa


@pytest.mark.django_db
def test_endpoint_deal_score_detail_404(catalog):
    client = APIClient()
    resp = client.get("/api/analytics/deal-scores/nope/")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_endpoint_rankings_brands(catalog):
    client = APIClient()
    resp = client.get("/api/analytics/rankings/brands/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert isinstance(body, list)
    assert any(r["id"] == "peugeot" for r in body)


@pytest.mark.django_db
def test_endpoint_rankings_bad_dim_404(catalog):
    client = APIClient()
    resp = client.get("/api/analytics/rankings/nonsense/")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_endpoint_deal_scores_bad_limit_400(catalog):
    client = APIClient()
    resp = client.get("/api/analytics/deal-scores/?limit=notanint")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_endpoint_newest_and_regional(catalog):
    client = APIClient()
    resp = client.get("/api/analytics/newest/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert isinstance(body, list)
    assert {r["code"] for r in body} >= {f"a{i}" for i in range(6)} | {"cheap"}

    resp = client.get("/api/analytics/regional/")
    assert resp.status_code == 200, resp.content
    assert any(r["city_name"] == catalog["city"].name_fa for r in resp.json())


@pytest.mark.django_db
def test_endpoint_time_on_market(catalog):
    client = APIClient()
    resp = client.get(f"/api/analytics/time-on-market/{catalog['model'].id}/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["active_count"] == 7
    assert body["removed_count"] == 1
