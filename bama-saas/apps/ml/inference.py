"""Serving: what the active models say about the live catalogue.

Batch, not per-request. Every consumer of these numbers — the deal board, the
listing card, the analysis page — reads them by ad code, and a per-request
`predict` would mean loading five artifacts inside a web worker and paying a
model load on a cold cache. So a scoring job writes ``AdPrediction`` once per
tick and the API reads a row, exactly as ``DealScoreCache`` already works.

Two properties this module has to preserve:

**A missing model is a refusal, not a zero.** If nothing is ACTIVE for a role —
never trained, held in shadow, rolled back, artifact volume recreated empty —
the corresponding columns stay NULL and every surface renders "no estimate".
That is the same contract ``liquidity`` has on the deal card, and it exists
because "the model declined to answer" and "the model answered zero" are
different facts.

**The learned number never replaces the statistical one.** ``peer_median``
remains what the discount badge is measured against. These columns sit beside
it, with their own decomposition attached, so a reader can see two independent
accounts of the same car and notice when they disagree. That is the property
`apps/core/pricing.py` was rebuilt around and the one thing a learned layer
could most easily destroy.
"""

from __future__ import annotations

import logging
import math

from django.db import transaction

from apps.core.models import Ad, DealScoreCache
from apps.core.quality import exclude_unclear_price, verified
from apps.ml import features, registry
from apps.ml.models import AdPrediction, MLModel

logger = logging.getLogger("bama.ml")

# How far below the model's own p10 an ask has to sit before the listing is
# called a candidate. p10 alone would flag 10% of the catalogue by construction;
# this asks for the ask to be under the bottom of the band *and* materially so.
UNDERPRICED_MARGIN = 0.05

# Contributions smaller than this are noise on a card. Twelve features each
# moving a price by 0.4% is not an explanation, it is a listing.
MIN_CONTRIBUTION = 0.01
MAX_CONTRIBUTIONS = 6

SCORE_BATCH = 1000


def _artifacts() -> dict:
    """Every active model's artifact, keyed by role. Absent means refuse."""
    loaded: dict[str, dict] = {}
    for name in MLModel.Name.values:
        record = registry.active(name)
        if record is None:
            continue
        payload = registry.load(record)
        if payload is None:
            continue
        loaded[name] = {"record": record, "payload": payload}
    return loaded


def scorable():
    """Live listings worth scoring: the same population the board reads."""
    return exclude_unclear_price(
        verified(Ad.objects).filter(status=Ad.Status.ACTIVE, current_price__gt=0)
    )


def score_all(*, limit: int | None = None, model_ids=None) -> dict:
    """Rescore every active listing, or the models whose board rows changed.

    Idempotent: an ad's row is replaced wholesale, so a run that scores with
    three models after a run that scored with four leaves the fourth's columns
    NULL rather than stale. A prediction from a model that is no longer active
    is worse than no prediction, because nothing on screen would say so.
    """
    if not registry.ML_AVAILABLE:
        return {"scored": 0, "reason": "ml_unavailable",
                "detail": registry.ML_UNAVAILABLE_REASON}
    art = _artifacts()
    if not art:
        return {"scored": 0, "reason": "no_active_models"}

    qs = scorable().order_by("code")
    if model_ids is not None:
        qs = qs.filter(model_id__in=model_ids)
    if limit:
        qs = qs[:limit]
    rows = list(qs.values(*features.QUERY_FIELDS, "title", "trim", "model__name_fa"))
    if not rows:
        return {"scored": 0, "reason": "no_rows"}

    versions = {name: art[name]["record"].version for name in art}
    predictions = {r["code"]: AdPrediction(ad_id=r["code"], model_versions=versions)
                   for r in rows}

    _apply_price(rows, predictions, art.get(MLModel.Name.PRICE))
    _apply_anomaly(rows, predictions, art.get(MLModel.Name.ANOMALY))
    _classify_anomaly_kind(rows, predictions)
    _apply_sell_fast(rows, predictions, art.get(MLModel.Name.SELL_FAST))
    _apply_value_tier(rows, predictions, art.get(MLModel.Name.VALUE_TIER))
    _apply_model_text(rows, predictions, art.get(MLModel.Name.MODEL_TEXT))

    objs = list(predictions.values())
    with transaction.atomic():
        # Delete-and-recreate rather than update_or_create per row: this is the
        # same wholesale-rebuild shape as `compute_deal_scores`, it is one query
        # plus one bulk insert instead of 25,000 round trips, and it guarantees
        # no row survives holding a column from a model that is no longer live.
        AdPrediction.objects.filter(ad_id__in=list(predictions)).delete()
        AdPrediction.objects.bulk_create(objs, batch_size=SCORE_BATCH)

    return {
        "scored": len(objs),
        "incremental": model_ids is not None,
        "models": {name: art[name]["record"].version for name in art},
        "priced": sum(1 for p in objs if p.price_p50 is not None),
        "candidates": sum(1 for p in objs
                          if p.anomaly_kind == AdPrediction.Anomaly.UNDERPRICED),
        "data_anomalies": sum(1 for p in objs
                              if p.anomaly_kind == AdPrediction.Anomaly.DATA),
        "suspect_models": sum(1 for p in objs if p.suspected_model_id),
    }


