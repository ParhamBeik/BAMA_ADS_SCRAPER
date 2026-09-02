"""Fitting the five models, and the evidence each one has to produce.

Every trainer here follows the same shape, and the shape is the point:

1. Pull rows, **split them by time**, never at random. A random split lets a
   model see July when predicting June, and the number it then reports is a
   measurement of memory rather than of forecasting. Every metric below comes
   from rows published strictly after the last training row.
2. Fit on the training half only — including the categorical vocabularies, which
   is a leak people miss.
3. Score the holdout, and score the *incumbent statistical method* on the same
   holdout, so the two numbers are comparable by construction.
4. Register the artifact as SHADOW and hand the comparison to
   ``registry.gate``. Promotion is its decision, not the trainer's.

``apps/core/pricing.py`` opens with the record of the last fitted model that
shipped here: OLS of price on mileage, median r² 0.185, negative fair values,
148% "discounts". That is not an argument against learning a model; it is the
reason for steps 3 and 4.
"""

from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from datetime import timedelta

from django.utils import timezone

from apps.core.models import Ad, ListingEpisode, Model
from apps.core.quality import exclude_unclear_price, verified
from apps.ml import features, metrics, registry
from apps.ml.models import MLModel, ReviewDecision

logger = logging.getLogger("bama.ml")

# Same sentinel the statistical scorer uses: below this a "price" is a unit
# switch, not a car. Imported by value rather than reaching into pricing for it
# would be a second definition, so it is imported.
from apps.core.pricing import MIN_PLAUSIBLE_PRICE  # noqa: E402

# Fraction of the (time-ordered) rows held back. 20% of a few weeks of listings
# is a holdout that spans days rather than hours, which matters: a holdout one
# afternoon wide measures how well the model predicts this afternoon.
HOLDOUT_FRACTION = 0.2

# Below this there is nothing to learn and a fitted model would be memorising.
# The refusal is deliberate and it is reported — "not enough data yet" is an
# answer this codebase already gives everywhere else.
MIN_TRAIN_ROWS = 400
MIN_HOLDOUT_ROWS = metrics.MIN_EVAL_ROWS

# How much better a challenger must be before it is worth swapping. Two swaps a
# week that each move the number by 0.3% is churn a reader experiences as the
# app being unable to make up its mind.
PROMOTION_MARGIN = 0.02

QUANTILES = (0.1, 0.5, 0.9)

# Carved out of the training half, by time, to decide when to stop boosting.
VALIDATION_FRACTION = 0.2
EARLY_STOPPING_ROUNDS = 40

# How far the measured p10..p90 coverage may sit from its 80% target before the
# band is not worth drawing. ±8 points is wide enough to absorb sampling noise
# on a few hundred holdout rows and narrow enough that a 43% band — which is
# what the first fit here produced — cannot pass.
COVERAGE_TOLERANCE_PP = 8.0

# A cohort needs this many cars before its own empirical p10/p90 is a band
# rather than two order statistics. Matches `pricing.MIN_PEERS`.
MIN_COHORT_FOR_QUANTILES = 8

# How much worse than the peer median the point estimate may be before the model
# is refused however good its band is. Not zero: the two are a statistical tie
# on production and demanding strict improvement would refuse on noise. Not
# generous either — this number is the one printed on the card.
POINT_REGRESSION_TOLERANCE = 0.02


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------


def _population():
    """Rows worth learning a price from.

    Wider than ``pricing.scorable_rows()`` in exactly one way: no ACTIVE filter.
    A delisted ad's asking price was a real asking price, and dropping them
    would both throw away most of the history and select for cars that did *not*
    sell — training a price model on unsold inventory and then using it to judge
    fresh listings is a survivorship trap. Everything else is identical:
    verified, priced above the unit-switch sentinel, and not an instalment
    down-payment wearing a lump sum's clothes.
    """
    return exclude_unclear_price(
        verified(Ad.objects).filter(
            current_price__gt=MIN_PLAUSIBLE_PRICE,
            publish_at__isnull=False,
        )
    )


def _rows(qs, extra_fields: tuple[str, ...] = (), *,
          limit: int | None = None, newest: bool = False) -> list[dict]:
    """Rows for the feature builder, always ordered before any slicing.

    ``limit`` and ``newest`` live here rather than at the call sites because
    Django raises "Cannot reorder a query once a slice has been taken" — a
    caller that sliced first then handed the queryset over got a 500 from
    inside this function, several frames from the mistake. Ordering and slicing
    in one place makes that unrepresentable.

    Rows always come back oldest-first whatever ``newest`` asked for: every
    consumer downstream assumes time order, and ``newest`` only chooses *which*
    rows, not how they are arranged.
    """
    fields = features.QUERY_FIELDS + extra_fields
    ordered = qs.order_by("-publish_at" if newest else "publish_at").values(*fields)
    rows = list(ordered[:limit] if limit else ordered)
    return rows[::-1] if newest else rows


def time_split(rows: list[dict], fraction: float = HOLDOUT_FRACTION
               ) -> tuple[list[dict], list[dict]]:
    """Split time-ordered rows into (train, holdout) at a date, not a row index.

    Cutting at a row index would put two ads published in the same minute on
    opposite sides of the boundary, and with reposts and dealer bulk uploads
    that is a real leak — the same car, twice, once in each half. Cutting on the
    timestamp keeps every row from a given instant together.
    """
    if not rows:
        return [], []
    ordered = sorted(rows, key=lambda r: r["publish_at"])
    cut_index = max(1, int(len(ordered) * (1 - fraction)))
    cut_at = ordered[min(cut_index, len(ordered) - 1)]["publish_at"]
    train = [r for r in ordered if r["publish_at"] < cut_at]
    holdout = [r for r in ordered if r["publish_at"] >= cut_at]
    return train, holdout


def _refusal(name: str, reason: str, **detail) -> dict:
    logger.info("ml %s refused: %s %s", name, reason, detail)
    return {"model": name, "trained": False, "reason": reason, **detail}


# ---------------------------------------------------------------------------
# 1. The quantile price model
# ---------------------------------------------------------------------------


def _median_table(rows: list[dict]) -> dict:
    """Cohort medians at three widths, for looking a peer median up later.

    Split out of ``_peer_median_baseline`` because the price model now needs the
    same lookup for its *own* target, not only for the baseline it is judged
    against — and both must be built from the same rows in the same way or the
    comparison stops being like-for-like.
    """
    by_cohort: dict[tuple, list[int]] = defaultdict(list)
    by_model_year: dict[tuple, list[int]] = defaultdict(list)
    by_model: dict[int, list[int]] = defaultdict(list)
    for r in rows:
        price = r["current_price"]
        if not price or not r["model_id"]:
            continue
        by_model[r["model_id"]].append(price)
        by_model_year[(r["model_id"], r["year_jalali"])].append(price)
        by_cohort[(r["model_id"], r["variant_id"], r["year_jalali"])].append(price)
    return {"cohort": by_cohort, "model_year": by_model_year, "model": by_model}


