"""The learned layer.

Structured so that most of it runs without fitting anything: the metrics, the
feature builder, the time split and the promotion gate are pure functions over
plain data, and those are where the interesting mistakes live. Only two tests
actually train a model, because a LightGBM fit in a unit test is a second of CPU
and a source of flakiness, and neither is worth paying eleven times.

The tests that matter most are the ones that assert a model is *refused*. A
suite that only checks the happy path would have passed on every version of this
code that shipped a broken model, including the one whose 80% interval contained
43% of held-out cars.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest
from django.utils import timezone as djtz

from apps.core.models import Ad, Brand, City, ListingEpisode, Model, Variant
from apps.ml import features, metrics, registry
from apps.ml.models import AdPrediction, MLModel

NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)


# ===========================================================================
# Metrics — pure, no database, no fitting
# ===========================================================================


def test_mape_and_median_ape_disagree_when_a_few_rows_are_catastrophic():
    """The reason both are reported. Nine rows are perfect and one is 100% out:
    the median says the model is excellent and the mean says it is not, and the
    gap between them is the finding — a few disasters, not a uniform wobble."""
    actual = [100.0] * 10
    predicted = [100.0] * 9 + [200.0]
    assert metrics.median_ape(actual, predicted) == 0.0
    assert metrics.mape(actual, predicted) == pytest.approx(10.0)


def test_mape_skips_rows_with_no_true_value_rather_than_dividing_by_zero():
    assert metrics.mape([0, 100.0], [50.0, 110.0]) == pytest.approx(10.0)
    assert metrics.mape([0, 0], [1.0, 1.0]) is None


def test_pinball_loss_is_asymmetric_in_the_direction_the_quantile_asks_for():
    """At alpha=0.1, over-predicting is punished nine times as hard as
    under-predicting. That asymmetry is the entire mechanism by which the fitted
    line lands on a 10th percentile instead of a mean."""
    under = metrics.pinball_loss([100.0], [90.0], 0.1)   # prediction below truth
    over = metrics.pinball_loss([100.0], [110.0], 0.1)   # prediction above truth
    assert under == pytest.approx(0.1 * 10)
    assert over == pytest.approx(0.9 * 10)
    assert over == pytest.approx(under * 9)


def test_interval_coverage_refuses_on_a_sample_too_small_to_mean_anything():
    """At n=10 an 80% band containing 6 rows is entirely ordinary, so reporting
    "60% coverage" invites a decision nobody should make on that evidence."""
    assert metrics.interval_coverage([1.0] * 10, [0.0] * 10, [2.0] * 10) is None
    n = metrics.MIN_EVAL_ROWS
    assert metrics.interval_coverage([1.0] * n, [0.0] * n, [2.0] * n) == 100.0


def test_interval_coverage_counts_the_boundary_as_inside():
    n = metrics.MIN_EVAL_ROWS
    assert metrics.interval_coverage([1.0] * n, [1.0] * n, [1.0] * n) == 100.0


def test_brier_scores_a_confident_wrong_forecast_worse_than_an_uncertain_one():
    assert metrics.brier_score([1], [0.0]) == pytest.approx(1.0)
    assert metrics.brier_score([1], [0.5]) == pytest.approx(0.25)
    assert metrics.brier_score([1], [1.0]) == pytest.approx(0.0)


def test_reliability_curve_omits_empty_bins_rather_than_reporting_them_as_zero():
    """An empty bin means "the model never said that"; a bin with an observed
    rate of 0 means "it said that and was always wrong". Drawing them the same
    way puts a false point on the calibration plot."""
    curve = metrics.reliability_curve([1, 0, 1, 1], [0.95, 0.05, 0.92, 0.91])
    assert [b["bin_lower"] for b in curve] == [0.0, 0.9]
    high = curve[-1]
    assert high["n"] == 3
    assert high["observed"] == pytest.approx(1.0)


def test_expected_calibration_error_is_zero_for_a_forecaster_that_means_it():
    """Ten rows at p=0.5 of which exactly five happened."""
    labels = [1] * 5 + [0] * 5
    assert metrics.expected_calibration_error(labels, [0.5] * 10) == pytest.approx(0.0)


def test_precision_at_k_reports_the_base_rate_beside_the_precision():
    """30% precision is excellent at an 8% base rate and worthless at 40%. The
    number the gate reads is the ratio, not the precision."""
    scores = [0.9, 0.8, 0.1, 0.05]
    labels = [1, 1, 0, 0]
    out = metrics.precision_at_k(scores, labels, k=2)
    assert out == {"k": 2, "precision": 1.0, "base_rate": 0.5, "lift": 2.0}


def test_psi_is_near_zero_for_an_unchanged_distribution_and_large_for_a_shift():
    stable = list(range(200))
    assert metrics.population_stability_index(stable, list(range(200))) < 0.1
    shifted = [v + 500 for v in stable]
    assert metrics.population_stability_index(stable, shifted) > 0.25


def test_psi_refuses_a_constant_feature_instead_of_dividing_by_a_zero_width_bin():
    assert metrics.population_stability_index([5.0] * 100, [5.0] * 100) is None


def test_psi_survives_a_bin_that_is_empty_on_one_side():
    """The formula takes a log of a ratio, so an empty bin is an infinity unless
    it is floored. Empty bins are the normal case under real drift."""
    value = metrics.population_stability_index(list(range(100)), [0.0] * 100)
    assert value is not None and math.isfinite(value)


# ===========================================================================
# Features
# ===========================================================================


def _row(**over) -> dict:
    base = {
        "code": "a1", "brand_id": 1, "model_id": 2, "variant_id": 3, "city_id": 4,
        "dealer_id": None, "current_price": 1_000_000_000, "mileage": 50_000,
        "year_jalali": 1400, "body_status": "بدون رنگ", "body_type": "هاچبک",
        "fuel": "بنزینی", "transmission": "دنده ای", "image_count": 5,
        "description_length": 200, "seller_authenticated": True,
        "publish_at": NOW - timedelta(days=10), "first_seen_at": NOW - timedelta(days=10),
    }
    return {**base, **over}


def test_a_category_never_seen_at_fit_time_encodes_to_unseen_not_to_zero():
    """A new trim appears on Bama every week. Collapsing it into whichever
    category happened to be code 0 is a silent, plausible wrong answer — the
    model would price it as some real, unrelated trim."""
    spec = features.fit_spec([_row(variant_id=3)], now=NOW)
    vector = features.row_features(_row(variant_id=999), spec, now=NOW)
    index = spec.columns.index("variant_id")
    assert vector[index] == features.UNSEEN


def test_missing_mileage_is_nan_because_zero_kilometres_is_a_real_value():
    """~33% of this catalogue genuinely reads صفر کیلومتر. Filling a missing
    mileage with 0 makes those two indistinguishable, and LightGBM routes NaN
    down its own learned branch anyway."""
    spec = features.fit_spec([_row()], now=NOW)
    index = spec.columns.index("mileage")
    assert math.isnan(features.row_features(_row(mileage=None), spec, now=NOW)[index])
    assert features.row_features(_row(mileage=0), spec, now=NOW)[index] == 0.0


def test_condition_is_an_ordinal_in_the_measured_haircut_order():
    """Clean < cosmetic < painted < structural. A tree can split an ordinal
    anywhere, so nothing is lost, and the monotone relationship arrives for free
    instead of having to be rediscovered from four indicators."""
    spec = features.fit_spec([_row()], now=NOW)
    index = spec.columns.index("condition_ordinal")

    def ordinal(status):
        return features.row_features(_row(body_status=status), spec, now=NOW)[index]

    assert ordinal("بدون رنگ") < ordinal("دو لکه رنگ") < ordinal("تصادفی")


def test_an_unrecognised_body_status_is_nan_not_clean():
    """Guessing "clean" on an unknown string is the bug `condition_band` exists
    to prevent, and it would price a damaged car as an undamaged one."""
    spec = features.fit_spec([_row()], now=NOW)
    index = spec.columns.index("condition_ordinal")
    assert math.isnan(features.row_features(_row(body_status="???"), spec, now=NOW)[index])


def test_seller_authenticated_keeps_three_states():
    """True, False, and "Bama did not say" — the third is common, and folding it
    into False would claim every quiet listing is unverified."""
    spec = features.fit_spec([_row()], now=NOW)
    i = spec.columns.index("seller_authenticated")
    assert features.row_features(_row(seller_authenticated=True), spec, now=NOW)[i] == 1.0
    assert features.row_features(_row(seller_authenticated=False), spec, now=NOW)[i] == 0.0
    assert math.isnan(
        features.row_features(_row(seller_authenticated=None), spec, now=NOW)[i])


def test_age_is_computed_in_the_jalali_calendar():
    """`year_jalali` is a Jalali number. Subtracting it from a Gregorian year
    would make every car about six centuries old."""
    spec = features.fit_spec([_row()], now=NOW)
    i = spec.columns.index("age_years")
    age = features.row_features(_row(year_jalali=1400), spec, now=NOW)[i]
    assert 0 <= age < 20


def test_the_feature_spec_survives_a_round_trip_through_json():
    """Vocabularies are keyed by database ids, and JSON turns every key into a
    string. Without the explicit coercion in `to_json`/`row_features`, every id
    would miss its own vocabulary entry on the way back in and the whole
    catalogue would encode as UNSEEN."""
    spec = features.fit_spec([_row(model_id=7), _row(model_id=8)], now=NOW)
    restored = features.FeatureSpec.from_json(spec.to_json())
    index = spec.columns.index("model_id")
    original = features.row_features(_row(model_id=7), spec, now=NOW)[index]
    round_tripped = features.row_features(_row(model_id=7), restored, now=NOW)[index]
    assert original == round_tripped != features.UNSEEN


def test_categorical_indices_point_at_the_categorical_columns():
    spec = features.fit_spec([_row()], now=NOW)
    named = {spec.columns[i] for i in spec.categorical_indices}
    assert named == set(features.CATEGORICAL_COLUMNS)


# ===========================================================================
# The time split
# ===========================================================================


def test_the_split_cuts_on_a_timestamp_so_one_instant_cannot_land_on_both_sides():
    """Dealers upload in bulk and reposts duplicate a car under a fresh code.
    Cutting at a row *index* would put two rows from the same second on opposite
    sides of the boundary — the same car, once in each half, which is a leak
    that flatters every metric downstream."""
    from apps.ml.train import time_split

    same_instant = NOW - timedelta(days=1)
    rows = ([{"publish_at": NOW - timedelta(days=d)} for d in range(20, 2, -1)]
            + [{"publish_at": same_instant} for _ in range(6)])
    train, holdout = time_split(rows, 0.2)
    assert train and holdout
    assert max(r["publish_at"] for r in train) < min(r["publish_at"] for r in holdout)


def test_the_split_is_empty_on_empty_input_rather_than_raising():
    from apps.ml.train import time_split

    assert time_split([]) == ([], [])


# ===========================================================================
# Conformal calibration
# ===========================================================================


def test_conformal_widening_repairs_a_band_that_is_too_narrow():
    """The failure this exists for. A fitted band covering ~30% of a calibration
    set is widened by the empirical quantile of how far outside it rows fell,
    and the widened band covers ~80%. Naive quantile regression intervals are
    anti-conservative out of sample; this is the standard repair and it assumes
    nothing about the distribution."""
    import numpy as np

    from apps.ml.train import _conformal_delta

    rng = np.random.default_rng(0)
    y = rng.normal(0.0, 1.0, 800)
    lower, upper = np.full(800, -0.4), np.full(800, 0.4)  # far too tight
    delta = _conformal_delta(y, lower, upper, alpha=0.2)
    before = float(np.mean((y >= lower) & (y <= upper)))
    after = float(np.mean((y >= lower - delta) & (y <= upper + delta)))
    assert before < 0.4
    assert 0.78 <= after <= 0.90


def test_conformal_delta_is_negative_when_the_band_was_already_too_wide():
    """Not clamped at zero. A band that covers 100% at alpha=0.2 is wider than
    it needs to be, and saying so is information — an interval nobody can fall
    outside is not a measurement."""
    import numpy as np

    from apps.ml.train import _conformal_delta

    y = np.zeros(200)
    delta = _conformal_delta(y, np.full(200, -10.0), np.full(200, 10.0), alpha=0.2)
    assert delta < 0


# ===========================================================================
# The promotion gate
# ===========================================================================


def test_a_challenger_must_beat_the_baseline_as_well_as_the_incumbent():
    """The whole reason the gate exists. Beating only the model it replaces is
    how a line of models drifts away from something simpler that was always
    better — which is what `apps/core/pricing.py` records happening here once."""
    decision = registry.gate(challenger=8.0, incumbent=9.0, baseline=7.0)
    assert decision["promote"] is False
    assert decision["reason"] == "loses_to_baseline"


def test_a_challenger_that_beats_both_is_promoted():
    decision = registry.gate(challenger=5.0, incumbent=9.0, baseline=7.0)
    assert decision["promote"] is True
    assert decision["reason"] == "beats_incumbent_and_baseline"


def test_the_margin_refuses_a_swap_that_is_only_noise():
    """Every promotion invalidates caches and changes numbers a reader may have
    screenshotted. A 0.5% improvement is not worth that."""
    assert registry.gate(challenger=9.95, incumbent=10.0, baseline=20.0,
                         margin=0.02)["promote"] is False
    assert registry.gate(challenger=9.0, incumbent=10.0, baseline=20.0,
                         margin=0.02)["promote"] is True


def test_higher_is_better_metrics_are_compared_the_other_way_round():
    assert registry.gate(challenger=0.9, incumbent=0.5, baseline=0.5,
                         lower_is_better=False)["promote"] is True
    assert registry.gate(challenger=0.4, incumbent=0.5, baseline=0.5,
                         lower_is_better=False)["promote"] is False


def test_nothing_to_beat_is_not_a_reason_to_refuse():
    """A first model has no incumbent. It still has to clear its baseline where
    one exists — see the anomaly detector, whose baseline is a lift of 1.0."""
    assert registry.gate(challenger=5.0, incumbent=None, baseline=None)["promote"] is True


def test_an_anomaly_detector_worse_than_random_is_not_promoted():
    """Lift is precision over the base rate, so 1.0 *is* random. An earlier
    version passed `baseline=None` here, `gate` read "nothing to beat" as "beat
    it", and a detector whose flagged listings left the feed *less* often than
    average went live."""
    from apps.ml.train import RANDOM_LIFT

    decision = registry.gate(challenger=0.85, incumbent=None, baseline=RANDOM_LIFT,
                             lower_is_better=False)
    assert decision["promote"] is False


def test_a_veto_refuses_a_model_that_won_on_accuracy():
    """The price model is judged on two things and only one of them is an error
    metric. A p10..p90 containing 43% of held-out cars is a picture of certainty
    nobody has, and no MAPE can see it."""
    decision = registry.gate(challenger=1.0, incumbent=10.0, baseline=10.0,
                             veto=(True, "interval_coverage_off_target"))
    assert decision["promote"] is False
    assert decision["reason"] == "interval_coverage_off_target"
    assert decision["vetoed"] is True


def test_a_missing_challenger_metric_refuses_rather_than_defaulting():
    assert registry.gate(challenger=None, incumbent=1.0,
                         baseline=1.0)["reason"] == "no_challenger_metric"


# ===========================================================================
# JSON coercion — the thing that broke a real training run
# ===========================================================================


def test_numpy_scalars_are_coerced_before_they_reach_a_json_field():
    """`np.bool_` survives `round()` and `and`, then fails `json.dumps` at the
    `save()` several frames later with "Object of type bool is not JSON
    serializable". Every metric here starts life inside numpy."""
    import numpy as np

    out = registry.jsonable({"ok": np.bool_(True), "score": np.float64(1.5),
                             "n": np.int64(3), "list": [np.float32(0.5)]})
    assert out == {"ok": True, "score": 1.5, "n": 3, "list": [0.5]}
    assert all(type(v).__module__ != "numpy" for v in (out["ok"], out["score"], out["n"]))


