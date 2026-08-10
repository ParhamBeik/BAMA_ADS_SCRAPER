"""Cohort-relative outlier detection.

Test type: integration — the whole subject is one ad's price measured against a
population of peers, so a unit test over a bare list would test statistics rather
than the behaviour.

The load-bearing test here is ``test_the_outlier_does_not_move_the_baseline``.
Everything else in this module is downstream of that one property: if a single
extreme value can widen its own detection band, the layer silently stops working
precisely when it is needed most.
"""

from datetime import datetime, timezone

import pytest

from apps.core.models import Ad, Brand, City, Model, Variant
from apps.core.services.quality import COHORT_FLAGS, without_cohort_outliers
from apps.jobs.services import verify_cohort as VC

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def cohort(db):
    brand = Brand.objects.create(slug="saipa", name_fa="سایپا", is_confirmed=True)
    model = Model.objects.create(brand=brand, name_fa="پراید", is_confirmed=True)
    variant = Variant.objects.create(model=model, name_fa="۱۳۱")
    city = City.objects.create(name_fa="تهران")
    return {"brand": brand, "model": model, "variant": variant, "city": city}


def make_ad(cohort, code, price, *, mileage=100_000, year=1399):
    return Ad.objects.create(
        code=code,
        brand=cohort["brand"],
        model=cohort["model"],
        variant=cohort["variant"],
        city=cohort["city"],
        year_jalali=year,
        mileage=mileage,
        current_price=price,
        status=Ad.Status.ACTIVE,
        first_seen_at=NOW,
        last_seen_at=NOW,
        publish_at=NOW,
    )


def seed_normal_cohort(cohort, n=20, base=500_000_000):
    """A tight, realistic spread — prices within a few percent of each other."""
    for i in range(n):
        make_ad(cohort, f"normal{i:03d}", base + i * 2_000_000)


@pytest.mark.django_db
def test_a_plausible_price_is_flagged_when_its_peers_disagree(cohort):
    """The case no per-field rule can reach: 4bn toman is inside
    MIN/MAX_PLAUSIBLE_PRICE, and on a first sighting there is no transition to
    compare against. Only the cohort knows."""
    seed_normal_cohort(cohort)
    make_ad(cohort, "pricey01", 4_000_000_000)

    VC.flag_cohort_outliers()

    assert Ad.objects.get(code="pricey01").cohort_flags == [VC.FLAG_HIGH]


@pytest.mark.django_db
def test_normal_listings_are_not_flagged(cohort):
    seed_normal_cohort(cohort)
    VC.flag_cohort_outliers()

    assert not Ad.objects.exclude(code="").filter(cohort_flags__len__gt=0).exists()


@pytest.mark.django_db
def test_a_suspiciously_cheap_listing_is_flagged_low(cohort):
    seed_normal_cohort(cohort)
    make_ad(cohort, "cheap001", 20_000_000)

    VC.flag_cohort_outliers()

    assert Ad.objects.get(code="cheap001").cohort_flags == [VC.FLAG_LOW]


@pytest.mark.django_db
def test_the_outlier_does_not_move_the_baseline(cohort):
    """Why median/MAD and not mean/stddev.

    A mean is dragged toward an extreme value and a standard deviation is
    inflated by it, so the outlier widens the band meant to catch it — the more
    extreme the value, the better it hides. Median and MAD both have a 50%
    breakdown point, so the verdict on every *other* ad must be identical whether
    or not the outlier is present.
    """
    seed_normal_cohort(cohort)
    VC.flag_cohort_outliers()
    before = set(Ad.objects.filter(cohort_flags__contains=[VC.FLAG_HIGH]).values_list("code", flat=True))

    make_ad(cohort, "extreme1", 90_000_000_000)
    VC.flag_cohort_outliers()
    after = set(Ad.objects.filter(cohort_flags__contains=[VC.FLAG_HIGH]).values_list("code", flat=True))

    assert after - before == {"extreme1"}, "an outlier changed the verdict on its peers"


@pytest.mark.django_db
def test_statistically_extreme_but_ordinary_prices_are_left_alone(cohort):
    """Found against the live database, not by reasoning.

    The z-score alone flagged 6.2% of the market, and 663 of those listings sat
    within 1.5x of their own cohort median. MAD measures how tightly sellers
    cluster, so in a tight cohort — everyone within a few percent of 500M — a car
    20% above is statistically extreme and economically unremarkable. Excluding
    those from every baseline would discard a slice of the real market.

    This cohort is deliberately tight, so the odd listing is many MADs out while
    still being an entirely believable price.
    """
    for i in range(30):
        make_ad(cohort, f"tight{i:03d}", 500_000_000 + i * 500_000)
    make_ad(cohort, "pricey02", 620_000_000)  # +24%: many MADs, ordinary money

    VC.flag_cohort_outliers()

    assert Ad.objects.get(code="pricey02").cohort_flags == []


