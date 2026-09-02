"""The learned layer's own read API.

Four endpoints, and the split between them is who they are for:

- ``/api/ml/models/`` — the model cards. Every trained artifact with its
  metrics, its promotion decision and the reason. This is what
  ``methodology_version`` has been pointing at for months with nothing behind
  it: a reader who wants to know why a number on a card is what it is can now
  read what produced it, including the models that were *not* promoted and why.
- ``/api/ads/<code>/prediction/`` — one listing's learned view with its SHAP
  decomposition, drawn beside the statistical breakdown rather than instead of
  it.
- ``/api/ml/review-queue/`` — ads whose text says one catalogue model while
  they are filed under another. Staff only, because acting on it edits the
  catalogue.
- ``/api/ml/monitoring/`` — drift and live error. Staff only; it belongs beside
  the crawl health on the Control page.

Everything here refuses rather than guesses. ``available: false`` with a machine
``reason`` is the same shape every thin answer in ``apps/core/views`` already
returns, and it is what the UI renders when no model has been promoted.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from apps.core.models import Ad
from apps.core.views import envelope
from apps.ml import inference, monitoring, registry
from apps.ml.models import AdPrediction, MLModel, ReviewDecision

# How many rows the review queue hands over at once. It is a human's worklist,
# not a dataset — a thousand suspected misfilings is the same signal as fifty
# and nobody works through either in one sitting.
REVIEW_LIMIT = 100


def _card(record: MLModel) -> dict:
    """One model, as the methodology page renders it.

    ``metrics`` goes out whole, including ``promotion``. That is the point: a
    model card that shows only the winners is marketing, and the interesting
    entry is the one that says a challenger lost to the peer median and was
    held in shadow.
    """
    return {
        "name": record.name,
        "label": record.get_name_display(),
        "version": record.version,
        "status": record.status,
        "algorithm": record.algorithm,
        "trained_at": record.trained_at,
        "trained_through": record.trained_through,
        "training_rows": record.training_rows,
        "metrics": record.metrics or {},
        "notes": record.notes,
        # Which columns it saw. A model card without its feature list is an
        # assertion; with it, a reader can tell whether the thing they think
        # explains a price was even available to the model.
        "features": (record.feature_spec or {}).get("columns") or [],
    }


@api_view(["GET"])
def models_view(request):
    """Every trained model, newest first — the methodology page's data."""
    if not registry.ML_AVAILABLE:
        return envelope({"available": False, "reason": "ml_unavailable",
                         "detail": registry.ML_UNAVAILABLE_REASON, "models": []})
    records = list(MLModel.objects.order_by("name", "-version"))
    if not records:
        return envelope({"available": False, "reason": "no_models_trained",
                         "models": []})
    return envelope({
        "available": True,
        "models": [_card(r) for r in records],
        "active": {r.name: r.version for r in records
                   if r.status == MLModel.Status.ACTIVE},
        "scored_ads": AdPrediction.objects.count(),
    })


@api_view(["GET"])
def prediction_view(request, code: str):
    """One listing's learned estimate, its band, and what moved it."""
    payload = inference.prediction_for(code)
    if payload is None:
        # Three different causes, one answer, because the reader's next action
        # is the same in all of them: nothing to show. The distinction is in
        # `models_view`, where it belongs.
        return envelope({
            "available": False,
            "reason": "ml_unavailable" if not registry.ML_AVAILABLE
            else "not_scored" if registry.active(MLModel.Name.PRICE)
            else "no_active_model",
        })
    return envelope({"available": True, **payload})


