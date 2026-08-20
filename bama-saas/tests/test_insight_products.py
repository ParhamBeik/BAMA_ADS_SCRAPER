"""The three insight products: liquidity, fair price, value retention.

Test type: unit for the Kaplan-Meier estimator (pure arithmetic, checked against
a hand-computed curve) and integration for everything else, since each is an
aggregate over stored rows.

The tests to read first are ``test_censoring_is_what_makes_this_different`` and
``test_fair_price_explains_itself``. They pin the two properties that separate
these from the naive versions they replace: a survival estimate that does not
throw away the slow half of the market, and a valuation a user can argue with.
"""

from datetime import datetime, timedelta, timezone

import pytest

from apps.core.models import Ad, Brand, City, ListingEpisode, Model, Variant
from apps.core import pricing as FP
from apps.core import research as L
from apps.core import research as R

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _episodes_are_trustworthy(settings):
    """Pin the clean-start cutoff behind these fixtures' episode dates.

    Survival only counts episodes started after removal detection became
    reliable (``BAMA_EPISODE_CLEAN_START``). These tests are about the
    Kaplan-Meier arithmetic, not that cutoff, so they opt out of it — the cutoff
    itself is covered by ``test_survival_excludes_episodes_from_the_dirty_era``.
    """
    settings.BAMA_EPISODE_CLEAN_START = "2000-01-01"


@pytest.fixture
def catalog(db):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    model = Model.objects.create(brand=brand, name_fa="207", is_confirmed=True)
    return {
        "brand": brand, "model": model,
        "variant": Variant.objects.create(model=model, name_fa="پانوراما"),
        "city": City.objects.create(name_fa="تهران"),
        "city2": City.objects.create(name_fa="مشهد"),
    }


def make_ad(catalog, code, *, price=1_000_000_000, mileage=100_000, year=1400,
            city=None, status=Ad.Status.ACTIVE, first_seen=None):
    return Ad.objects.create(
        code=code, brand=catalog["brand"], model=catalog["model"],
        variant=catalog["variant"], city=city or catalog["city"],
        year_jalali=year, mileage=mileage, current_price=price, status=status,
        first_seen_at=first_seen or NOW - timedelta(days=30),
        last_seen_at=NOW, publish_at=first_seen or NOW - timedelta(days=30),
    )


def make_episode(ad, *, started, ended=None, price=None):
    return ListingEpisode.objects.create(
        ad=ad, started_at=started, ended_at=ended,
        first_price=price or ad.current_price, last_price=price or ad.current_price,
    )


# --- Kaplan-Meier -----------------------------------------------------------

def test_kaplan_meier_matches_a_hand_computed_curve():
    """Four listings, delisted on days 1, 2, 3, 4. At each step the survivor
    fraction is 1 - 1/at_risk, so the curve is 3/4, 1/2, 1/4, 0."""
    observations = [L.Observation(days=d, delisted=True) for d in (1, 2, 3, 4)]

    curve = L.kaplan_meier(observations)

    assert curve.survival == pytest.approx([0.75, 0.5, 0.25, 0.0])
    assert curve.median_days() == 2


def test_censoring_is_what_makes_this_different():
    """The property the naive average gets wrong.

    Ten cars: five delisted quickly, five still listed after a long time. The
    naive mean over *finished* listings sees only the fast five and reports a
    small number. The estimator keeps the slow five in the risk set, so survival
    never falls to 0.5 and the honest answer is "we do not know yet".

    This bias is one-directional and worst exactly when it matters: the slower the
    market, the more of it is unfinished and excluded.
    """
    fast = [L.Observation(days=d, delisted=True) for d in (1, 2, 3, 4, 5)]
    slow = [L.Observation(days=90, delisted=False) for _ in range(5)]

    naive = sum(o.days for o in fast) / len(fast)
    curve = L.kaplan_meier(fast + slow)

    assert naive == 3.0, "averaging only the finished listings"
    assert curve.censored == 5
    assert curve.median_days() == 5, "the still-listed cars hold the curve up"
    assert curve.median_days() > naive

    # Push the censoring further and the honest answer stops being a number at
    # all: with most of the market unfinished, survival never reaches 0.5 and
    # anything reported would be extrapolation.
    mostly_open = [L.Observation(days=d, delisted=True) for d in (1, 2, 3)] + [
        L.Observation(days=90, delisted=False) for _ in range(7)
    ]
    assert L.kaplan_meier(mostly_open).median_days() is None


def test_a_censored_listing_never_counts_as_a_delisting():
    only_open = [L.Observation(days=10, delisted=False) for _ in range(5)]

    curve = L.kaplan_meier(only_open)

    assert curve.delisted == 0
    assert curve.survival == []
    assert curve.median_days() is None


@pytest.mark.django_db
def test_survival_refuses_a_thin_cohort(catalog):
    for i in range(3):
        make_episode(make_ad(catalog, f"thin{i:04d}"), started=NOW - timedelta(days=10))

    result = L.survival(model_id=catalog["model"].pk)

    assert result["available"] is False
    assert result["reason"] == "insufficient_episodes"


