"""Insights endpoints: liquidity, undervalued listings, market depth, depreciation.

Each is keyed by ``<int:model_id>`` with optional ``?variant`` and ``?year``.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.catalog.models import Model
from apps.analytics.services import insights


def _opt_int(params, key):
    raw = params.get(key)
    if raw in (None, "", "null"):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be an integer")


@api_view(["GET"])
def insight(request, model_id: int, kind: str):
    model = get_object_or_404(Model, id=model_id)
    params = request.query_params
    try:
        variant = _opt_int(params, "variant")
        year = _opt_int(params, "year")
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    if kind == "liquidity":
        data = insights.liquidity(model.id, variant_id=variant, year=year)
    elif kind == "market-depth":
        data = insights.market_depth(model.id, variant_id=variant, year=year)
    elif kind == "undervalued":
        data = insights.undervalued(model.id, variant_id=variant, year=year)
    elif kind == "depreciation":
        data = insights.depreciation(model.id, variant_id=variant)
    else:
        return Response(
            {"detail": "kind must be one of: liquidity, market-depth, undervalued, depreciation"},
            status=status.HTTP_404_NOT_FOUND,
        )
    data["model_name"] = model.name_fa
    data["brand_name"] = model.brand.name_fa
    return Response(data)
