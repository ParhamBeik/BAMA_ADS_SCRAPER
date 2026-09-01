"""How a model is judged. Pure functions over plain lists, so the judgement is
testable without fitting anything.

The choice of metric per model is the argument, so it is written down here
rather than buried in the trainer:

- **The price model is judged on interval coverage, not only on error.** A
  quantile model whose p10..p90 band contains 55% of held-out cars is worse than
  useless — it looks precise and is not — and MAE cannot see that at all. MAPE
  is reported too, in real toman space rather than the log space the model fits
  in, because a 6% error is what a reader can judge and 0.06 in log space is not.
- **The sell-fast classifier is judged on the Brier score and a reliability
  curve, not accuracy.** With a base rate near 20%, "never" scores 80% accurate.
  What the product needs from it is that when it says 70% it is right about 70%
  of the time, and that is calibration, which only the reliability curve shows.
- **The anomaly detector is judged on lift, not on a score distribution.** An
  unsupervised model can always produce outliers; the question is whether the
  listings it calls underpriced actually leave the feed faster than the rest.
"""

from __future__ import annotations

import math

# Judging quantile coverage on a handful of held-out rows is judging noise: at
# n=10 an 80% band containing 6 or 9 rows is entirely ordinary. Below this the
# metric is reported as None rather than as a number nobody should act on.
MIN_EVAL_ROWS = 30


def mape(actual: list[float], predicted: list[float]) -> float | None:
    """Mean absolute percentage error, skipping non-positive actuals.

    Percentage rather than absolute because this catalogue spans 50M-toman
    Prides and 50B-toman imports, and an MAE dominated by the top of that range
    would say nothing about the cars most readers are looking at.
    """
    pairs = [(a, p) for a, p in zip(actual, predicted, strict=True) if a and a > 0]
    if not pairs:
        return None
    return sum(abs(a - p) / a for a, p in pairs) / len(pairs) * 100


def median_ape(actual: list[float], predicted: list[float]) -> float | None:
    """MAPE's robust twin. Reported beside it: a large gap between the two is
    itself the finding — it means a few catastrophic rows, not a model that is
    uniformly a bit off."""
    errs = sorted(abs(a - p) / a for a, p in zip(actual, predicted, strict=True) if a and a > 0)
    if not errs:
        return None
    mid = len(errs) // 2
    return (errs[mid] if len(errs) % 2 else (errs[mid - 1] + errs[mid]) / 2) * 100


def pinball_loss(actual: list[float], predicted: list[float], alpha: float) -> float | None:
    """The loss the quantile models are actually fitted on.

    Asymmetric by design: at alpha=0.1 being above the true value is penalised
    nine times as heavily as being below it, which is what makes the fitted line
    a 10th percentile rather than a mean.
    """
    if not actual:
        return None
    total = 0.0
    for a, p in zip(actual, predicted, strict=True):
        diff = a - p
        total += alpha * diff if diff >= 0 else (alpha - 1) * diff
    return total / len(actual)


def interval_coverage(actual: list[float], lower: list[float], upper: list[float]
                      ) -> float | None:
    """Share of held-out values that fell inside the predicted band.

    The headline number for the quantile model. p10..p90 should contain ~80%;
    materially less means the band is too tight to be believed and materially
    more means it is too wide to be useful.
    """
    if len(actual) < MIN_EVAL_ROWS:
        return None
    inside = sum(1 for a, lo, hi in zip(actual, lower, upper, strict=True) if lo <= a <= hi)
    return inside / len(actual) * 100


def brier_score(labels: list[int], probs: list[float]) -> float | None:
    """Mean squared error of a probability. Lower is better; the all-base-rate
    forecaster scores ``p(1-p)``, which is the number a real model must beat."""
    if not labels:
        return None
    return sum((p - y) ** 2 for y, p in zip(labels, probs, strict=True)) / len(labels)