@pytest.mark.django_db
def test_thin_cohorts_are_left_alone(cohort):
    """A MAD over a handful of prices is noise, and most of the long tail is thin
    — flagging against it would invent outliers everywhere."""
    for i in range(VC.MIN_COHORT_PEERS - 1):
        make_ad(cohort, f"thin{i:04d}", 500_000_000)
    make_ad(cohort, "thinodd1", 9_000_000_000)

    VC.flag_cohort_outliers()

    assert Ad.objects.get(code="thinodd1").cohort_flags == []


@pytest.mark.django_db
def test_a_cohort_with_no_spread_is_left_alone(cohort):
    """MAD of zero: more than half the cohort shares one price, which happens
    whenever sellers round to the same number. Any deviation would otherwise
    score as infinitely far out."""
    for i in range(20):
        make_ad(cohort, f"same{i:04d}", 500_000_000)
    make_ad(cohort, "sameodd1", 520_000_000)

    VC.flag_cohort_outliers()

    assert Ad.objects.get(code="sameodd1").cohort_flags == []


@pytest.mark.django_db
def test_high_mileage_alone_is_not_an_outlier(cohort):
    """The cohort key says nothing about the odometer, so without the mileage
    adjustment a worn car reads as underpriced — a real price difference with a
    boring explanation."""
    for i in range(20):
        make_ad(cohort, f"miles{i:03d}", 500_000_000 + i * 2_000_000, mileage=100_000)
    make_ad(cohort, "worn0001", 250_000_000, mileage=600_000)

    VC.flag_cohort_outliers()

    worn = Ad.objects.get(code="worn0001")
    assert VC.FLAG_LOW not in worn.cohort_flags


@pytest.mark.django_db
def test_flags_clear_when_the_cohort_moves(cohort):
    """Recompute, not accumulate: an ad stops being an outlier when its peers
    catch up, and an add-only flag would eventually mark the whole market."""
    seed_normal_cohort(cohort)
    make_ad(cohort, "moving01", 4_000_000_000)
    VC.flag_cohort_outliers()
    assert Ad.objects.get(code="moving01").cohort_flags == [VC.FLAG_HIGH]

    Ad.objects.filter(code="moving01").update(current_price=505_000_000)
    VC.flag_cohort_outliers()

    assert Ad.objects.get(code="moving01").cohort_flags == []


@pytest.mark.django_db
def test_the_outlier_stays_in_the_catalog(cohort):
    """A flag is not a deletion. The suspiciously cheap car is the single most
    valuable thing this product finds; hiding it would defeat the purpose."""
    seed_normal_cohort(cohort)
    make_ad(cohort, "bargain1", 20_000_000)
    VC.flag_cohort_outliers()

    assert Ad.objects.filter(code="bargain1").exists()
    assert Ad.objects.get(code="bargain1").quality_flags == [], "not a quality verdict"


@pytest.mark.django_db
def test_baselines_exclude_outliers_but_the_catalog_does_not(cohort):
    seed_normal_cohort(cohort)
    make_ad(cohort, "bargain2", 20_000_000)
    VC.flag_cohort_outliers()

    assert Ad.objects.filter(code="bargain2").exists()
    assert not without_cohort_outliers(Ad.objects).filter(code="bargain2").exists()


@pytest.mark.django_db
def test_unrelated_flags_survive_a_recompute(cohort):
    seed_normal_cohort(cohort)
    make_ad(cohort, "other001", 4_000_000_000)
    Ad.objects.filter(code="other001").update(cohort_flags=["something_else"])

    VC.flag_cohort_outliers()

    assert set(Ad.objects.get(code="other001").cohort_flags) == {
        "something_else", VC.FLAG_HIGH,
    }


@pytest.mark.django_db
def test_scoping_to_one_model_leaves_others_untouched(cohort, django_assert_num_queries):
    other_brand = Brand.objects.create(slug="ikco", name_fa="ایران خودرو", is_confirmed=True)
    other_model = Model.objects.create(brand=other_brand, name_fa="سمند", is_confirmed=True)
    other_variant = Variant.objects.create(model=other_model, name_fa="ال ایکس")
    seed_normal_cohort(cohort)
    make_ad(cohort, "inscope1", 4_000_000_000)

    other = {**cohort, "brand": other_brand, "model": other_model, "variant": other_variant}
    for i in range(20):
        make_ad(other, f"oth{i:05d}", 500_000_000 + i * 2_000_000)
    make_ad(other, "outscope", 4_000_000_000)

    VC.flag_cohort_outliers(model_id=cohort["model"].pk)

    assert Ad.objects.get(code="inscope1").cohort_flags == [VC.FLAG_HIGH]
    assert Ad.objects.get(code="outscope").cohort_flags == []


@pytest.mark.django_db
def test_cohort_flags_are_not_quality_flags(cohort):
    """Two different statements: quality_flags judges the row, cohort_flags judges
    it against peers. They also have different lifecycles — quality_flags is
    rewritten on every observation, which would erase an async cohort verdict."""
    from apps.jobs.services.verify import HARD_RULE_IDS

    assert not COHORT_FLAGS & HARD_RULE_IDS