def test_nan_and_infinity_become_null_rather_than_invalid_json():
    """Both are valid Python floats and neither is valid JSON. They arrive
    whenever a metric was computed over an empty slice."""
    out = registry.jsonable({"a": float("nan"), "b": float("inf"), "c": 1.0})
    assert out == {"a": None, "b": None, "c": 1.0}


def test_booleans_stay_booleans_and_do_not_become_integers():
    """bool subclasses int, so an order-of-checks slip writes True as 1 and the
    model card reads "promote: 1"."""
    assert registry.jsonable({"p": True}) == {"p": True}


# ===========================================================================
# Refusal — the behaviour on a host or database with no model
# ===========================================================================


@pytest.fixture
def catalog(db):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    model = Model.objects.create(brand=brand, name_fa="207", is_confirmed=True)
    return {
        "brand": brand, "model": model,
        "variant": Variant.objects.create(model=model, name_fa="پانوراما"),
        "city": City.objects.create(name_fa="تهران"),
    }


def _ad(catalog, code, **over):
    fields = {
        "code": code, "brand": catalog["brand"], "model": catalog["model"],
        "variant": catalog["variant"], "city": catalog["city"],
        "year_jalali": 1400, "mileage": 50_000, "current_price": 1_000_000_000,
        "status": Ad.Status.ACTIVE, "body_status": "بدون رنگ",
        "publish_at": djtz.now() - timedelta(days=5),
        "first_seen_at": djtz.now() - timedelta(days=5),
        "last_seen_at": djtz.now(),
    }
    return Ad.objects.create(**{**fields, **over})


