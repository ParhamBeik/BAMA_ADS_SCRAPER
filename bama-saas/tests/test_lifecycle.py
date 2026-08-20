"""Ad removal detection and the mileage-adjusted deal score.

Both are unit-level: they operate on ORM rows and pure arithmetic with no HTTP
and no network, so seeding rows directly is the smallest thing that fails when
the logic breaks. The removal rule gets the most cases because its failure modes
are asymmetric — marking too few ads is a stale statistic, marking too many
wipes the live market.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as tz

import pytest

from apps.core.models import (
    Ad,
    Brand,
    City,
    DealScoreCache,
    FetchRun,
    Model,
    PageCoverage,
    Variant,
)
from apps.core.pricing import compute_deal_scores
from apps.jobs.jobs import REQUIRED_MISSED_WINDOWS, mark_inactive, sweep_cutoff
from django.utils import timezone as djtz

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=tz.utc)


@pytest.fixture(autouse=True)
def _reset_ingest_caches():
    """Both ingest caches are process-global and survive test rollback.

    `resolve_dimensions` memoises City/Brand primary keys; after a test's
    transaction rolls back those ids no longer exist, and the next test's insert
    fails on the foreign key.
    """
    from apps.jobs.ingest import reset_cache
    from apps.jobs.ingest import reset_price_cache

    reset_cache()
    reset_price_cache()
    yield
    reset_cache()
    reset_price_cache()


@pytest.fixture
def catalog(db):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو")
    model = Model.objects.create(brand=brand, name_fa="پژو ۲۰۶")
    variant = Variant.objects.create(model=model, name_fa="تیپ ۵")
    city = City.objects.create(name_fa="تهران")
    return {"brand": brand, "model": model, "variant": variant, "city": city}


FEED_DEPTH = 100


def _cover(at, lo=1, hi=FEED_DEPTH, run=None):
    """Record that ranks ``lo..hi`` were fetched at ``at``.

    Coverage, not run status, is what the removal rule reads now, so these
    tests seed PageCoverage directly. ``run`` is shared between calls when a
    test needs one run to contribute several disjoint ranges.
    """
    run = run or FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH,
        status=FetchRun.Status.SUCCEEDED,
        started_at=at,
    )
    PageCoverage.objects.create(
        fetch_run=run,
        page_index=(lo - 1) // 30,
        rank_lo=lo,
        rank_hi=hi,
        ad_count=hi - lo + 1,
        new_count=0,
        changed_count=0,
        fetched_at=at,
    )
    return run


def _ad(catalog, code, last_seen, *, price=1_000_000_000, mileage=100_000):
    return Ad.objects.create(
        code=code, brand=catalog["brand"], model=catalog["model"],
        variant=catalog["variant"], city=catalog["city"],
        year=1399, year_jalali=1399, year_calendar="jalali",
        mileage=mileage, current_price=price,
        publish_at=last_seen, first_seen_at=last_seen - timedelta(days=10),
        last_seen_at=last_seen, status=Ad.Status.ACTIVE,
    )


# ---------------------------------------------------------------------------
# Coverage-based removal
#
# The rule reads accumulated PageCoverage over two consecutive windows, not
# fetch-run status. Windows are relative to wall-clock now, so these seed times
# are offsets from `timezone.now()` rather than the fixed NOW above.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_no_removal_with_one_covered_window(catalog):
    """The critical safety case: one window must never empty the market.

    A wall-clock rule marks everything REMOVED the moment the crawler stalls
    long enough. The coverage rule must refuse to act, because a single pass
    proves nothing about ads it may have missed to rank shift.
    """
    now = djtz.now()
    _cover(now - timedelta(hours=2))
    stale = _ad(catalog, "stale001", now - timedelta(days=30))

    mark_inactive()

    stale.refresh_from_db()
    assert stale.status == Ad.Status.ACTIVE
    cutoff, n = sweep_cutoff()
    assert cutoff is None
    assert n == 1


@pytest.mark.django_db
def test_no_removal_without_any_coverage(catalog):
    stale = _ad(catalog, "stale002", djtz.now() - timedelta(days=90))
    mark_inactive()
    stale.refresh_from_db()
    assert stale.status == Ad.Status.ACTIVE


@pytest.mark.django_db
def test_ad_absent_from_two_covered_windows_is_removed(catalog):
    now = djtz.now()
    _cover(now - timedelta(hours=2))    # recent window
    _cover(now - timedelta(hours=30))   # older window

    gone = _ad(catalog, "gone0001", now - timedelta(hours=50))

    mark_inactive()

    gone.refresh_from_db()
    assert gone.status == Ad.Status.REMOVED
    # removed_at is stamped with the ad's own last sighting, not "now" — that
    # is the best estimate of when it actually left the feed.
    assert gone.removed_at == gone.last_seen_at


@pytest.mark.django_db
def test_ad_seen_inside_the_windows_survives(catalog):
    now = djtz.now()
    _cover(now - timedelta(hours=2))
    _cover(now - timedelta(hours=30))

    survivor = _ad(catalog, "alive001", now - timedelta(hours=3))

    mark_inactive()

    survivor.refresh_from_db()
    assert survivor.status == Ad.Status.ACTIVE


@pytest.mark.django_db
def test_partial_coverage_does_not_authorise_removal(catalog):
    """A window that missed part of the feed proves nothing about it.

    The older window walked the whole feed, so the depth ratchet knows it is
    100 deep; the recent window only reached rank 50. Ranks 51-100 were seen by
    nobody recently, so no ad may be declared gone.
    """
    now = djtz.now()
    _cover(now - timedelta(hours=30), lo=1, hi=FEED_DEPTH)
    _cover(now - timedelta(hours=2), lo=1, hi=50)

    stale = _ad(catalog, "stale003", now - timedelta(days=30))
    mark_inactive()

    stale.refresh_from_db()
    assert stale.status == Ad.Status.ACTIVE
    assert sweep_cutoff()[0] is None


@pytest.mark.django_db
def test_coverage_unions_across_several_partial_runs(catalog):
    """The property the whole redesign exists for.

    Four interrupted runs that each walked a quarter of the feed prove exactly
    what one uninterrupted sweep proved. Under the old rule none of these set
    reached_end, so removal detection stalled and delisted ads stayed ACTIVE
    for days — which is what batched listing episodes into lumps.
    """
    now = djtz.now()
    for window_hours in (30, 2):
        at = now - timedelta(hours=window_hours)
        for lo in (1, 26, 51, 76):
            _cover(at, lo=lo, hi=lo + 24)   # a separate run each time

    gone = _ad(catalog, "gone0002", now - timedelta(hours=50))
    mark_inactive()

    gone.refresh_from_db()
    assert gone.status == Ad.Status.REMOVED


@pytest.mark.django_db
def test_days_override_bypasses_coverage_rule(catalog):
    """The escape hatch still works with no coverage on record at all."""
    stale = _ad(catalog, "stale005", djtz.now() - timedelta(days=30))
    mark_inactive(days=1)
    stale.refresh_from_db()
    assert stale.status == Ad.Status.REMOVED


@pytest.mark.django_db
def test_cutoff_is_the_start_of_the_older_window(catalog):
    now = djtz.now()
    _cover(now - timedelta(hours=2))
    _cover(now - timedelta(hours=30))
    cutoff, n = sweep_cutoff()
    assert n == REQUIRED_MISSED_WINDOWS
    # Two 24h windows back, give or take the second spent running the test.
    assert abs((cutoff - (now - timedelta(hours=48))).total_seconds()) < 60


# ---------------------------------------------------------------------------
# Lifecycle timestamps must stay monotonic under out-of-order ingestion
# ---------------------------------------------------------------------------

def _payload(code, price=1_000_000_000):
    return {
        "detail": {
            "code": code, "title": "پژو، ۲۰۶", "brand_fa": "پژو",
            "year": "1399", "mileage": "100,000", "type": "car",
            "time": "۲ ساعت پیش", "url": f"https://bama.ir/cad/{code}",
            "location": "تهران", "transmission": "دنده‌ای",
        },
        "price": {"price": str(price), "type": "lumpsum",
                  "payment": "0", "prepayment": "0", "installments": "0"},
    }


@pytest.mark.django_db
def test_stale_observation_does_not_move_last_seen_backwards(catalog):
    """import_history / crawl_gaps replay old pages; the bounds must not follow.

    `observed_at` is not monotonic across ingest calls, and writing it straight
    into last_seen_at put 5,009 production ads in a state where
    last_seen_at < first_seen_at and every duration came out negative.
    """
    from apps.jobs.ingest import ingest_ad
    from apps.jobs.parsing import extract_ad

    recent = NOW
    older = NOW - timedelta(days=10)
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)

    ingest_ad(extract_ad(_payload("mono0001"), recent), run=run,
              observed_at=recent, publish_at=recent)
    # A second run replaying an OLDER sighting of the same ad.
    run2 = FetchRun.objects.create(source=FetchRun.Source.HISTORY_REPLAY)
    ingest_ad(extract_ad(_payload("mono0001"), older), run=run2,
              observed_at=older, publish_at=older)

    ad = Ad.objects.get(code="mono0001")
    assert ad.last_seen_at == recent      # not dragged back to `older`
    assert ad.first_seen_at == older      # widened to the earliest sighting
    assert ad.last_seen_at >= ad.first_seen_at


@pytest.mark.django_db
def test_stale_observation_does_not_resurrect_a_removed_ad(catalog):
    """A backfill of old pages must not undo mark_inactive_ads."""
    from apps.jobs.ingest import ingest_ad
    from apps.jobs.parsing import extract_ad

    recent = NOW
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    ingest_ad(extract_ad(_payload("mono0002"), recent), run=run,
              observed_at=recent, publish_at=recent)

    Ad.objects.filter(code="mono0002").update(
        status=Ad.Status.REMOVED, removed_at=recent
    )

    run2 = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    ingest_ad(extract_ad(_payload("mono0002"), recent - timedelta(days=5)),
              run=run2, observed_at=recent - timedelta(days=5),
              publish_at=recent - timedelta(days=5))

    ad = Ad.objects.get(code="mono0002")
    assert ad.status == Ad.Status.REMOVED
    assert ad.removed_at is not None


@pytest.mark.django_db
def test_fresh_observation_still_reactivates(catalog):
    """The guard must not break the legitimate case: a genuinely re-seen ad."""
    from apps.jobs.ingest import ingest_ad
    from apps.jobs.parsing import extract_ad

    older = NOW - timedelta(days=5)
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    ingest_ad(extract_ad(_payload("mono0003"), older), run=run,
              observed_at=older, publish_at=older)
    Ad.objects.filter(code="mono0003").update(
        status=Ad.Status.REMOVED, removed_at=older
    )

    run2 = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    ingest_ad(extract_ad(_payload("mono0003"), NOW), run=run2,
              observed_at=NOW, publish_at=NOW)

    ad = Ad.objects.get(code="mono0003")
    assert ad.status == Ad.Status.ACTIVE
    assert ad.removed_at is None


# ---------------------------------------------------------------------------
# Mileage-adjusted deal score
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_thin_cohort_is_not_scored(catalog):
    """MIN_PEERS=8: a median of seven cars is not a deal board."""
    for i in range(7):
        _ad(catalog, f"thin{i:03d}", NOW, price=1_000_000_000, mileage=100_000)

    compute_deal_scores()

    assert DealScoreCache.objects.count() == 0


@pytest.mark.django_db
def test_asking_above_peer_median_is_not_a_deal(catalog):
    for i in range(8):
        _ad(catalog, f"peer{i:03d}", NOW, price=1_000_000_000, mileage=100_000)
    expensive = _ad(catalog, "pricey01", NOW, price=1_200_000_000, mileage=100_000)

    compute_deal_scores()

    assert DealScoreCache.objects.filter(ad_id=expensive.code).first() is None


@pytest.mark.django_db
def test_honest_discount_is_scored_with_evidence(catalog):
    for i in range(8):
        _ad(catalog, f"peer{i:03d}", NOW, price=1_000_000_000, mileage=100_000)
    cheap = _ad(catalog, "bargain1", NOW, price=800_000_000, mileage=100_000)

    compute_deal_scores()

    row = DealScoreCache.objects.get(ad_id=cheap.code)
    assert row.discount_pct == pytest.approx(20.0, abs=0.5)
    assert row.peer_median == 1_000_000_000
    assert row.components["peer_count"] == 9
    assert row.components["confidence"] == "low"
    assert row.components["fair_value"] > 0
    assert cheap.current_price < row.peer_median


@pytest.mark.django_db
def test_ask_below_half_peer_median_is_not_a_deal(catalog):
    """80M against a 1.7B cohort is a deposit/typo, not a 95% discount."""
    for i in range(8):
        _ad(catalog, f"peer{i:03d}", NOW, price=1_700_000_000, mileage=100_000)
    deposit = _ad(catalog, "deposit1", NOW, price=80_000_000, mileage=100_000)

    compute_deal_scores()

    assert DealScoreCache.objects.filter(ad_id=deposit.code).first() is None