@pytest.mark.django_db
def test_survival_reports_the_naive_number_alongside(catalog):
    """Shown, not asserted: the user can see the difference the method makes."""
    for i in range(15):
        ad = make_ad(catalog, f"done{i:04d}", status=Ad.Status.REMOVED)
        make_episode(ad, started=NOW - timedelta(days=20), ended=NOW - timedelta(days=17))
    for i in range(15):
        make_episode(make_ad(catalog, f"open{i:04d}"), started=NOW - timedelta(days=60))

    result = L.survival(model_id=catalog["model"].pk)

    assert result["available"] is True
    assert result["naive_mean_days_finished_only"] == pytest.approx(3.0, abs=0.2)
    assert result["censored"] == 15


@pytest.mark.django_db
def test_very_short_episodes_are_ignored(catalog):
    """A listing that appears and vanishes within hours is far more often a
    posting error or a moderation removal than a car that sold the same morning."""
    for i in range(25):
        ad = make_ad(catalog, f"blip{i:04d}", status=Ad.Status.REMOVED)
        make_episode(ad, started=NOW - timedelta(hours=2), ended=NOW - timedelta(hours=1))

    result = L.survival(model_id=catalog["model"].pk)

    assert result["available"] is False


# --- fair price -------------------------------------------------------------

@pytest.mark.django_db
def test_fair_price_explains_itself(catalog):
    """The reason this replaced a bare score: a number a user can argue with is
    worth more than one they cannot."""
    for i in range(20):
        make_ad(catalog, f"peer{i:04d}", price=1_000_000_000 + i * 1_000_000)
    make_ad(catalog, "target01", price=900_000_000)

    result = FP.fair_price("target01")

    assert result["available"] is True
    assert result["fair_value"] > 0
    assert [c["name"] for c in result["components"]][0] == "cohort_median"
    assert result["gap_pct"] < 0, "asking below fair value"
    assert result["confidence"] in {"low", "medium", "high"}


@pytest.mark.django_db
def test_fair_price_refuses_a_thin_cohort_rather_than_guessing(catalog):
    make_ad(catalog, "lonely01")

    result = FP.fair_price("lonely01")

    assert result["available"] is False
    assert result["reason"] == "insufficient_peers"


@pytest.mark.django_db
def test_fair_price_confidence_tracks_the_evidence(catalog):
    for i in range(10):
        make_ad(catalog, f"few{i:05d}", price=1_000_000_000)
    make_ad(catalog, "subject1", price=1_000_000_000)

    result = FP.fair_price("subject1")

    assert result["confidence"] == "low"
    assert result["peer_count"] == 11


@pytest.mark.django_db
def test_an_outlier_peer_does_not_set_the_fair_value(catalog):
    """Outliers are excluded from the baseline that judges believability — the
    same rule the cohort pass applies, honoured here."""
    for i in range(20):
        make_ad(catalog, f"norm{i:04d}", price=1_000_000_000)
    absurd = make_ad(catalog, "absurd01", price=90_000_000_000)
    Ad.objects.filter(code=absurd.code).update(cohort_flags=["price_outlier_high"])
    make_ad(catalog, "subject2", price=1_000_000_000)

    result = FP.fair_price("subject2")

    assert result["fair_value"] == pytest.approx(1_000_000_000, rel=0.05)


# --- retention --------------------------------------------------------------

@pytest.mark.django_db
def test_depreciation_curve_is_medians_not_a_fitted_line(catalog):
    """Cars lose value fastest early and flatten later, so a straight line
    overcharges high-mileage cars and undercharges low-mileage ones. A table of
    medians assumes no shape at all."""
    for year, price in ((1398, 600_000_000), (1400, 800_000_000), (1402, 1_000_000_000)):
        for i in range(10):
            make_ad(catalog, f"y{year}n{i:03d}", price=price + i * 1_000_000, year=year)

    curve = R.depreciation_curve(catalog["model"].pk)

    assert curve["available"] is True
    assert [p["year_jalali"] for p in curve["points"]] == [1398, 1400, 1402]
    assert curve["points"][0]["pct_of_newest"] == pytest.approx(60.0, abs=1.0)
    assert curve["reference_year"] == 1402


@pytest.mark.django_db
def test_a_thin_year_is_dropped_not_guessed(catalog):
    for i in range(10):
        make_ad(catalog, f"solid{i:04d}", year=1400)
    make_ad(catalog, "sparse01", year=1390)

    curve = R.depreciation_curve(catalog["model"].pk)

    assert curve["available"] is False, "one year with data is not a curve"


@pytest.mark.django_db
def test_survival_excludes_episodes_from_the_dirty_era(catalog, settings):
    """Episodes predating reliable removal detection are not evidence.

    Their end dates record when a sweep happened to finish, not when the car
    left the feed: endings landed on 17 of 39 days in lumps of up to 6,873, and
    every cohort of every model then returned a median of exactly 21.02 days.
    Excluded rather than deleted — they stay for provenance.
    """
    settings.BAMA_EPISODE_CLEAN_START = "2026-08-01"

    # Plenty of episodes, all from before the cutoff.
    for i in range(40):
        ad = make_ad(catalog, f"dirty{i:03d}", status=Ad.Status.REMOVED)
        make_episode(
            ad,
            started=datetime(2026, 7, 1, tzinfo=timezone.utc),
            ended=datetime(2026, 7, 22, tzinfo=timezone.utc),
        )

    result = L.survival(model_id=catalog["model"].pk)

    assert result["available"] is False
    assert result["reason"] == "insufficient_clean_history"
    assert result["n"] == 0
    assert result["excluded_episodes"] == 40
    assert result["clean_start"] == "2026-08-01"