def test_scoring_refuses_when_no_model_has_been_promoted(catalog):
    """The state a fresh database is in, and the state after a rollback. It must
    be a refusal with a reason, not an exception and not a row of zeroes."""
    from apps.ml.inference import score_all

    _ad(catalog, "a1")
    assert score_all() == {"scored": 0, "reason": "no_active_models"}


def test_training_refuses_below_the_minimum_rather_than_memorising(catalog):
    """Under a few hundred rows a boosted tree fits the training set exactly and
    reports a beautiful, meaningless number."""
    from apps.ml.train import train_price

    for i in range(20):
        _ad(catalog, f"a{i}")
    result = train_price()
    assert result["trained"] is False
    assert result["reason"] == "insufficient_rows"
    assert MLModel.objects.count() == 0


def test_a_prediction_for_an_unscored_ad_is_a_refusal_not_an_empty_object(catalog):
    from apps.ml.inference import prediction_for

    _ad(catalog, "a1")
    assert prediction_for("a1") is None


def test_sell_fast_labels_only_episodes_that_had_the_whole_window_to_end(catalog, settings):
    """Right-censoring handled by exclusion, which is exact because the window
    is fixed. An episode that started three days ago has not had fourteen days
    in which to end, so counting it as a negative would teach the model that
    recent listings do not sell — the same bias `research.turnover` avoids by
    only counting completed windows."""
    from apps.ml.train import _episode_rows

    settings.BAMA_EPISODE_CLEAN_START = "2000-01-01"
    now = djtz.now()
    old_and_gone = _ad(catalog, "old1", publish_at=now - timedelta(days=40))
    old_and_still_there = _ad(catalog, "old2", publish_at=now - timedelta(days=40))
    too_recent = _ad(catalog, "new1", publish_at=now - timedelta(days=3))

    ListingEpisode.objects.create(ad=old_and_gone, started_at=now - timedelta(days=40),
                                  ended_at=now - timedelta(days=33))
    ListingEpisode.objects.create(ad=old_and_still_there,
                                  started_at=now - timedelta(days=40))
    ListingEpisode.objects.create(ad=too_recent, started_at=now - timedelta(days=3))

    labels = {r["code"]: r["label"] for r in _episode_rows(horizon_days=14)}
    assert labels == {"old1": 1, "old2": 0}, "the three-day-old episode must be excluded"