def _apply_price(rows, predictions, art) -> None:
    if not art:
        return
    import numpy as np

    spec = features.FeatureSpec.from_json(art["payload"]["spec"])
    boosters = art["payload"]["boosters"]
    # The conformal widening measured at training time. Serving the raw fitted
    # quantiles instead would serve a band that is not the band whose 80%
    # coverage was measured and published — the guarantee belongs to the
    # widened interval, not to the booster.
    delta = float(art["payload"].get("conformal_delta", 0.0))
    shift = {"0.1": -delta, "0.5": 0.0, "0.9": delta}

    # The model predicts a log-ratio against the peer median, so serving it
    # needs that median back. It comes from `DealScoreCache` — the same number
    # the statistical panel prints — which makes the learned estimate exactly
    # "the peer median times a learned adjustment" and guarantees the two
    # accounts of a car on screen can always be reconciled by the reader.
    # A car with no cached valuation gets no learned price either: a refusal,
    # which every surface already renders, rather than an unanchored guess.
    anchored = art["payload"].get("target") == "log_ratio_to_peer_median"
    medians: dict[str, float] = {}
    if anchored:
        medians = {
            code: float(m) for code, m in DealScoreCache.objects
            .filter(ad_id__in=[r["code"] for r in rows], peer_median__gt=0)
            .values_list("ad_id", "peer_median")
        }
        rows = [r for r in rows if r["code"] in medians]
        if not rows:
            return

    matrix, codes = features.build(rows, spec)
    offset = (np.log(np.array([medians[c] for c in codes], dtype=np.float64))
              if anchored else 0.0)
    quantiles = {a: np.exp(boosters[a].predict(matrix) + shift[a] + offset)
                 for a in ("0.1", "0.5", "0.9")}
    # Exact TreeSHAP straight out of LightGBM — same algorithm the `shap`
    # package would run for a tree model, without the dependency. The last
    # column is the base value, not a feature.
    contribs = boosters["0.5"].predict(matrix, pred_contrib=True)

    by_code = {r["code"]: r for r in rows}
    for i, code in enumerate(codes):
        p10, p50, p90 = (float(quantiles[a][i]) for a in ("0.1", "0.5", "0.9"))
        # A quantile model fitted independently per alpha can cross — p10 above
        # p50 — on a thin region of the feature space. Sorting is the standard
        # repair and it is honest: the three are estimates of three quantiles of
        # one distribution, and quantiles do not cross.
        p10, p50, p90 = sorted((p10, p50, p90))
        pred = predictions[code]
        pred.price_p10, pred.price_p50, pred.price_p90 = int(p10), int(p50), int(p90)
        price = by_code[code].get("current_price")
        if price and p50 > 0:
            pred.residual_pct = round((p50 - price) / p50 * 100, 2)
        pred.contributions = _explain_row(contribs[i], spec)


def _explain_row(contributions, spec: features.FeatureSpec) -> list[dict]:
    """One row's SHAP vector as something a card can print.

    Contributions come back in log-price space, where they add up. Converting
    each to ``exp(c) - 1`` turns it into the multiplicative effect a reader can
    actually use — "this car's mileage is worth -8% against the base" — at the
    cost that the percentages no longer sum, which is why the base value is not
    published beside them as though they did.
    """
    values = list(contributions)
    base = values[-1]
    ranked = sorted(zip(spec.columns, values[:-1], strict=True),
                    key=lambda pair: abs(pair[1]), reverse=True)
    out = []
    for name, contrib in ranked[:MAX_CONTRIBUTIONS]:
        effect = math.exp(contrib) - 1
        if abs(effect) < MIN_CONTRIBUTION:
            continue
        out.append({"feature": name, "effect_pct": round(effect * 100, 2)})
    if out:
        out.append({"feature": "_base", "effect_pct": None,
                    "base_price": int(math.exp(base))})
    return out


def _apply_anomaly(rows, predictions, art) -> None:
    if not art:
        return
    import numpy as np

    spec = features.FeatureSpec.from_json(art["payload"]["spec"])
    forest = art["payload"]["forest"]
    fill = np.array(art["payload"]["fill"], dtype=np.float64)
    matrix, codes = features.build(rows, spec)
    filled = matrix.copy()
    idx = np.where(np.isnan(filled))
    filled[idx] = np.take(fill, idx[1])
    scores = -forest.score_samples(filled)
    flags = forest.predict(filled)  # -1 outlier, 1 inlier
    for i, code in enumerate(codes):
        pred = predictions[code]
        pred.anomaly_score = round(float(scores[i]), 4)
        # Stashed, not saved: `_classify_anomaly_kind` needs it together with
        # the price residual, and neither reading means much alone.
        pred._is_outlier = bool(flags[i] == -1)


