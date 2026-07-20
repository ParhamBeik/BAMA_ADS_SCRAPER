"""IsStaff-gated admin endpoints that trigger ingestion/analytics jobs.

Jobs run in a daemon thread (no Celery yet — the docker-compose Celery/Redis
stack is a Phase 5 stub) and return HTTP 202 immediately; the client polls
``GET /api/fetch-runs/`` for the resulting ``FetchRun`` (fetch/import) or simply
retries. A cheap DB-level guard rejects a new run while a same-source run is
already ``RUNNING`` so two live fetches can't race. Per-endpoint consumer tier
gating is intentionally not applied here — these are operator-only endpoints.
"""

from __future__ import annotations

import threading
from typing import Callable

from django.core.management import call_command
from django.db import connection
from rest_framework import status, serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiResponse, extend_schema

from apps.accounts.permissions import IsStaff
from apps.history.models import FetchRun


class JobAcceptedSerializer(serializers.Serializer):
    """202 response body for an async admin job trigger."""
    status = serializers.CharField()
    command = serializers.CharField()
    poll = serializers.CharField(required=False)


def _running(source: str) -> bool:
    return FetchRun.objects.filter(
        source=source, status=FetchRun.Status.RUNNING
    ).exists()


def _spawn(command: str, **opts) -> None:
    """Run a management command in a daemon thread, always closing the DB conn.

    Django hands each thread its own connection; a detached thread outliving the
    request must release it or the pool leaks one connection per job.
    """

    def _run() -> None:
        try:
            call_command(command, **opts)
        finally:
            connection.close()

    threading.Thread(target=_run, daemon=True).start()


def _opt(data: dict, key: str, cast: Callable) -> dict:
    """Return ``{key: cast(value)}`` if present, else ``{}``. Raises 400 on bad input."""
    raw = data.get(key)
    if raw in (None, "", "null"):
        return {}
    try:
        return {key: cast(raw)}
    except (TypeError, ValueError) as exc:
        name = cast.__name__
        article = "an" if name[:1].lower() in "aeiou" else "a"
        raise ValueError(f"{key} must be {article} {name}") from exc


def _accepted(command: str, **extra) -> Response:
    payload = {"status": "started", "command": command, **extra}
    return Response(payload, status=status.HTTP_202_ACCEPTED)


@extend_schema(
    tags=["Admin · Jobs"],
    request=None,
    responses={202: JobAcceptedSerializer, 409: None},
    description="Trigger an async live Bama fetch (operator-only).",
)
@api_view(["POST"])
@permission_classes([IsStaff])
def trigger_fetch(request):
    """POST /api/admin/jobs/fetch/ — trigger an async live Bama fetch."""
    if _running(FetchRun.Source.LIVE_FETCH):
        return Response({"detail": "A live fetch is already running."},
                        status=status.HTTP_409_CONFLICT)
    try:
        opts = {**_opt(request.data, "max_ads", int),
                **_opt(request.data, "page_pause", float),
                **_opt(request.data, "request_timeout", int)}
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    _spawn("fetch_live", **opts)
    return _accepted("fetch_live", poll="GET /api/fetch-runs/?source=live_fetch")


@extend_schema(
    tags=["Admin · Jobs"],
    request=None,
    responses={202: JobAcceptedSerializer, 409: None},
    description="Trigger an async bulk import of scraped JSON (operator-only).",
)
@api_view(["POST"])
@permission_classes([IsStaff])
def trigger_import(request):
    """POST /api/admin/jobs/import/ — trigger an async bulk import of scraped JSON."""
    if _running(FetchRun.Source.BULK_IMPORT):
        return Response({"detail": "A bulk import is already running."},
                        status=status.HTTP_409_CONFLICT)
    try:
        opts = {**_opt(request.data, "limit", int),
                **_opt(request.data, "batch_size", int)}
        if request.data.get("root"):
            opts["root"] = str(request.data["root"])
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    _spawn("import_scraped", **opts)
    return _accepted("import_scraped", poll="GET /api/fetch-runs/?source=bulk_import")


@extend_schema(
    tags=["Admin · Jobs"],
    request=None,
    responses={202: JobAcceptedSerializer},
    description="Rebuild PriceStatistics aggregates (operator-only).",
)
@api_view(["POST"])
@permission_classes([IsStaff])
def trigger_refresh(request):
    """POST /api/admin/jobs/refresh-analytics/ — rebuild PriceStatistics (async)."""
    try:
        opts = _opt(request.data, "min_count", int)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    _spawn("refresh_analytics", **opts)
    return _accepted("refresh_analytics")


@extend_schema(
    tags=["Admin · Jobs"],
    request=None,
    responses={202: JobAcceptedSerializer},
    description="Rebuild per-ad DealScoreCache (the best-deal board). Operator-only.",
)
@api_view(["POST"])
@permission_classes([IsStaff])
def trigger_deal_scores(request):
    """POST /api/admin/jobs/deal-scores/ — rebuild DealScoreCache (async)."""
    try:
        opts = {**_opt(request.data, "min_peers", int),
                **_opt(request.data, "model", int)}
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    _spawn("compute_deal_scores", **opts)
    return _accepted("compute_deal_scores")


@extend_schema(
    tags=["Admin · Jobs"],
    request=None,
    responses={202: JobAcceptedSerializer},
    description="Evaluate every enabled user Alert and dispatch notifications. Operator-only.",
)
@api_view(["POST"])
@permission_classes([IsStaff])
def trigger_evaluate_alerts(request):
    """POST /api/admin/jobs/evaluate-alerts/ — run alert evaluation now (async)."""
    _spawn("evaluate_alerts")
    return _accepted("evaluate_alerts")