def test_a_row_scored_without_the_price_model_reports_no_prediction(catalog):
    """Every column is nullable and null means "the model declined", never zero.
    A card that renders a refused estimate as 0 toman is lying about it."""
    from apps.ml.inference import prediction_for

    ad = _ad(catalog, "a1")
    AdPrediction.objects.create(ad=ad, sell_fast_prob=0.4)
    assert prediction_for("a1") is None


# ===========================================================================
# One real training run, end to end
# ===========================================================================


@pytest.fixture
def fitted(catalog, settings, tmp_path):
    """A catalogue with a genuine price signal, trained and scored.

    Slow by the standards of this suite — one LightGBM fit — so it is one
    fixture that several tests share rather than one per test. The prices are a
    deterministic function of mileage, year and condition plus a little noise,
    so a model that learns nothing is detectable: it would lose to the peer
    median, which the gate would then refuse.
    """
    import random

    settings.ML_ARTIFACT_DIR = tmp_path
    settings.BAMA_EPISODE_CLEAN_START = "2000-01-01"
    rng = random.Random(11)
    now = djtz.now()
    variants = [catalog["variant"]] + [
        Variant.objects.create(model=catalog["model"], name_fa=f"تیپ {i}")
        for i in range(1, 4)
    ]
    bodies = ["بدون رنگ", "خط و خش جزیی", "دو لکه رنگ", "تصادفی"]
    ads = []
    for i in range(900):
        year = rng.choice([1396, 1398, 1400, 1402])
        mileage = rng.randint(0, 250_000)
        body = rng.choice(bodies)
        variant = rng.choice(variants)
        price = int(
            2_000_000_000
            * (0.90 ** (1403 - year))
            * (1 - mileage / 900_000)
            * {"بدون رنگ": 1.0, "خط و خش جزیی": 0.97,
               "دو لکه رنگ": 0.9, "تصادفی": 0.75}[body]
            * rng.lognormvariate(0, 0.05)
        )
        published = now - timedelta(days=rng.uniform(0, 60), hours=rng.uniform(0, 24))
        ads.append(Ad(
            code=f"t{i:04d}", brand=catalog["brand"], model=catalog["model"],
            variant=variant, city=catalog["city"], year_jalali=year,
            mileage=mileage, current_price=price, status=Ad.Status.ACTIVE,
            body_status=body, body_type="هاچبک", fuel="بنزینی",
            transmission="دنده ای", image_count=rng.randint(1, 10),
            description_length=rng.randint(50, 500),
            title=f"پژو 207 {variant.name_fa} مدل {year}", trim=variant.name_fa,
            publish_at=published, first_seen_at=published, last_seen_at=now,
        ))
    Ad.objects.bulk_create(ads)
    # The price model predicts a ratio against the peer median, and serving it
    # needs that median back — from the same cache the statistical panel reads.
    # Without this the scorer correctly refuses every row, which is the right
    # behaviour and would make these tests silently vacuous.
    from apps.core.pricing import compute_deal_scores
    from apps.ml.train import train_price

    compute_deal_scores()
    return train_price()