def _classify_anomaly_kind(rows, predictions) -> None:
    """Cheap-and-ordinary versus strange — the split the MAD threshold cannot make.

    Order matters here. A listing that is *both* far below its predicted p10 and
    an outlier in feature space is called a data anomaly, not a candidate: when
    the record itself is suspect, the price computed from it is the least
    trustworthy thing about it, and putting that row at the top of a buyer's
    board is precisely the failure the review band exists to prevent.
    """
    by_code = {r["code"]: r for r in rows}
    for code, pred in predictions.items():
        price = by_code[code].get("current_price")
        is_outlier = getattr(pred, "_is_outlier", False)
        if is_outlier:
            pred.anomaly_kind = AdPrediction.Anomaly.DATA
        elif (price and pred.price_p10
              and price < pred.price_p10 * (1 - UNDERPRICED_MARGIN)):
            pred.anomaly_kind = AdPrediction.Anomaly.UNDERPRICED


def _apply_sell_fast(rows, predictions, art) -> None:
    if not art:
        return
    spec = features.FeatureSpec.from_json(art["payload"]["spec"])
    model = art["payload"]["model"]
    horizon = art["payload"].get("horizon_days")
    matrix, codes = features.build(rows, spec)
    probs = model.predict_proba(matrix)[:, 1]
    for i, code in enumerate(codes):
        pred = predictions[code]
        pred.sell_fast_prob = round(float(probs[i]), 4)
        pred.sell_fast_horizon_days = horizon


def _apply_value_tier(rows, predictions, art) -> None:
    if not art:
        return
    import numpy as np

    from apps.ml.train import _tier_vector

    variants = art["payload"]["variants"]
    for r in rows:
        fit = variants.get(r["variant_id"])
        if fit is None:
            continue
        vector = _tier_vector(r)
        if any(math.isnan(v) for v in vector):
            continue  # a car missing a tier input has no position to state
        scaled = fit["scaler"].transform(np.array([vector], dtype=np.float64))
        cluster = int(fit["kmeans"].predict(scaled)[0])
        rank = int(fit["order"][cluster])
        pred = predictions[r["code"]]
        pred.value_tier_rank = rank
        # A machine label. The Persian words for "budget end" and "clean low
        # mileage" are composed in the UI, per the house rule that the API
        # returns keys and facts and never prose.
        pred.value_tier = f"tier_{rank + 1}_of_{fit['k']}"


def _apply_model_text(rows, predictions, art) -> None:
    """Populate the review queue: text says one model, the catalogue says another.

    Two guards, both of which this originally lacked and both of which it needed.

    The label is stripped from the text before the model reads it, exactly as in
    training — otherwise the classifier simply reads the filed model back off the
    title and can never disagree with it.

    And an ad filed under a model the classifier never learned is skipped. The
    trainer keeps only classes with ``MIN_CLASS_ADS`` ads, so a car in the long
    tail is not in ``classes_`` at all; the model is then *unable* to name it,
    must pick some sibling, and does so at p=1.000. Without this check every one
    of those becomes a "suspect" by construction — which is precisely what
    happened: 1,537 flagged ads in production, 1,537 of them filed under a model
    outside the vocabulary, 0 real. Disagreement only carries information when
    agreement was available.
    """
    if not art:
        return
    import numpy as np

    pipeline = art["payload"]["pipeline"]
    threshold = float(art["payload"].get("threshold", 0.85))
    known = {int(c) for c in pipeline.classes_}
    scored = [r for r in rows if r["model_id"] in known]
    if not scored:
        return
    texts = [features.text_of(r, exclude=r.get("model__name_fa")) for r in scored]
    probs = pipeline.predict_proba(texts)
    classes = pipeline.classes_
    best = np.argmax(probs, axis=1)
    for i, r in enumerate(scored):
        confidence = float(probs[i, best[i]])
        predicted_model = int(classes[best[i]])
        if confidence < threshold or predicted_model == r["model_id"]:
            continue
        pred = predictions[r["code"]]
        pred.suspected_model_id = predicted_model
        pred.suspected_model_prob = round(confidence, 4)


def prediction_for(code: str) -> dict | None:
    """One listing's learned view, shaped for the API.

    ``None`` when nothing has been scored — the caller turns that into the same
    ``available: false`` refusal every other thin answer here produces, rather
    than into an empty object that renders as a chart of nothing.
    """
    row = AdPrediction.objects.filter(ad_id=code).first()
    if row is None or row.price_p50 is None:
        return None
    return {
        "price_p10": row.price_p10,
        "price_p50": row.price_p50,
        "price_p90": row.price_p90,
        "residual_pct": row.residual_pct,
        "contributions": row.contributions,
        "anomaly_kind": row.anomaly_kind or None,
        "anomaly_score": row.anomaly_score,
        "sell_fast_prob": row.sell_fast_prob,
        "sell_fast_horizon_days": row.sell_fast_horizon_days,
        "value_tier": row.value_tier or None,
        "value_tier_rank": row.value_tier_rank,
        "model_versions": row.model_versions,
        "scored_at": row.scored_at,
    }
