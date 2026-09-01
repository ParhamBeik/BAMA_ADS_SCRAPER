"""Is the live model still answering the question it was fitted on?

Three readings, and they fail in different ways, which is why there are three:

- **Input drift (PSI).** The listings arriving today may not resemble the ones
  the model was fitted on. This is the earliest warning and the only one
  available immediately — it needs no outcomes at all, just today's features
  against the training distribution stored on the row.
- **Prediction drift.** The inputs can look identical while the output
  distribution moves, which usually means the mix inside a category changed
  rather than the categories themselves.
- **Live error.** The ground truth, and the slowest: for the price model the
  ask is observable immediately, so the residual distribution can be compared
  against the holdout's; for the sell-fast model an outcome takes a fortnight to
  exist at all.

None of these fires anything automatically. They are surfaced on the Control
page next to the crawl health that is already there, because "retrain now" is a
judgement about whether the new data is better, and a threshold that retrains on
a drift number alone will happily refit on a week when the crawler was broken.
"""

from __future__ import annotations

import logging

from apps.ml import features, metrics, registry
from apps.ml.models import AdPrediction, MLModel

logger = logging.getLogger("bama.ml")

# The conventional reading of PSI. Written down because "0.25" is meaningless
# without it and the number ends up in a UI.
PSI_BANDS = ((0.1, "stable"), (0.25, "watch"))
PSI_UNSTABLE = "unstable"

# Features worth watching. Not all of them: PSI on a categorical id encoded as
# an integer code is arithmetic on a label, and would report drift whenever the
# catalogue gained a row.
MONITORED = ("mileage", "log_mileage", "year_jalali", "age_years",
             "condition_ordinal", "days_listed", "image_count",
             "description_length")


def psi_band(value: float | None) -> str | None:
    if value is None:
        return None
    for edge, label in PSI_BANDS:
        if value < edge:
            return label
    return PSI_UNSTABLE


def input_drift(name: str = MLModel.Name.PRICE, *, sample: int = 4000) -> dict:
    """PSI per monitored feature: what the model was fitted on, against what it
    is being asked about.

    The live side is the **scoring population** — every listing ``score_all``
    actually runs the model over — and not "rows published since the model was
    trained". That distinction turned out to matter. Ads published since the
    boundary are by construction all young, so ``days_listed`` on that side
    spans a fortnight against the training set's two months, and PSI read 8.0 on
    a feature that had not drifted at all. It pinned the whole model's verdict
    at "unstable" permanently, which is the failure mode that gets a monitor
    ignored. The question drift is supposed to answer is whether the rows going
    into the model today look like the rows it learned from, and the rows going
    in today are the ones being scored.

    The training distribution is *recomputed* rather than stored as a histogram,
    because the bin edges have to come from the expected side and a stored
    histogram would pin bins chosen for a different question. It costs one
    bounded query on a job that runs daily.
    """
    if not registry.ML_AVAILABLE:
        return {"available": False, "reason": "ml_unavailable"}
    record = registry.active(name)
    if record is None:
        return {"available": False, "reason": "no_active_model"}
    if record.trained_through is None:
        return {"available": False, "reason": "no_training_boundary"}

    from apps.ml.inference import scorable
    from apps.ml.train import _population, _rows

    spec = features.FeatureSpec.from_json(record.feature_spec)
    train_rows = _rows(_population().filter(publish_at__lt=record.trained_through),
                       limit=sample, newest=True)
    live_rows = _rows(scorable(), limit=sample, newest=True)
    if not train_rows or not live_rows:
        return {"available": False, "reason": "insufficient_rows",
                "train_rows": len(train_rows), "live_rows": len(live_rows)}

    index = {c: i for i, c in enumerate(spec.columns)}
    # Each side against its own clock. `days_listed` is `now - publish_at`, so
    # rebuilding the training rows with today's clock would age every one of
    # them by however long ago the model was fitted, and report the passage of
    # time as drift. They are rebuilt as they stood at `trained_through`, which
    # is what the model actually saw.
    train_matrix = [features.row_features(r, spec, now=record.trained_through)
                    for r in train_rows]
    live_matrix = [features.row_features(r, spec) for r in live_rows]
    per_feature = []
    for column in MONITORED:
        if column not in index:
            continue
        i = index[column]
        value = metrics.population_stability_index(
            [row[i] for row in train_matrix], [row[i] for row in live_matrix])
        per_feature.append({"feature": column, "psi": value, "band": psi_band(value)})

    measured = [f for f in per_feature if f["psi"] is not None]
    worst = max(measured, key=lambda f: f["psi"]) if measured else None
    return {
        "available": True,
        "model": name,
        "version": record.version,
        "trained_through": record.trained_through,
        "train_rows": len(train_rows),
        "live_rows": len(live_rows),
        "features": per_feature,
        "worst": worst,
        # One word for the whole model, taken from its worst feature — a single
        # badly-drifted input is enough to make a prediction untrustworthy, so
        # averaging would hide exactly the case worth seeing.
        "verdict": worst["band"] if worst else None,
    }


def prediction_drift(name: str = MLModel.Name.PRICE) -> dict:
    """How today's live predictions compare to the holdout they were judged on.

    For the price model the interesting statistic is the *residual*: the ask
    against the model's own p50. Its holdout distribution is implied by the
    recorded MAPE, so a live median absolute residual far above that number
    means the model is being asked about cars it no longer prices well — even
    though no true price has been observed, because the asking price is the
    label here.
    """
    record = registry.active(name)
    if record is None:
        return {"available": False, "reason": "no_active_model"}
    residuals = list(
        AdPrediction.objects.filter(price_p50__isnull=False, residual_pct__isnull=False)
        .values_list("residual_pct", flat=True)
    )
    if len(residuals) < metrics.MIN_EVAL_ROWS:
        return {"available": False, "reason": "insufficient_scored_rows",
                "scored_rows": len(residuals)}
    ordered = sorted(abs(r) for r in residuals)
    live_mae_pct = ordered[len(ordered) // 2]
    holdout_mape = (record.metrics or {}).get("mape")
    return {
        "available": True,
        "model": name,
        "version": record.version,
        "scored_rows": len(residuals),
        "live_median_abs_residual_pct": round(live_mae_pct, 3),
        "holdout_mape": holdout_mape,
        # A ratio, so the reading does not depend on knowing what a good MAPE is
        # for this catalogue. Above ~1.5 the model is doing materially worse in
        # production than it did on the holdout that got it promoted.
        "ratio": round(live_mae_pct / holdout_mape, 3) if holdout_mape else None,
        "signed_median_pct": round(sorted(residuals)[len(residuals) // 2], 3),
    }


def report() -> dict:
    """Everything the Control page needs about the learned layer in one call."""
    models = [
        {
            "name": m.name,
            "version": m.version,
            "status": m.status,
            "algorithm": m.algorithm,
            "trained_at": m.trained_at,
            "trained_through": m.trained_through,
            "training_rows": m.training_rows,
            "metrics": m.metrics,
        }
        for m in MLModel.objects.order_by("name", "-version")[:40]
    ]
    return {
        "available": registry.ML_AVAILABLE,
        "reason": None if registry.ML_AVAILABLE else registry.ML_UNAVAILABLE_REASON,
        "models": models,
        "active": {m["name"]: m["version"] for m in models if m["status"] == "active"},
        "shadow": [f"{m['name']} v{m['version']}" for m in models
                   if m["status"] == "shadow"],
        "scored_ads": AdPrediction.objects.count(),
        "input_drift": input_drift(),
        "prediction_drift": prediction_drift(),
    }