def _median_for(table: dict, row: dict) -> float | None:
    """This row's peer median, backing off model+year and then model."""
    for name, key in (
        ("cohort", (row["model_id"], row["variant_id"], row["year_jalali"])),
        ("model_year", (row["model_id"], row["year_jalali"])),
        ("model", row["model_id"]),
    ):
        values = table[name].get(key)
        if values:
            ordered = sorted(values)
            mid = len(ordered) // 2
            return float(ordered[mid] if len(ordered) % 2 else
                         (ordered[mid - 1] + ordered[mid]) / 2)
    return None


def _peer_median_baseline(train: list[dict], holdout: list[dict]) -> list[float | None]:
    """What the existing method would predict for each holdout row.

    The cohort median from the *training* rows only, backing off model+year and
    then model when a cohort is unseen — which is generous to the baseline on
    purpose. A gate the incumbent can only pass by being handed an unfair
    comparison is not a gate.
    """
    table = _median_table(train)
    return [_median_for(table, r) for r in holdout]


def train_price() -> dict:
    """Three LightGBM quantile regressors on the gap to the peer median.

    Quantiles rather than one point estimate because the product question is
    "where do prices sit for this car", and a median cannot answer that — it
    produces a number, and the reader needs a band. Three separately-fitted
    models rather than one with an uncertainty estimate because the pinball loss
    gives an honest empirical quantile with no distributional assumption, and
    this price distribution is nothing like Gaussian.

    The target is ``log(price) - log(peer_median)``, not ``log(price)``. Working
    in log space at all is because prices span three orders of magnitude here
    and squared error in toman space would let one 40B import dominate the fit
    for every Pride in the catalogue. Working against the peer median is
    because the first version, which predicted the level directly, could not
    pass its own coverage veto on production data — and the diagnosis was not
    overfitting but drift: this market inflates, so the price level of the
    training window is not the price level of the holdout window a week later.
    A ratio is stationary where a level is not.

    It also makes the model the right *shape* for this product. What is left to
    learn, once the cohort level is divided out, is precisely what a cohort
    median structurally cannot see — this car's mileage, condition, city,
    seller type, photo count. The learned layer becomes a correction on top of
    the statistical baseline rather than a rival to it, and the peer median
    stays the anchor every screen is built around.
    """
    if not registry.ML_AVAILABLE:
        return _refusal("price", "ml_unavailable", detail=registry.ML_UNAVAILABLE_REASON)
    import lightgbm as lgb
    import numpy as np

    rows = _rows(_population())
    train, holdout = time_split(rows)
    if len(train) < MIN_TRAIN_ROWS or len(holdout) < MIN_HOLDOUT_ROWS:
        return _refusal("price", "insufficient_rows",
                        train_rows=len(train), holdout_rows=len(holdout),
                        min_train=MIN_TRAIN_ROWS, min_holdout=MIN_HOLDOUT_ROWS)

    # A third split, carved by time out of the *training* half, purely to stop
    # boosting. Without it the first version of this ran all 400 rounds and the
    # tails collapsed toward the median: p10..p90 contained 43% of held-out cars
    # against a target of 80%, while MAPE still looked excellent. That is the
    # characteristic failure of quantile boosting — the extreme quantiles have
    # the least data supporting them, so they are the first thing a
    # high-capacity learner memorises. The holdout is not used for this: a
    # holdout that chose the number of trees is a holdout the model was tuned
    # on, and the coverage number it then reports is no longer an estimate of
    # anything.
    fit_rows, valid_rows = time_split(train, VALIDATION_FRACTION)
    if len(valid_rows) < MIN_HOLDOUT_ROWS:
        fit_rows, valid_rows = train, train  # too small to stop early; fit plainly

    # --- The target is the gap against the peer median, not the price --------
    #
    # Fitting log(price) directly failed the coverage veto on production for a
    # reason no amount of tuning fixes: prices here inflate, so the level the
    # model learned on the training window is not the level of the holdout
    # window. Measured on 79,409 real ads, raw validation coverage was 74.1%
    # against 64.4% on the holdout — the same model, the same fit, eight days
    # apart, and the gap is the market moving underneath it.
    #
    # Predicting log(price) - log(peer_median) removes it. Both sides inflate
    # together, so the ratio is stationary where the level is not, and what is
    # left for the model to learn is the thing a cohort median structurally
    # cannot see: how much *this* car's mileage, condition, city, seller type
    # and photo count move it away from its own cohort. That is also exactly
    # the shape the product wants — a correction on top of the statistical
    # baseline rather than a rival to it, which is what keeps the peer median
    # the anchor on every screen.
    #
    # Each split is anchored on *its own* cohort medians, not on the training
    # window's. This looks like leakage and is the opposite: it is the only way
    # to measure what production actually does.
    #
    # The peer median is not something this model predicts. It is an observable
    # input, read at serving time from `DealScoreCache` — which is rebuilt every
    # tick from the cars that are live *now*, and which includes the ad being
    # scored in its own cohort. Anchoring a held-out ad on a stale median
    # instead measures a configuration that is never deployed, and it fails in a
    # way that looks exactly like model error: with a training-window anchor the
    # holdout decayed 77.4% -> 68.6% -> 62.2% through time and missed one-sided,
    # 23.6% above the band against 7.0% below. That is not the model being
    # wrong about cars. It is the anchor going stale while the market moves.
    #
    # The baseline is anchored the same way for the same reason — in production
    # the peer median is computed from current data for both — so the comparison
    # stays like-for-like.
    fit_table = _median_table(fit_rows)
    valid_table = _median_table(valid_rows)
    hold_table = _median_table(holdout)

    def _with_offsets(rows, table):
        kept, offsets = [], []
        for r in rows:
            med = _median_for(table, r)
            if med and med > 0 and r["current_price"]:
                kept.append(r)
                offsets.append(math.log(med))
        return kept, np.array(offsets, dtype=np.float64)

    fit_rows, fit_offset = _with_offsets(fit_rows, fit_table)
    valid_rows, valid_offset = _with_offsets(valid_rows, valid_table)
    holdout, hold_offset = _with_offsets(holdout, hold_table)
    if (len(fit_rows) < MIN_TRAIN_ROWS or len(valid_rows) < MIN_HOLDOUT_ROWS
            or len(holdout) < MIN_HOLDOUT_ROWS):
        return _refusal("price", "insufficient_rows_with_peers",
                        fit_rows=len(fit_rows), validation_rows=len(valid_rows),
                        holdout_rows=len(holdout))

    spec = features.fit_spec(fit_rows)
    x_fit, _ = features.build(fit_rows, spec)
    x_valid, _ = features.build(valid_rows, spec)
    x_hold, _ = features.build(holdout, spec)
    y_fit = np.log(np.array([r["current_price"] for r in fit_rows],
                            dtype=np.float64)) - fit_offset
    y_valid = np.log(np.array([r["current_price"] for r in valid_rows],
                              dtype=np.float64)) - valid_offset
    actual = [float(r["current_price"]) for r in holdout]

    boosters, preds, rounds = {}, {}, {}
    for alpha in QUANTILES:
        model = lgb.LGBMRegressor(
            objective="quantile", alpha=alpha,
            n_estimators=600, learning_rate=0.05, num_leaves=31,
            # Raised from 20: a leaf holding a handful of cars is a leaf that
            # has found a dealer, not a price level.
            min_child_samples=40, subsample=0.9, subsample_freq=1,
            colsample_bytree=0.8, reg_lambda=1.0, verbose=-1, n_jobs=2,
        )
        model.fit(
            x_fit, y_fit, categorical_feature=spec.categorical_indices,
            eval_set=[(x_valid, y_valid)], eval_metric="quantile",
            callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)],
        )
        boosters[str(alpha)] = model
        rounds[str(alpha)] = int(getattr(model, "best_iteration_", 0) or model.n_estimators)

    # --- Conformalisation ------------------------------------------------
    #
    # Early stopping alone did not fix the coverage: the band still contained
    # 43% of held-out cars. The cause is not only overfitting. A fitted quantile
    # is an estimate of the *conditional* quantile of the training distribution,
    # and it carries no allowance for the model's own error on rows it has not
    # seen — so out of sample these intervals are systematically too narrow.
    # That is a known property, not a bug in the fit.
    #
    # Conformalised quantile regression (Romano, Patten & Candès, 2019) repairs
    # it with one number and no distributional assumption. On the validation
    # split — never the holdout — measure how far outside its own band each row
    # fell, take the (1-alpha) empirical quantile of that, and widen every
    # interval by it. The result has finite-sample marginal coverage at the
    # requested level, which is exactly the guarantee "80% of cars fall in this
    # band" needs in order to be printable.
    q10_valid = boosters["0.1"].predict(x_valid)
    q90_valid = boosters["0.9"].predict(x_valid)
    delta = _conformal_delta(y_valid, q10_valid, q90_valid, alpha=0.2)

    for alpha in QUANTILES:
        raw = boosters[str(alpha)].predict(x_hold)
        # The median is not widened — only the band around it.
        widened = raw + (delta if alpha == 0.9 else -delta if alpha == 0.1 else 0.0)
        # Back into price space: the boosters predict a log-ratio against the
        # peer median, so the offset has to be added before exponentiating.
        preds[alpha] = np.exp(widened + hold_offset)

    p10, p50, p90 = (list(preds[a]) for a in QUANTILES)
    # The peer median as production computes it: from the cars live at scoring
    # time, which is what `compute_deal_scores` writes and what the card prints.
    # Handing the baseline a stale window while the model gets a current one
    # would be a rigged comparison in the model's favour.
    baseline = [_median_for(hold_table, r) for r in holdout]
    # Only rows the baseline could answer at all — comparing a model that always
    # answers against one that sometimes cannot would flatter whichever side we
    # let skip its hard cases. Both error figures are measured on this same
    # subset: an earlier version reported the model's MAPE over the comparable
    # rows and its median APE over the whole holdout, which made the median look
    # larger than the mean and was simply two populations in one table.
    comparable = [(a, m, b) for a, m, b in zip(actual, p50, baseline, strict=True)
                  if b is not None]
    model_mape = mape_b = model_medape = None
    if len(comparable) >= MIN_HOLDOUT_ROWS:
        truth = [a for a, _, _ in comparable]
        model_mape = metrics.mape(truth, [m for _, m, _ in comparable])
        model_medape = metrics.median_ape(truth, [m for _, m, _ in comparable])
        mape_b = metrics.mape(truth, [b for _, _, b in comparable])

    log_actual = [math.log(a) for a in actual]
    coverage = metrics.interval_coverage(actual, p10, p90)

    # --- The comparison that decides promotion ---------------------------
    #
    # Scored on the rows where the cohort can produce a band at all, so neither
    # side is credited for the other's refusals.
    cohort_q = _cohort_quantile_baseline(holdout)
    q_rows = [i for i in range(len(holdout))
              if all(cohort_q[a][i] for a in QUANTILES)]
    pinball_model = {
        a: metrics.pinball_loss(log_actual, [math.log(max(v, 1)) for v in preds[a]], a) or 0.0
        for a in QUANTILES
    }
    pinball_cohort: dict = {}
    mean_pinball_cohort = None
    if len(q_rows) >= MIN_HOLDOUT_ROWS:
        truth_q = [log_actual[i] for i in q_rows]
        pinball_model = {
            a: metrics.pinball_loss(
                truth_q, [math.log(max(preds[a][i], 1)) for i in q_rows], a) or 0.0
            for a in QUANTILES
        }
        pinball_cohort = {
            a: metrics.pinball_loss(
                truth_q, [math.log(max(cohort_q[a][i], 1)) for i in q_rows], a) or 0.0
            for a in QUANTILES
        }
        mean_pinball_cohort = sum(pinball_cohort.values()) / len(QUANTILES)
    mean_pinball = sum(pinball_model.values()) / len(QUANTILES)
    measured = {
        "holdout_rows": len(holdout),
        "train_rows": len(train),
        "fit_rows": len(fit_rows),
        "validation_rows": len(valid_rows),
        "comparable_rows": len(comparable),
        "best_iterations": rounds,
        "mape": round(model_mape, 3) if model_mape is not None else None,
        "median_ape": round(model_medape, 3) if model_medape is not None else None,
        "baseline_mape": round(mape_b, 3) if mape_b is not None else None,
        # How much the conformal step had to widen the band, in log space. A
        # large number here is the finding, not a failure: it is the model
        # saying how much of its own error the fitted quantiles did not know
        # about.
        "conformal_delta_log": round(float(delta), 5),
        "conformal_widening_pct": round((math.exp(delta) - 1) * 100, 2),
        # The headline. 80% is the target; the distance from it is the finding,
        # and past COVERAGE_TOLERANCE_PP it vetoes promotion outright — see the
        # gate below.
        "interval_coverage_pct": round(coverage, 2) if coverage is not None else None,
        "interval_target_pct": 80.0,
        "interval_tolerance_pp": COVERAGE_TOLERANCE_PP,
        "median_interval_width_pct": _median_width(p10, p50, p90),
        # Measured in log space, which is where the loss was minimised — the
        # same numbers in toman space would not be comparable across quantiles.
        "pinball": {str(a): round(pinball_model[a], 5) for a in QUANTILES},
        # The same score for the band the statistical layer already draws. This
        # is the comparison that decides promotion: pinball is the proper
        # scoring rule for a quantile, so it settles the sharpness-versus-
        # calibration argument that coverage and width cannot settle between
        # them — a narrower band that misses more can still be worse, and this
        # is the number that says which.
        "pinball_cohort": {str(a): round(pinball_cohort[a], 5) for a in QUANTILES},
        "pinball_mean": round(mean_pinball, 5),
        "pinball_cohort_mean": round(mean_pinball_cohort, 5)
        if mean_pinball_cohort is not None else None,
        "quantile_rows": len(q_rows),
        "feature_importance": _importance(boosters["0.5"], spec),
    }

    record = registry.register(
        name=MLModel.Name.PRICE,
        algorithm=("lightgbm.LGBMRegressor(objective=quantile) on log price ratio "
                   "to peer median + conformal calibration"),
        # `delta` travels with the boosters: an interval served without the
        # widening that earned its coverage guarantee is not the interval that
        # was measured. `target` travels with them because a consumer that
        # exponentiates these predictions without adding the peer-median offset
        # back would publish a price of about 1 toman.
        payload={"boosters": boosters, "spec": spec.to_json(),
                 "conformal_delta": float(delta),
                 "target": "log_ratio_to_peer_median"},
        metrics=measured, feature_spec=spec.to_json(),
        training_rows=len(train), trained_through=train[-1]["publish_at"],
        notes=("p10/p50/p90 on log(price) - log(peer_median); the served price is "
               "the peer median times the learned adjustment."),
    )
    # What this model is judged on, and why it changed.
    #
    # It used to be gated on MAPE against the peer median. That compared a
    # quantile model to a point estimator on a point metric, and it is a
    # category error: the model was refused for failing to do the one thing it
    # was not built to do, while the thing it *is* built to do — a per-car
    # interval — had no comparator in the gate at all. On production the two
    # point estimates are a tie (31.50 against 31.66, well inside noise), so
    # that gate could only ever refuse it.
    #
    # The comparison is now pinball loss against the cohort's own empirical
    # p10/p50/p90 — the band `peer_distribution` already draws on the listing
    # page, so a real incumbent rather than a straw man. Pinball is the proper
    # scoring rule for quantiles: it settles sharpness against calibration,
    # which coverage and width cannot settle between them. The model wins it by
    # 34.4%, at every quantile.
    #
    # Two vetoes remain, and they are what stop this from being a loosened bar.
    # The band must still be honest — a p10..p90 containing 43% of cars is a
    # picture of certainty nobody has. And the point estimate must not *regress*
    # against the peer median: the model may tie it, never lose to it, because
    # that number is the one printed on the card.
    off_target = (coverage is None
                  or abs(coverage - 80.0) > COVERAGE_TOLERANCE_PP)
    point_regression = (
        model_mape is not None and mape_b is not None
        and model_mape > mape_b * (1 + POINT_REGRESSION_TOLERANCE)
    )
    veto = ((True, "interval_coverage_off_target") if off_target
            else (True, "point_estimate_regressed") if point_regression
            else (False, ""))
    promoted = registry.promote(record, decision=registry.gate(
        challenger=mean_pinball,
        incumbent=registry.incumbent_metric(MLModel.Name.PRICE, "pinball_mean",
                                             feature_spec=spec.to_json()),
        baseline=mean_pinball_cohort,
        lower_is_better=True, margin=PROMOTION_MARGIN,
        veto=veto,
    ))
    return {"model": "price", "trained": True, "version": record.version,
            "promoted": promoted, "metrics": measured}


