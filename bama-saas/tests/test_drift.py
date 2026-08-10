"""Distribution drift detection.

Test type: integration — a snapshot is an aggregate over the whole Ad table and
drift is a comparison across stored days, so neither half exists without the DB.

The distinguishing property of this layer is that it catches changes *no rule
anticipated*. The tests are written the same way: they never assert on a
verification rule, only on the shape of the data moving.
"""

from datetime import date, timedelta

import pytest

from apps.core.models import Ad, Brand, City, DataQualitySnapshot, Model, Variant
from apps.jobs.services import drift as D

TODAY = date(2026, 8, 8)


@pytest.fixture
def catalog(db):
    brand = Brand.objects.create(slug="saipa", name_fa="سایپا", is_confirmed=True)
    model = Model.objects.create(brand=brand, name_fa="پراید", is_confirmed=True)
    variant = Variant.objects.create(model=model, name_fa="۱۳۱")
    city = City.objects.create(name_fa="تهران")
    return {"brand": brand, "model": model, "variant": variant, "city": city}


def make_ads(catalog, n, *, transmission="دنده‌ای", price=500_000_000, prefix="ad"):
    from django.utils import timezone

    now = timezone.now()
    Ad.objects.bulk_create([
        Ad(
            code=f"{prefix}{i:05d}",
            brand=catalog["brand"], model=catalog["model"],
            variant=catalog["variant"], city=catalog["city"],
            year_jalali=1399, mileage=100_000, current_price=price,
            transmission=transmission, status=Ad.Status.ACTIVE,
            first_seen_at=now, last_seen_at=now, publish_at=now,
        )
        for i in range(n)
    ])


def seed_stable_history(days=10, **overrides):
    """A calm trailing window: the same numbers every day."""
    base = {
        "active_ads": 1000, "total_ads": 1000, "priced_ads": 1000,
        "null_rates": {"transmission": 0.02, "publish_at": 0.01},
        "distinct_counts": {"transmission": 3},
        "flag_counts": {"price_too_high": 10},
        "unconfirmed_models": 0, "price_median": 500_000_000,
    }
    base.update(overrides)
    for i in range(days, 0, -1):
        DataQualitySnapshot.objects.create(date=TODAY - timedelta(days=i), **base)


@pytest.mark.django_db
def test_snapshot_records_the_shape_of_the_population(catalog):
    make_ads(catalog, 5)
    snapshot = D.build_snapshot(TODAY)

    assert snapshot.active_ads == 5
    assert snapshot.priced_ads == 5
    assert snapshot.price_median == 500_000_000
    assert snapshot.null_rates["transmission"] == 0.0
    assert snapshot.distinct_counts["transmission"] == 1


@pytest.mark.django_db
def test_no_alarms_without_enough_history(catalog):
    make_ads(catalog, 5)
    seed_stable_history(days=D.MIN_HISTORY_DAYS - 1)

    _, alarms = D.run_drift_check(TODAY)

    assert alarms == [], "two data points is not a baseline"


@pytest.mark.django_db
def test_a_stable_population_raises_nothing(catalog):
    seed_stable_history()
    make_ads(catalog, 1000)
    D.build_snapshot(TODAY)
    snapshot = DataQualitySnapshot.objects.get(date=TODAY)
    snapshot.null_rates = {"transmission": 0.02, "publish_at": 0.01}
    snapshot.distinct_counts = {"transmission": 3}
    snapshot.flag_counts = {"price_too_high": 10}
    snapshot.save()

    assert D.detect_drift(snapshot) == []


@pytest.mark.django_db
def test_a_field_going_empty_is_caught(catalog):
    """The schema-change signature: no rule fires, the column just stops being
    populated, and every statistic that groups by it quietly changes meaning."""
    seed_stable_history()
    make_ads(catalog, 1000, transmission="")
    snapshot = D.build_snapshot(TODAY)
    snapshot.null_rates = {"transmission": 0.85, "publish_at": 0.01}
    snapshot.save()

    alarms = D.detect_drift(snapshot)

    assert any(a["metric"] == "null_rate.transmission" for a in alarms)


@pytest.mark.django_db
def test_a_collapse_in_variety_is_caught(catalog):
    """Worse than nulls: the parser writes one constant, so the field looks
    populated while carrying no information at all."""
    seed_stable_history()
    make_ads(catalog, 1000)
    snapshot = D.build_snapshot(TODAY)
    snapshot.distinct_counts = {"transmission": 1}
    snapshot.save()

    alarms = D.detect_drift(snapshot)

    assert any(a["metric"] == "distinct.transmission" for a in alarms)