@pytest.mark.slow
def test_the_price_model_beats_the_peer_median_on_features_the_cohort_cannot_see(fitted):
    """The claim the whole layer rests on. The cohort key is (model, variant,
    year), so a median over it cannot use mileage or condition at all — and here
    both genuinely move the price. If the model cannot win against that, it
    should not be shipped, and the gate would refuse it."""
    assert fitted["trained"] is True
    m = fitted["metrics"]
    assert m["mape"] < m["baseline_mape"]


@pytest.mark.slow
def test_the_published_interval_actually_contains_about_eighty_percent(fitted):
    """The headline metric, and the one an accuracy score cannot see. Before
    conformal calibration this read 43% while MAPE looked excellent."""
    from apps.ml.train import COVERAGE_TOLERANCE_PP

    coverage = fitted["metrics"]["interval_coverage_pct"]
    assert coverage is not None
    assert abs(coverage - 80.0) <= COVERAGE_TOLERANCE_PP


@pytest.mark.slow
def test_a_trained_model_is_registered_with_its_promotion_decision(fitted):
    record = MLModel.objects.get(name=MLModel.Name.PRICE, version=fitted["version"])
    assert record.metrics["promotion"]["reason"]
    assert record.training_rows > 0
    assert record.trained_through is not None
    assert record.feature_spec["columns"] == list(features.COLUMNS)


