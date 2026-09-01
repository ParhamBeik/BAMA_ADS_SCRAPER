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

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser

from apps.core.views import envelope
from apps.ml import inference, monitoring, registry
from apps.ml.models import AdPrediction, MLModel

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
    rows = (
        AdPrediction.objects
        .filter(suspected_model__isnull=False)
        .select_related("ad", "ad__model", "ad__model__brand", "suspected_model",
                        "suspected_model__brand")
        .order_by("-suspected_model_prob")[:REVIEW_LIMIT]
    )
    return envelope({
        "available": True,
        "count": AdPrediction.objects.filter(suspected_model__isnull=False).count(),
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
        } for r in rows],
    })


@api_view(["GET"])
@permission_classes([IsAdminUser])
def monitoring_view(request):
    """Input drift, prediction drift, and the registry — for the Control page."""
    return envelope(monitoring.report())