def reliability_curve(labels: list[int], probs: list[float], bins: int = 10) -> list[dict]:
    """Predicted probability against observed frequency, per bin.

    This is the deliverable for the classifier, not a diagnostic: a model that
    says 70% and is right 40% of the time is worse than one with a lower AUC
    that means what it says, because a threshold set on the first one is set on
    a lie. Empty bins are omitted rather than reported as zero.
    """
    buckets: list[list[tuple[int, float]]] = [[] for _ in range(bins)]
    for y, p in zip(labels, probs, strict=True):
        idx = min(bins - 1, max(0, int(p * bins)))
        buckets[idx].append((y, p))
    curve = []
    for i, rows in enumerate(buckets):
        if not rows:
            continue
        curve.append({
            "bin_lower": round(i / bins, 3),
            "bin_upper": round((i + 1) / bins, 3),
            "n": len(rows),
            "mean_predicted": round(sum(p for _, p in rows) / len(rows), 4),
            "observed": round(sum(y for y, _ in rows) / len(rows), 4),
        })
    return curve


def expected_calibration_error(labels: list[int], probs: list[float], bins: int = 10
                               ) -> float | None:
    """One number for the reliability curve: the sample-weighted mean gap
    between what was promised and what happened."""
    if not labels:
        return None
    curve = reliability_curve(labels, probs, bins)
    total = sum(b["n"] for b in curve)
    if not total:
        return None
    return sum(b["n"] * abs(b["mean_predicted"] - b["observed"]) for b in curve) / total


def precision_at_k(scores: list[float], labels: list[int], k: int) -> dict:
    """Of the k highest-scoring rows, what share were positive — and the lift
    over the base rate.

    Precision alone is unreadable without the base rate beside it: 30% precision
    is excellent when 8% of listings leave the feed quickly and worthless when
    40% do. `lift` is the ratio, and it is the number the gate reads.
    """
    if not scores or k <= 0:
        return {"k": k, "precision": None, "base_rate": None, "lift": None}
    ranked = sorted(zip(scores, labels, strict=True), key=lambda pair: pair[0], reverse=True)[:k]
    precision = sum(y for _, y in ranked) / len(ranked)
    base = sum(labels) / len(labels)
    return {
        "k": len(ranked),
        "precision": round(precision, 4),
        "base_rate": round(base, 4),
        "lift": round(precision / base, 3) if base else None,
    }


def population_stability_index(expected: list[float], observed: list[float],
                               bins: int = 10) -> float | None:
    """PSI between a training distribution and a live one, for one feature.

    The standard reading: under 0.1 the input has not moved, 0.1-0.25 is worth
    watching, over 0.25 means the model is being asked about a different
    population than the one it was fitted on. Bin edges come from the *expected*
    side — measuring drift against bins drawn from the drifted data would define
    the problem away.

    Both sides get a floor of 0.0001 in each bin because the formula divides and
    takes a log; an empty bin on one side is a real thing that happens and it
    must not produce an infinity.
    """
    expected = [v for v in expected if v is not None and not math.isnan(v)]
    observed = [v for v in observed if v is not None and not math.isnan(v)]
    if len(expected) < MIN_EVAL_ROWS or len(observed) < MIN_EVAL_ROWS:
        return None
    ordered = sorted(expected)
    edges = [ordered[min(len(ordered) - 1, int(len(ordered) * i / bins))]
             for i in range(1, bins)]
    if len(set(edges)) < 2:
        return None  # a constant feature has no distribution to drift

    def share(values: list[float]) -> list[float]:
        counts = [0] * bins
        for v in values:
            idx = 0
            while idx < len(edges) and v > edges[idx]:
                idx += 1
            counts[idx] += 1
        return [max(c / len(values), 0.0001) for c in counts]

    e, o = share(expected), share(observed)
    return round(sum((oi - ei) * math.log(oi / ei) for ei, oi in zip(e, o, strict=True)), 4)