@pytest.mark.slow
def test_scoring_writes_a_band_and_a_decomposition_for_every_live_listing(fitted):
    from apps.ml.inference import prediction_for, score_all

    result = score_all()
    assert result["scored"] > 0
    assert result["priced"] == result["scored"]

    row = AdPrediction.objects.filter(price_p50__isnull=False).first()
    payload = prediction_for(row.ad_id)
    assert payload["price_p10"] <= payload["price_p50"] <= payload["price_p90"]
    # The decomposition is what keeps the learned number checkable beside the
    # statistical one. Without it this is an oracle.
    assert any(c["feature"] != "_base" for c in payload["contributions"])


@pytest.mark.slow
def test_rescoring_replaces_a_row_rather_than_leaving_a_retired_model_s_columns(fitted):
    """A prediction from a model that is no longer active is worse than no
    prediction, because nothing on screen would say so."""
    from apps.ml.inference import score_all

    score_all()
    ad_id = AdPrediction.objects.first().ad_id
    AdPrediction.objects.filter(ad_id=ad_id).update(sell_fast_prob=0.99)
    score_all()
    # No sell_fast model was ever trained here, so the column must come back
    # empty rather than keeping the value from the previous run.
    assert AdPrediction.objects.get(ad_id=ad_id).sell_fast_prob is None