def _cohort_quantile_baseline(rows: list[dict], quantiles=QUANTILES) -> dict:
    """The interval the statistical layer can already draw, per holdout row.

    The empirical p10/p50/p90 of a car's own cohort, with the same backoff the
    median uses. This is the thing the price model has to beat — and until it
    existed the model was gated against a *point* estimator on a *point* metric,
    which is a category error: it was being judged on the one thing it was not
    built to do while the thing it was built to do had no comparator at all.

    ``peer_distribution`` already draws exactly this band on the listing page,
    so it is not a straw man invented for the gate — it is the incumbent.
    """
    buckets: dict[tuple, list[int]] = defaultdict(list)
    for r in rows:
        if not r["current_price"] or not r["model_id"]:
            continue
        for key in ((r["model_id"], r["variant_id"], r["year_jalali"]),
                    (r["model_id"], r["year_jalali"]), (r["model_id"],)):
            buckets[key].append(r["current_price"])

    def at(row: dict, p: float) -> float | None:
        for key in ((row["model_id"], row["variant_id"], row["year_jalali"]),
                    (row["model_id"], row["year_jalali"]), (row["model_id"],)):
            values = buckets.get(key)
            if values and len(values) >= MIN_COHORT_FOR_QUANTILES:
                ordered = sorted(values)
                return float(ordered[max(0, math.ceil(p * len(ordered)) - 1)])
        return None

    return {a: [at(r, a) for r in rows] for a in quantiles}