@api_view(["GET"])
@permission_classes([IsAdminUser])
def review_queue_view(request):
    """Ads the text classifier disagrees with the catalogue about.

    A worklist, never an action. The classifier never rewrites ``Ad.model``:
    merging two catalogue rows changes the cohort key that every price on the
    site is computed from, and that has to be somebody's decision with the
    evidence in front of them. Ordered by the classifier's confidence, so the
    clearest cases are the ones a human sees first.
    """
    # Already-settled cases drop out. A queue that re-presents a case somebody
    # decided last week is a queue people stop opening.
    settled = ReviewDecision.objects.filter(
        kind=ReviewDecision.Kind.SUSPECT_MODEL).values_list("ad_id", flat=True)
    pending = (AdPrediction.objects
               .filter(suspected_model__isnull=False)
               .exclude(ad_id__in=settled))
    rows = (
        pending
        .select_related("ad", "ad__model", "ad__model__brand", "suspected_model",
                        "suspected_model__brand")
        .order_by("-suspected_model_prob")[:REVIEW_LIMIT]
    )
    return envelope({
        "available": True,
        "count": pending.count(),
        "settled": settled.count(),
        "limit": REVIEW_LIMIT,
        "results": [{
            "code": r.ad_id,
            "title": r.ad.title,
            "filed_under": {
                "id": r.ad.model_id,
                "name": r.ad.model.name_fa if r.ad.model_id else None,
                "brand": r.ad.model.brand.name_fa if r.ad.model_id else None,
            },
            "suspected": {
                "id": r.suspected_model_id,
                "name": r.suspected_model.name_fa,
                "brand": r.suspected_model.brand.name_fa,
            },
            "confidence": r.suspected_model_prob,
            "excluded_from_analytics": r.ad.excluded_from_analytics,
        } for r in rows],
    })


@api_view(["GET"])
@permission_classes([IsAdminUser])
def monitoring_view(request):
    """Input drift, prediction drift, and the registry — for the Control page."""
    return envelope(monitoring.report())


@api_view(["POST"])
@permission_classes([IsAdminUser])
def review_decide_view(request, code: str):
    """Record a human's verdict on a flag, and optionally exclude the ad.

    Two things happen here and they are deliberately separable. The verdict is
    *about the model* — it says whether this flag was right, and it is the only
    labelled data this project produces, because every other label is either the
    catalogue's own value or a rule's output. Excluding the ad is *about the
    data* — a broken listing poisons every median it sits in, and until now
    there was no way to say so short of deleting the row.

    A reviewer can do either without the other. Rejecting a flag on a listing
    that is nonetheless junk is a real combination, and so is confirming a
    misfiling on a listing whose price is perfectly good.
    """
    kind = (request.data.get("kind") or "").strip()
    verdict = (request.data.get("verdict") or "").strip()
    exclude = request.data.get("exclude")

    if kind not in ReviewDecision.Kind.values:
        return Response({"detail": f"kind must be one of "
                                   f"{', '.join(ReviewDecision.Kind.values)}"},
                        status=status.HTTP_400_BAD_REQUEST)
    if verdict and verdict not in ReviewDecision.Verdict.values:
        return Response({"detail": f"verdict must be one of "
                                   f"{', '.join(ReviewDecision.Verdict.values)}"},
                        status=status.HTTP_400_BAD_REQUEST)
    if not verdict and exclude is None:
        return Response({"detail": "nothing to do: send a verdict, an exclude, or both"},
                        status=status.HTTP_400_BAD_REQUEST)

    ad = get_object_or_404(Ad.objects, code=code)
    decision = None
    if verdict:
        # The model's claim at this moment, snapshotted: the next retrain may
        # change its mind, and a decision has to stay interpretable against what
        # was actually on screen when somebody made it.
        pred = AdPrediction.objects.filter(ad_id=code).first()
        claim: dict = {}
        if pred and kind == ReviewDecision.Kind.SUSPECT_MODEL:
            claim = {"suspected_model_id": pred.suspected_model_id,
                     "confidence": pred.suspected_model_prob,
                     "filed_model_id": ad.model_id}
        elif pred:
            claim = {"anomaly_score": pred.anomaly_score,
                     "anomaly_kind": pred.anomaly_kind}
        decision, _ = ReviewDecision.objects.update_or_create(
            ad=ad, kind=kind,
            defaults={"verdict": verdict, "claim": claim,
                      "note": (request.data.get("note") or "")[:2000],
                      "reviewer": request.user if request.user.is_authenticated else None},
        )
    if exclude is not None and bool(exclude) != ad.excluded_from_analytics:
        ad.excluded_from_analytics = bool(exclude)
        ad.save(update_fields=["excluded_from_analytics"])

    return Response({
        "code": code,
        "kind": kind,
        "verdict": decision.verdict if decision else None,
        "excluded_from_analytics": ad.excluded_from_analytics,
        # The caller needs to know the numbers it is looking at are now stale:
        # an exclusion changes every median this ad was part of, and nothing is
        # recomputed until the next `deal_scores` tick.
        "recompute_pending": exclude is not None,
    })