@pytest.mark.slow
def test_promotion_retires_the_incumbent_so_exactly_one_model_is_ever_live(fitted):
    """Enforced by a partial unique index rather than by convention: "which
    model is live" answered two ways is how a rollback silently fails."""
    from apps.ml.train import train_price

    train_price()
    live = MLModel.objects.filter(name=MLModel.Name.PRICE,
                                  status=MLModel.Status.ACTIVE)
    assert live.count() <= 1


@pytest.mark.slow
def test_input_drift_does_not_report_the_passage_of_time_as_drift(fitted):
    """`days_listed` is `now - publish_at`. Rebuilding the training rows with
    today's clock ages every one of them by however long ago the model was
    fitted, which read as a PSI of 8.0 and pinned the verdict at "unstable"
    forever — the failure mode that gets a monitor ignored."""
    from apps.ml.inference import score_all
    from apps.ml.monitoring import input_drift

    score_all()
    report = input_drift()
    if not report["available"]:
        pytest.skip(f"drift unavailable: {report['reason']}")
    days = next(f for f in report["features"] if f["feature"] == "days_listed")
    assert days["psi"] is None or days["psi"] < 1.0


# ===========================================================================
# The API
# ===========================================================================


def test_the_model_card_endpoint_refuses_before_anything_is_trained(staff_client):
    body = staff_client.get("/api/ml/models/").json()
    assert body["available"] is False
    assert body["reason"] in ("no_models_trained", "ml_unavailable")


def test_a_prediction_for_an_unknown_ad_refuses_with_a_machine_reason(staff_client):
    body = staff_client.get("/api/ads/nosuchad/prediction/").json()
    assert body["available"] is False
    assert body["reason"] in ("no_active_model", "not_scored", "ml_unavailable")


def test_the_review_queue_is_staff_only(api_client, db):
    """It backs an edit to the catalogue — merging two model rows changes the
    cohort key every price on the site is computed from."""
    assert api_client.get("/api/ml/review-queue/").status_code in (401, 403)


def test_monitoring_is_staff_only(api_client, db):
    assert api_client.get("/api/ml/monitoring/").status_code in (401, 403)


def test_the_deal_board_rejects_an_unknown_band(staff_client):
    assert staff_client.get("/api/analytics/deal-scores/?band=nonsense").status_code == 400


def test_the_ml_band_is_empty_rather_than_erroring_before_anything_is_scored(staff_client):
    body = staff_client.get("/api/analytics/deal-scores/?band=ml").json()
    assert body["band"] == "ml"
    assert body["count"] == 0


def test_a_deal_row_carries_no_ml_block_when_the_ad_was_never_scored(catalog, staff_client):
    """Absent, not zeroed. `null` renders as "no estimate"; a zero renders as an
    estimate of nothing."""
    from apps.core.models import DealScoreCache

    ad = _ad(catalog, "a1")
    DealScoreCache.objects.create(ad=ad, score=10.0, discount_pct=10.0,
                                  peer_median=1_100_000_000, components={})
    body = staff_client.get("/api/analytics/deal-scores/?band=all").json()
    assert body["results"][0]["ml"] is None


# ===========================================================================
# Target leakage in the model/text classifier
#
# Both of these are regression tests for defects found on production data, not
# hypotheticals. The first cost the classifier its entire purpose; the second
# filled the staff review queue with 1,537 ads and zero findings.
# ===========================================================================


def test_the_label_is_stripped_from_the_text_the_classifier_reads():
    """Bama titles are «brand، model» and the catalogue row is minted from that
    same model string, so the raw title contains the answer. Left in, the model
    scores 1.0 by reading it back."""
    row = {"title": "پژو، 206", "trim": "تیپ 2"}
    assert "206" in features.text_of(row)
    assert "206" not in features.text_of(row, exclude="206")
    assert "تیپ" in features.text_of(row, exclude="206")


def test_stripping_the_label_survives_persian_digits_and_spacing():
    """«۲۰۶» and «206» are one car. If normalisation ran only on one side of the
    replace, the leak would survive the removal that was supposed to stop it."""
    row = {"title": "پژو، ۲۰۶", "trim": "تیپ ۵"}
    assert "206" not in features.text_of(row, exclude="206")
    assert "۲۰۶" not in features.text_of(row, exclude="206")