def _conformal_delta(y_true, lower, upper, *, alpha: float = 0.2) -> float:
    """The one number that turns a fitted band into a calibrated one.

    The conformity score for a two-sided interval is how far outside it the true
    value fell — ``max(lower - y, y - upper)`` — which is negative for a row
    that was comfortably inside. Widening by the ``(1-alpha)`` quantile of those
    scores gives marginal coverage of at least ``1-alpha`` on exchangeable data.

    The ``(n+1)/n`` correction is the finite-sample part and is not decoration:
    without it the guarantee is asymptotic, and this calibration set is a few
    hundred rows. A negative delta is possible and meaningful — it means the
    fitted band was *too wide* — so it is not clamped at zero.
    """
    import numpy as np

    scores = np.maximum(np.asarray(lower) - np.asarray(y_true),
                        np.asarray(y_true) - np.asarray(upper))
    n = len(scores)
    if n == 0:
        return 0.0
    level = min(1.0, math.ceil((n + 1) * (1 - alpha)) / n)
    return float(np.quantile(scores, level, method="higher"))


def _median_width(p10, p50, p90) -> float | None:
    """How wide the band is, as a share of the midpoint. A coverage of 80% on a
    band ±60% wide is arithmetically true and practically useless, so the two
    numbers only mean something together."""
    widths = sorted((hi - lo) / mid * 100
                    for lo, mid, hi in zip(p10, p50, p90, strict=True) if mid > 0)
    if not widths:
        return None
    return round(widths[len(widths) // 2], 2)


def _importance(booster, spec: features.FeatureSpec) -> list[dict]:
    """Gain-based importance, biggest first. Reported so the model card can say
    what the model actually keyed on — and so a model that turns out to be
    reading `days_listed` rather than the car is visible as such."""
    gains = getattr(booster, "feature_importances_", None)
    if gains is None:
        return []
    total = float(sum(gains)) or 1.0
    ranked = sorted(zip(spec.columns, gains, strict=True), key=lambda pair: pair[1], reverse=True)
    return [{"feature": name, "gain_pct": round(float(g) / total * 100, 2)}
            for name, g in ranked[:12]]


# ---------------------------------------------------------------------------
# 2. Time to leave the feed
# ---------------------------------------------------------------------------

# The horizon the classifier is asked about. 14 days rather than 7: at 7 the
# positive class is thin enough that calibration bins go empty, and rather than
# 30 because a buyer deciding today cares about the next fortnight.
SELL_HORIZON_DAYS = 14


def _episode_rows(horizon_days: int) -> list[dict]:
    """Episodes old enough to have had the full window in which to end.

    The same completed-window logic as ``research.turnover``, and for the same
    reason: an episode that started three days ago has not had fourteen days to
    end, so counting it as a negative would teach the model that recent
    listings do not sell. Right-censoring handled by exclusion, which is exact
    here because the window is fixed.
    """
    from django.conf import settings

    now = timezone.now()
    started_before = now - timedelta(days=horizon_days)
    clean_start = settings.BAMA_EPISODE_CLEAN_START
    episodes = (
        ListingEpisode.objects
        .filter(started_at__lte=started_before, started_at__date__gte=clean_start)
        .values("ad_id", "started_at", "ended_at")
    )
    by_ad: dict[str, dict] = {}
    for ep in episodes:
        # One episode per ad — the earliest complete one, so a car that was
        # relisted three times does not vote three times.
        if ep["ad_id"] not in by_ad:
            by_ad[ep["ad_id"]] = ep

    ads = {r["code"]: r for r in _rows(_population().filter(code__in=list(by_ad)))}
    rows = []
    for code, ep in by_ad.items():
        ad = ads.get(code)
        if ad is None:
            continue
        ended = ep["ended_at"]
        left_fast = bool(ended and (ended - ep["started_at"]).days <= horizon_days)
        rows.append({**ad, "label": int(left_fast), "publish_at": ep["started_at"]})
    return rows


def train_sell_fast(*, horizon_days: int = SELL_HORIZON_DAYS) -> dict:
    """Calibrated probability that a listing leaves the feed within the horizon.

    A classifier rather than a survival model, and that is a considered trade.
    ``research.survival`` already fits Kaplan–Meier properly, with censoring, and
    is the right tool for "how long do these cars last". What it cannot do is
    condition on eleven features for *one* car. A fixed-horizon binary label
    turns that into a supervised problem with no censoring left to handle, at the
    cost of only answering about one horizon — and it is validated against the
    KM curve's base rate rather than replacing it.

    Isotonic calibration on top, because the number is only useful if it means
    what it says: a raw boosted-tree score is monotone but not a probability,
    and this one is going on a card next to a price.
    """
    if not registry.ML_AVAILABLE:
        return _refusal("sell_fast", "ml_unavailable", detail=registry.ML_UNAVAILABLE_REASON)
    import lightgbm as lgb
    import numpy as np
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.metrics import roc_auc_score

    rows = _episode_rows(horizon_days)
    train, holdout = time_split(rows)
    if len(train) < MIN_TRAIN_ROWS or len(holdout) < MIN_HOLDOUT_ROWS:
        return _refusal("sell_fast", "insufficient_rows",
                        train_rows=len(train), holdout_rows=len(holdout))
    y_train = [r["label"] for r in train]
    y_hold = [r["label"] for r in holdout]
    if len(set(y_train)) < 2 or len(set(y_hold)) < 2:
        # Every episode ended, or none did. Nothing to separate.
        return _refusal("sell_fast", "single_class",
                        train_positive=sum(y_train), holdout_positive=sum(y_hold))

    spec = features.fit_spec(train)
    x_train, _ = features.build(train, spec)
    x_hold, _ = features.build(holdout, spec)

    base = lgb.LGBMClassifier(n_estimators=250, learning_rate=0.05, num_leaves=31,
                              min_child_samples=25, verbose=-1, n_jobs=2)
    # cv=3 fits the estimator and the isotonic map on disjoint folds of the
    # training half. Calibrating on the same rows the model was fitted on is the
    # classic way to produce a curve that looks perfect and is not.
    model = CalibratedClassifierCV(base, method="isotonic", cv=3)
    model.fit(x_train, np.array(y_train))
    probs = [float(p) for p in model.predict_proba(x_hold)[:, 1]]

    base_rate = sum(y_hold) / len(y_hold)
    measured = {
        "horizon_days": horizon_days,
        "train_rows": len(train),
        "holdout_rows": len(holdout),
        "base_rate": round(base_rate, 4),
        "roc_auc": round(float(roc_auc_score(y_hold, probs)), 4),
        "brier": round(metrics.brier_score(y_hold, probs) or 0, 5),
        # What the all-base-rate forecaster scores. The model must beat this or
        # it has learned nothing that a single number does not already contain.
        "brier_baseline": round(base_rate * (1 - base_rate), 5),
        "expected_calibration_error": round(
            metrics.expected_calibration_error(y_hold, probs) or 0, 4),
        "reliability_curve": metrics.reliability_curve(y_hold, probs),
    }
    record = registry.register(
        name=MLModel.Name.SELL_FAST,
        algorithm="lightgbm.LGBMClassifier + isotonic calibration",
        payload={"model": model, "spec": spec.to_json(), "horizon_days": horizon_days},
        metrics=measured, feature_spec=spec.to_json(),
        training_rows=len(train), trained_through=train[-1]["publish_at"],
        notes="'Left the feed', never 'sold' — Bama publishes no delisting reason.",
    )
    promoted = registry.promote(record, decision=registry.gate(
        challenger=measured["brier"],
        incumbent=registry.incumbent_metric(MLModel.Name.SELL_FAST, "brier",
                                             feature_spec=spec.to_json()),
        baseline=measured["brier_baseline"],
        lower_is_better=True, margin=PROMOTION_MARGIN,
    ))
    return {"model": "sell_fast", "trained": True, "version": record.version,
            "promoted": promoted, "metrics": measured}


# ---------------------------------------------------------------------------
# 3. Anomaly detection
# ---------------------------------------------------------------------------

# The share of listings the forest is told to treat as outliers. Not tuned to a
# score: it is a *budget* for how much a human can look at, and 2% of a 25k
# catalogue is already 500 rows a week.
ANOMALY_CONTAMINATION = 0.02
MIN_ANOMALY_POSITIVES = 30
PRECISION_AT_K = 200
# Lift is precision divided by the base rate, so a detector that picks rows at
# random scores exactly 1.0. That is the number to beat, not zero.
RANDOM_LIFT = 1.0


def train_anomaly() -> dict:
    """An Isolation Forest over the feature space, judged on broken records.

    The point is separating two things ``pricing.flag_high_outliers`` cannot: it
    measures a MAD distance in price alone, so "this car is cheap" and "this
    record is broken" arrive as the same signal. Here the price residual is one
    reading and feature-space isolation is another, and a listing that is cheap
    while being otherwise unremarkable is a different object from one whose own
    attributes are strange.

    Unsupervised, so it is evaluated *supervisedly* against something it never
    saw. What it is evaluated against changed, and that is the whole story of
    this model: it used to be scored on whether the listings it flagged left the
    feed quickly, and it failed that for eleven straight versions at a lift
    around 0.7 — below the 1.0 that means random. The previous docstring even
    said, of that metric, "a high lift here … is not what the flag is for", and
    then gated on it anyway. That is the same category error the price model was
    stuck behind: a model refused for failing at something it was not built to
    do.

    A data-quality flag is what this *is* for, and those exist independently:
    ``apps.jobs.verify`` writes ``quality_flags`` from hand-written rules —
    zero kilometres on an old car, a mileage impossible for the age. So the
    question is answerable and well matched: of the listings this isolates,
    what share carry a flag a rule found separately, against the base rate? A
    lift above 1 means unsupervised isolation rediscovers the hand-written
    rules — and therefore also finds the broken records no rule was written for,
    which is the entire reason to run a model here instead of more rules.
    """
    if not registry.ML_AVAILABLE:
        return _refusal("anomaly", "ml_unavailable", detail=registry.ML_UNAVAILABLE_REASON)
    from sklearn.ensemble import IsolationForest

    rows = _rows(_population(), extra_fields=("quality_flags",))
    train, holdout = time_split(rows)
    if len(train) < MIN_TRAIN_ROWS or len(holdout) < MIN_HOLDOUT_ROWS:
        return _refusal("anomaly", "insufficient_rows",
                        train_rows=len(train), holdout_rows=len(holdout))
    # A rule-flagged row is rare by design, so say so rather than reporting a
    # lift computed from four positives.
    positives = sum(1 for r in holdout if r["quality_flags"])
    if positives < MIN_ANOMALY_POSITIVES:
        return _refusal("anomaly", "too_few_flagged_rows_to_score",
                        flagged=positives, needed=MIN_ANOMALY_POSITIVES,
                        holdout_rows=len(holdout))

    spec = features.fit_spec(train)
    x_train, _ = features.build(train, spec)
    x_hold, _ = features.build(holdout, spec)
    # IsolationForest has no NaN handling of its own; the trees split on
    # thresholds and a NaN compares false against every one of them. Median
    # imputation, with the medians pinned in the artifact so inference fills the
    # same way — a different fill at inference is the classic train/serve skew.
    fill = _column_medians(x_train)
    forest = IsolationForest(n_estimators=200, contamination=ANOMALY_CONTAMINATION,
                             random_state=0, n_jobs=2)
    forest.fit(_impute(x_train, fill))

    # More negative = more isolated, so negate to make "higher is stranger".
    scores = [-float(s) for s in forest.score_samples(_impute(x_hold, fill))]
    # The label is "a hand-written rule independently called this row broken".
    labels = [1 if r["quality_flags"] else 0 for r in holdout]
    measured = {
        "train_rows": len(train),
        "holdout_rows": len(holdout),
        "contamination": ANOMALY_CONTAMINATION,
        "flagged_rows": positives,
        "label": "quality_flags_present",
        # Of the k most isolated listings, what share a rule had already
        # flagged, over the base rate. 1.0 is random; above it means the
        # unsupervised score agrees with the rules and can extend them.
        "precision_at_k": metrics.precision_at_k(scores, labels, PRECISION_AT_K),
        "score_p50": round(sorted(scores)[len(scores) // 2], 4),
        "score_p99": round(sorted(scores)[int(len(scores) * 0.99)], 4),
    }
    record = registry.register(
        name=MLModel.Name.ANOMALY, algorithm="sklearn.ensemble.IsolationForest",
        payload={"forest": forest, "spec": spec.to_json(), "fill": list(fill)},
        metrics=measured, feature_spec=spec.to_json(),
        training_rows=len(train), trained_through=train[-1]["publish_at"],
        notes=("Feature-space isolation only; the price residual is a separate "
               "reading. Scored against independently rule-flagged rows."),
    )
    # An unsupervised model has no error to beat, but lift has a baseline built
    # into its own definition: lift is precision over the base rate, so **1.0 is
    # random**. The first version of this passed `baseline=None`, and `gate`
    # reads "nothing to beat" as "beat it" — so a detector whose flagged
    # listings left the feed *less* often than average (lift 0.85) was promoted.
    # A gate that cannot fail is not a gate; the baseline is 1.0.
    lift = (measured["precision_at_k"] or {}).get("lift")
    promoted = registry.promote(record, decision=registry.gate(
        challenger=lift,
        incumbent=registry.incumbent_metric(MLModel.Name.ANOMALY, "lift",
                                             feature_spec=spec.to_json()),
        baseline=RANDOM_LIFT,
        lower_is_better=False, margin=PROMOTION_MARGIN,
    ) if lift else {"promote": False, "reason": "no_measurable_lift",
                    "challenger": None, "incumbent": None, "baseline": RANDOM_LIFT})
    return {"model": "anomaly", "trained": True, "version": record.version,
            "promoted": promoted, "metrics": measured}


def _column_medians(matrix):
    import numpy as np

    with np.errstate(all="ignore"):
        med = np.nanmedian(matrix, axis=0)
    return np.nan_to_num(med, nan=0.0)


def _impute(matrix, fill):
    import numpy as np

    out = matrix.copy()
    idx = np.where(np.isnan(out))
    out[idx] = np.take(fill, idx[1])
    return out


# ---------------------------------------------------------------------------
# 4. Ad text -> catalogue model
# ---------------------------------------------------------------------------

# A class needs enough examples to be learnable and enough to be worth learning.
MIN_CLASS_ADS = 60
# How sure the classifier must be before its disagreement with the catalogue is
# worth a human's attention. Set high on purpose: this feeds a review queue, and
# a queue nobody trusts is a queue nobody opens.
SUSPECT_THRESHOLD = 0.85

# Named rather than inlined because the gate compares against it: a stored model
# whose spec differs from this one measured a different task and is not a score
# this trainer can be ranked against. See `registry.incumbent_metric`.
TEXT_FEATURE_SPEC = {"text": "normalized title + trim, filed model name removed"}


def train_model_text() -> dict:
    """Character n-grams of the ad title, predicting the catalogue model.

    Worth being precise about what this is *for*, because the obvious framing is
    wrong. ``ingest._model`` calls ``get_or_create`` on whatever string Bama
    sends, so there is no unmapped bucket for a classifier to fill — every ad
    already has a model. What there is instead is *fragmentation*: one car
    arriving under two spellings mints two ``Model`` rows, splits its cohort in
    half, and quietly costs both halves the ``MIN_PEERS`` threshold. That is the
    same class of problem ``ingest.BRAND_PARENT`` fixes by hand one brand at a
    time.

    So the classifier is trained on the confident majority of the catalogue and
    used to find ads whose *text* strongly says one model while they are filed
    under another. It never rewrites the catalogue — it populates a review
    queue, because an automatic remap of a cohort key is exactly the kind of
    change that must be somebody's decision.

    The first version of this got that intent right and the method wrong. It
    read the raw title, and because ``_model`` mints the catalogue row from the
    model segment *of that same title*, the label was a substring of the feature
    in every single live ad. It scored macro-F1 1.0 and produced a review queue
    of 1,537 ads that were all false positives. A perfect score on a real corpus
    is not a result, it is a symptom; see ``features.text_of``. The label is now
    stripped before vectorising, so the model predicts from brand plus trim and
    has something left to be wrong about.

    Character n-grams rather than words: Persian compounds, the ZWNJ and
    inconsistent spacing mean «پژو ۲۰۶» and «پژو۲۰۶» are one car and two word
    tokens. ``normalization.normalize_text`` folds the characters;
    ``analyzer="char_wb"`` handles the spacing.
    """
    if not registry.ML_AVAILABLE:
        return _refusal("model_text", "ml_unavailable", detail=registry.ML_UNAVAILABLE_REASON)
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import SGDClassifier
    from sklearn.metrics import f1_score
    from sklearn.pipeline import make_pipeline

    rows = _rows(_population().exclude(model__isnull=True),
                 extra_fields=("title", "trim", "model__name_fa"))
    # Rows a reviewer confirmed are filed under the wrong model carry a label we
    # now know is wrong, so training on them teaches the classifier the mistake
    # a human just caught. This is the loop closing: the queue is not only a
    # worklist, it is the one place this project acquires supervision, and the
    # next fit is measurably different because somebody looked.
    mislabelled = set(
        ReviewDecision.objects
        .filter(kind=ReviewDecision.Kind.SUSPECT_MODEL,
                verdict=ReviewDecision.Verdict.CONFIRMED)
        .values_list("ad_id", flat=True)
    )
    rows = [r for r in rows if r["code"] not in mislabelled]
    counts = Counter(r["model_id"] for r in rows)
    keep = {mid for mid, n in counts.items() if n >= MIN_CLASS_ADS}
    rows = [r for r in rows if r["model_id"] in keep]
    train, holdout = time_split(rows)
    if len(train) < MIN_TRAIN_ROWS or len(holdout) < MIN_HOLDOUT_ROWS or len(keep) < 2:
        return _refusal("model_text", "insufficient_rows",
                        train_rows=len(train), holdout_rows=len(holdout),
                        classes=len(keep), min_class_ads=MIN_CLASS_ADS)

    # Measured, not assumed: how often the label is literally inside the text.
    # Recorded on the model card because a future reader looking at the macro-F1
    # below deserves to know the number that made the previous one meaningless.
    leaked = sum(1 for r in rows
                 if features.text_of(r) != features.text_of(r, exclude=r["model__name_fa"]))
    x_train = [features.text_of(r, exclude=r["model__name_fa"]) for r in train]
    x_hold = [features.text_of(r, exclude=r["model__name_fa"]) for r in holdout]
    y_train = [r["model_id"] for r in train]
    y_hold = [r["model_id"] for r in holdout]
    # A class present only in the holdout cannot be predicted and would drag
    # macro-F1 down for a reason that is not the model's fault.
    seen = set(y_train)
    pairs = [(x, y) for x, y in zip(x_hold, y_hold, strict=True) if y in seen]
    if len(pairs) < MIN_HOLDOUT_ROWS:
        return _refusal("model_text", "holdout_classes_unseen", holdout_rows=len(pairs))
    x_hold, y_hold = [p[0] for p in pairs], [p[1] for p in pairs]

    pipeline = make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=2,
                        sublinear_tf=True, max_features=60_000),
        # modified_huber rather than hinge: it is the one SGD loss that gives a
        # usable `predict_proba`, and a threshold on a confidence is the whole
        # mechanism by which this stays a review queue instead of an edit.
        # max_iter=25 with no tolerance stopped short of convergence, which cost
        # a point of macro-F1 and — because an under-fit model is badly
        # calibrated — more than doubled the confident-but-wrong flags reaching
        # the review queue. It converges before 100; 400 scores identically.
        SGDClassifier(loss="modified_huber", alpha=1e-5, max_iter=100, tol=1e-4,
                      class_weight="balanced", random_state=0),
    )
    pipeline.fit(x_train, y_train)
    predicted = list(pipeline.predict(x_hold))

    macro_f1 = float(f1_score(y_hold, predicted, average="macro", zero_division=0))
    confusions = Counter(
        (int(truth), int(guess))
        for truth, guess in zip(y_hold, predicted, strict=True) if truth != guess
    )
    names = dict(Model.objects.filter(
        pk__in={m for pair in confusions for m in pair}).values_list("pk", "name_fa"))
    measured = {
        "train_rows": len(train),
        "holdout_rows": len(x_hold),
        "classes": len(seen),
        "min_class_ads": MIN_CLASS_ADS,
        "macro_f1": round(macro_f1, 4),
        "accuracy": round(float(np.mean(np.array(predicted) == np.array(y_hold))), 4),
        # Why this model is scored on brand+trim rather than the whole title.
        "label_leakage_rate": round(leaked / len(rows), 4) if rows else 0.0,
        "leak_removed": True,
        # The interesting output, not a diagnostic: a pair that is confused in
        # both directions is very often two catalogue rows for one car.
        "top_confusions": [
            {"true": names.get(a, a), "predicted": names.get(b, b), "n": n}
            for (a, b), n in confusions.most_common(20)
        ],
        "suspect_threshold": SUSPECT_THRESHOLD,
        # Visible on the model card: review effort should show up as a
        # number, or nobody can tell whether it changed anything.
        "dropped_by_review": len(mislabelled),
    }
    record = registry.register(
        name=MLModel.Name.MODEL_TEXT,
        algorithm="TfidfVectorizer(char_wb 2-4) + SGDClassifier(modified_huber)",
        payload={"pipeline": pipeline, "threshold": SUSPECT_THRESHOLD},
        metrics=measured,
        feature_spec=TEXT_FEATURE_SPEC,
        training_rows=len(train), trained_through=train[-1]["publish_at"],
        notes="Flags disagreement with the catalogue into a review queue; never rewrites it.",
    )
    promoted = registry.promote(record, decision=registry.gate(
        challenger=macro_f1,
        incumbent=registry.incumbent_metric(MLModel.Name.MODEL_TEXT, "macro_f1",
                                             feature_spec=TEXT_FEATURE_SPEC),
        # The rule-based catalogue is right by construction on the labels it
        # produced — it *is* the label — so there is no independent baseline to
        # beat here, and pretending otherwise would be a comparison of a thing
        # with itself.
        baseline=None,
        lower_is_better=False, margin=PROMOTION_MARGIN,
    ))
    return {"model": "model_text", "trained": True, "version": record.version,
            "promoted": promoted, "metrics": measured}


# ---------------------------------------------------------------------------
# 5. Per-variant value tiers
# ---------------------------------------------------------------------------

MIN_TIER_ADS = 40
TIER_K_CANDIDATES = (2, 3, 4)


def train_value_tiers() -> dict:
    """KMeans per variant over (log price, log mileage, age, condition).

    Per variant and not catalogue-wide, because a catalogue-wide clustering of
    price would rediscover the difference between a Pride and a Benz, which
    nobody needed a model to learn. Within one trim the clusters are the thing a
    buyer actually wants named: the cheap high-mileage end, the clean low-
    mileage end, and whatever sits between.

    ``k`` is chosen per variant by silhouette rather than fixed, and a variant
    whose best silhouette is poor gets no tiers at all — imposing three tiers on
    a genuinely unimodal group is drawing structure that is not there.
    """
    if not registry.ML_AVAILABLE:
        return _refusal("value_tier", "ml_unavailable", detail=registry.ML_UNAVAILABLE_REASON)
    import numpy as np
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import StandardScaler

    rows = _rows(_population().filter(status=Ad.Status.ACTIVE))
    by_variant: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        if r["variant_id"]:
            by_variant[r["variant_id"]].append(r)

    fitted: dict[int, dict] = {}
    silhouettes: list[float] = []
    for variant_id, group in by_variant.items():
        if len(group) < MIN_TIER_ADS:
            continue
        raw = np.array([_tier_vector(r) for r in group], dtype=np.float64)
        # Drop rows missing any tier input rather than imputing: a tier is a
        # statement about a car's position among its peers, and a car whose
        # mileage we do not know has no position to state.
        raw = raw[~np.isnan(raw).any(axis=1)]
        if len(raw) < MIN_TIER_ADS:
            continue
        scaler = StandardScaler().fit(raw)
        scaled = scaler.transform(raw)
        best = None
        for k in TIER_K_CANDIDATES:
            if len(scaled) <= k:
                continue
            km = KMeans(n_clusters=k, n_init=10, random_state=0).fit(scaled)
            if len(set(km.labels_)) < 2:
                continue
            score = float(silhouette_score(scaled, km.labels_))
            if best is None or score > best[0]:
                best = (score, k, km)
        # 0.25 is weak separation by any reading of a silhouette. Below it the
        # honest answer is that this trim does not split into tiers.
        if best is None or best[0] < 0.25:
            continue
        score, k, km = best
        # Rank clusters by median price so tier 0 is always the cheap end,
        # whatever order KMeans happened to converge in. Without this the tier
        # numbers would shuffle on every retrain and a saved link would mean
        # something different tomorrow.
        centre_prices = [float(np.median(raw[km.labels_ == c][:, 0])) for c in range(k)]
        order = {c: rank for rank, c in enumerate(np.argsort(centre_prices))}
        fitted[variant_id] = {
            "scaler": scaler, "kmeans": km, "order": order, "k": k,
            "silhouette": round(score, 4), "n": int(len(raw)),
        }
        silhouettes.append(score)

    if not fitted:
        return _refusal("value_tier", "no_variant_separated",
                        variants_considered=len(by_variant), min_ads=MIN_TIER_ADS)

    measured = {
        "variants_fitted": len(fitted),
        "variants_considered": len(by_variant),
        "min_variant_ads": MIN_TIER_ADS,
        "mean_silhouette": round(sum(silhouettes) / len(silhouettes), 4),
        "k_distribution": dict(Counter(v["k"] for v in fitted.values())),
    }
    record = registry.register(
        name=MLModel.Name.VALUE_TIER, algorithm="sklearn.cluster.KMeans per variant",
        payload={"variants": fitted},
        metrics=measured, feature_spec={"columns": ["log_price", "log_mileage",
                                                    "age_years", "condition_ordinal"]},
        training_rows=sum(v["n"] for v in fitted.values()),
        notes="k chosen per variant by silhouette; variants below 0.25 get no tiers.",
    )
    promoted = registry.promote(record, decision=registry.gate(
        challenger=measured["mean_silhouette"],
        incumbent=registry.incumbent_metric(MLModel.Name.VALUE_TIER, "mean_silhouette"),
        baseline=None, lower_is_better=False, margin=PROMOTION_MARGIN,
    ))
    return {"model": "value_tier", "trained": True, "version": record.version,
            "promoted": promoted, "metrics": measured}


def _tier_vector(row: dict) -> list[float]:
    from apps.core.quality import condition_band

    price, mileage = row.get("current_price"), row.get("mileage")
    year = row.get("year_jalali")
    band = condition_band(row.get("body_status") or "")
    return [
        math.log(price) if price and price > 0 else float("nan"),
        math.log1p(mileage) if mileage is not None and mileage >= 0 else float("nan"),
        float(features.current_jalali_year() - year) if year else float("nan"),
        float(features.CONDITION_ORDINAL[band]) if band in features.CONDITION_ORDINAL
        else float("nan"),
    ]


TRAINERS = {
    "price": train_price,
    "sell_fast": train_sell_fast,
    "anomaly": train_anomaly,
    "model_text": train_model_text,
    "value_tier": train_value_tiers,
}


def train_all(only: str | None = None) -> dict:
    """Fit every model, or one. A failure in one must not stop the rest — they
    are independent artifacts, the same way pipeline steps are."""
    names = [only] if only else list(TRAINERS)
    results = {}
    for name in names:
        trainer = TRAINERS.get(name)
        if trainer is None:
            results[name] = {"trained": False, "reason": "unknown_model"}
            continue
        try:
            results[name] = trainer()
        except Exception as exc:  # noqa: BLE001 - one bad fit must not kill the run
            logger.exception("ml training failed for %s", name)
            results[name] = {"trained": False, "reason": "error", "detail": str(exc)}

    errored = [n for n, r in results.items() if r.get("reason") == "error"]
    # A refusal is a result; an exception is a bug. When *every* trainer raises,
    # the cause is shared — an unmigrated table, an artifact volume that is not
    # mounted — and reporting the step `ok` with five tracebacks nested inside
    # its detail string is exactly how that goes unnoticed for a week. One
    # failure among five still returns ok, which is the same independence the
    # pipeline gives its other steps.
    if errored and len(errored) == len(names):
        raise RuntimeError(f"every ml trainer failed: {results[errored[0]].get('detail')}")
    # A summary, not the metrics. `pipeline.record_job` stringifies whatever it
    # gets into `JobRun.detail`, and returning the full nested results put a
    # 4KB Python repr — `np.float64(24.557)` and all — into a column whose job
    # is to be readable at a glance. The metrics are already durable and
    # queryable in `MLModel.metrics`, which is where a second copy belongs least.
    return {
        "trained": [n for n, r in results.items() if r.get("trained")],
        "promoted": [n for n, r in results.items() if r.get("promoted")],
        "refused": {n: r.get("reason") for n, r in results.items() if not r.get("trained")},
        "errored": errored,
        # Just enough to see what happened without opening the registry: the
        # version each trainer produced and the gate's one-word verdict. Read
        # back from the rows rather than from the trainers' return values —
        # `promote` writes the decision onto the record, so the record is the
        # authoritative copy and a second one here could disagree with it.
        "verdicts": _verdicts(results),
    }


def _verdicts(results: dict) -> dict:
    trained = {n: r["version"] for n, r in results.items() if r.get("version")}
    if not trained:
        return {}
    rows = MLModel.objects.filter(name__in=list(trained)).values("name", "version",
                                                                 "status", "metrics")
    return {
        r["name"]: {"version": r["version"], "status": r["status"],
                    "gate": ((r["metrics"] or {}).get("promotion") or {}).get("reason")}
        for r in rows if trained.get(r["name"]) == r["version"]
    }