@pytest.mark.django_db
def test_a_spike_in_minted_dimensions_is_caught(catalog):
    """The catalog-pollution alarm: individual unknown_dimension flags are normal,
    a cliff of them means the title format moved."""
    seed_stable_history()
    make_ads(catalog, 1000)
    snapshot = D.build_snapshot(TODAY)
    snapshot.unconfirmed_models = 300
    snapshot.save()

    alarms = D.detect_drift(snapshot)

    assert any(a["metric"] == "unconfirmed_models" for a in alarms)


@pytest.mark.django_db
def test_a_flag_rate_spike_is_caught(catalog):
    seed_stable_history()
    make_ads(catalog, 1000)
    snapshot = D.build_snapshot(TODAY)
    snapshot.flag_counts = {"price_too_high": 600}
    snapshot.save()

    alarms = D.detect_drift(snapshot)

    assert any(a["metric"] == "flag_rate.price_too_high" for a in alarms)


@pytest.mark.django_db
def test_flag_counts_are_compared_as_rates_not_counts(catalog):
    """Inventory size moves for ordinary reasons. Comparing raw counts would fire
    every flag metric at once on a busy day and teach the operator to ignore it."""
    seed_stable_history()
    make_ads(catalog, 2000)
    snapshot = D.build_snapshot(TODAY)
    snapshot.active_ads = 2000
    snapshot.flag_counts = {"price_too_high": 20}  # same 1% rate, double the count
    snapshot.save()

    alarms = D.detect_drift(snapshot)

    assert not any(a["metric"] == "flag_rate.price_too_high" for a in alarms)


@pytest.mark.django_db
def test_a_drifting_baseline_does_not_absorb_the_drift(catalog):
    """Why median and MAD rather than a rolling mean.

    If the baseline absorbed bad days, a problem that persists would become the
    new normal and the alarm would switch itself off exactly while the fault is
    still live. With a median baseline, a minority of bad days cannot move it.
    """
    for i in range(10, 3, -1):
        DataQualitySnapshot.objects.create(
            date=TODAY - timedelta(days=i), active_ads=1000,
            null_rates={"transmission": 0.02}, distinct_counts={},
            flag_counts={}, price_median=500_000_000,
        )
    for i in range(3, 0, -1):  # three consecutive bad days
        DataQualitySnapshot.objects.create(
            date=TODAY - timedelta(days=i), active_ads=1000,
            null_rates={"transmission": 0.85}, distinct_counts={},
            flag_counts={}, price_median=500_000_000,
        )
    snapshot = DataQualitySnapshot.objects.create(
        date=TODAY, active_ads=1000, null_rates={"transmission": 0.85},
        distinct_counts={}, flag_counts={}, price_median=500_000_000,
    )

    alarms = D.detect_drift(snapshot)

    assert any(a["metric"] == "null_rate.transmission" for a in alarms), (
        "the alarm stopped firing while the fault was still present"
    )


def test_level_metrics_are_judged_on_their_own_scale():
    """Metrics in one table span orders of magnitude — 3 distinct transmission
    values next to 60,000 active ads. A single absolute spread floor is
    necessarily deafening for one and blind for the other, so the floor for level
    metrics is a fraction of their own baseline.

    Pure function, no DB: this is arithmetic about the scale rule itself.
    """
    tiny = D._spread(baseline=3.0, mad=0.0, family=D.LEVEL)
    huge = D._spread(baseline=60_000.0, mad=0.0, family=D.LEVEL)

    assert abs(1.0 - 3.0) / tiny > D.DRIFT_SIGMA, "a cardinality collapse must alarm"
    assert abs(59_500 - 60_000) / huge < D.DRIFT_SIGMA, "ordinary churn must not"


@pytest.mark.django_db
def test_snapshot_is_idempotent_for_a_date(catalog):
    make_ads(catalog, 5)
    D.build_snapshot(TODAY)
    D.build_snapshot(TODAY)

    assert DataQualitySnapshot.objects.filter(date=TODAY).count() == 1


@pytest.mark.django_db
def test_the_verdict_is_persisted_on_the_row(catalog):
    seed_stable_history()
    make_ads(catalog, 1000)
    snapshot, alarms = D.run_drift_check(TODAY)

    assert DataQualitySnapshot.objects.get(date=TODAY).alarms == alarms