def test_an_ad_the_classifier_never_learned_is_not_flagged_as_misfiled():
    """The trainer drops classes below MIN_CLASS_ADS, so a long-tail car is not
    in `classes_` and the model *cannot* agree with its filing. Every confident
    guess on one is a false positive, which is how a review queue reached 1,537
    entries that were all artefacts."""
    from apps.ml import inference

    class _Pipeline:
        classes_ = [1, 2]

        def predict_proba(self, texts):
            import numpy as np

            return np.array([[0.0, 1.0] for _ in texts])

    rows = [{"code": "rare", "model_id": 99, "title": "هیوندای، جنسیس کوپه",
             "trim": "2.5", "model__name_fa": "جنسیس کوپه"}]
    predictions = {"rare": AdPrediction(ad_id="rare")}
    inference._apply_model_text(
        rows, predictions, {"payload": {"pipeline": _Pipeline(), "threshold": 0.85}})
    assert predictions["rare"].suspected_model_id is None


def test_an_ad_inside_the_vocabulary_is_still_flagged_when_the_text_disagrees():
    """The guard above must not silence the model on the population it was
    actually trained to judge."""
    from apps.ml import inference

    class _Pipeline:
        classes_ = [1, 2]

        def predict_proba(self, texts):
            import numpy as np

            return np.array([[0.0, 1.0] for _ in texts])

    rows = [{"code": "known", "model_id": 1, "title": "پژو، 206",
             "trim": "تیپ 2", "model__name_fa": "206"}]
    predictions = {"known": AdPrediction(ad_id="known")}
    inference._apply_model_text(
        rows, predictions, {"payload": {"pipeline": _Pipeline(), "threshold": 0.85}})
    assert predictions["known"].suspected_model_id == 2
    assert predictions["known"].suspected_model_prob == 1.0


@pytest.mark.django_db
def test_an_incumbent_that_measured_a_different_task_is_not_treated_as_a_rival():
    """Changing what goes into the model changes what the metric means. The
    leaky classifier scored macro-F1 1.0 by reading the label off the title; no
    honest successor can beat that, so the gate must decline the comparison
    rather than hold the fixed model in shadow forever."""
    MLModel.objects.create(
        name=MLModel.Name.MODEL_TEXT, version=1, algorithm="tfidf+sgd",
        status=MLModel.Status.ACTIVE, trained_at=djtz.now(), training_rows=1000,
        feature_spec={"text": "normalized title + trim"}, metrics={"macro_f1": 1.0},
        artifact_path="x")
    same = registry.incumbent_metric(
        MLModel.Name.MODEL_TEXT, "macro_f1",
        feature_spec={"text": "normalized title + trim"})
    changed = registry.incumbent_metric(
        MLModel.Name.MODEL_TEXT, "macro_f1",
        feature_spec={"text": "normalized title + trim, filed model name removed"})
    assert same == 1.0
    assert changed is None


@pytest.mark.django_db
def test_a_growing_category_vocabulary_is_not_a_change_of_task():
    """`FeatureSpec.to_json` pins the fitted vocabularies and the Jalali year,
    and both move every night. If those counted as a spec change, every retrain
    would look incomparable and the incumbent half of the gate would silently
    stop applying — which is what happened to four of five models the first time
    this check shipped."""
    spec = features.fit_spec([], now=djtz.now()).to_json()
    MLModel.objects.create(
        name=MLModel.Name.SELL_FAST, version=1, algorithm="lgbm",
        status=MLModel.Status.ACTIVE, trained_at=djtz.now(), training_rows=1000,
        feature_spec=spec, metrics={"brier": 0.1}, artifact_path="x")

    grown = {**spec, "vocabularies": {"brand_id": {"7": 0, "8": 1}},
             "jalali_year": spec["jalali_year"] + 1}
    assert registry.incumbent_metric(
        MLModel.Name.SELL_FAST, "brier", feature_spec=grown) == 0.1

    fewer = {**spec, "columns": spec["columns"][:-1]}
    assert registry.incumbent_metric(
        MLModel.Name.SELL_FAST, "brier", feature_spec=fewer) is None
