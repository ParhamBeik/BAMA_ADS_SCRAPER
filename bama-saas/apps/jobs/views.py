"""Operator endpoints that trigger ingestion/analytics jobs.

Jobs run in a daemon thread (no Celery — flock + the compose worker loop is the
scheduler) and return HTTP 202 immediately; the caller polls
``GET /api/admin/jobs/overview/``. A cheap DB-level guard rejects a new run while
a same-source run is already ``RUNNING`` so two live fetches can't race.

Every endpoint is explicitly staff-gated. The network boundary is useful
defense-in-depth, not the authorization model.
"""

from __future__ import annotations

import logging
import threading
from io import StringIO
from typing import Callable

from datetime import timedelta

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.db.models import Count
from django.utils import timezone
from rest_framework import status, serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiResponse, extend_schema

from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes

from apps.core.models import Ad, AdVersion, Brand, FetchRun, IngestReject, JobRun, Model
from apps.jobs.services.coverage import COVERAGE_WINDOW_HOURS, find_gaps, known_feed_depth
from apps.jobs.services.health import run_checks
from apps.jobs.services.pipeline import record_job

logger = logging.getLogger("bama.jobs")


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

    The work is wrapped in a JobRun so an operator-triggered job leaves the same
    trace as a scheduled one. Without it these threads returned 202 and then
    vanished: a job that died in the thread was indistinguishable from one that
    finished, because nothing but the response had ever been recorded.
    """

    def _run() -> None:
        try:
            with record_job(command, triggered_by=JobRun.Trigger.ADMIN) as job:
                buf = StringIO()
                call_command(command, stdout=buf, **opts)
                job.detail = buf.getvalue().strip()[:4000]
        except Exception:  # noqa: BLE001 — recorded on the JobRun; nothing to raise to
            logger.exception("admin job %s failed", command)
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
@permission_classes([IsAdminUser])
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
    bounds = {
        "max_ads": (1, settings.BAMA_MAX_ADS),
        "page_pause": (0.0, 60.0),
        "request_timeout": (1, 120),
    }
    for key, (lower, upper) in bounds.items():
        if key in opts and not lower <= opts[key] <= upper:
            return Response(
                {"detail": f"{key} must be between {lower} and {upper}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
    _spawn("fetch_live", **opts)
    return _accepted("fetch_live", poll="GET /api/admin/jobs/overview/")


@extend_schema(
    tags=["Admin · Jobs"],
    request=None,
    responses={202: JobAcceptedSerializer},
    description=(
        "Rebuild every derived analytic — snapshots, market index, deal scores — "
        "without fetching. Operator-only."
    ),
)
@api_view(["POST"])
@permission_classes([IsAdminUser])
def trigger_refresh(request):
    """POST /api/admin/jobs/refresh-analytics/ — rebuild the derived analytics.

    Kept at its original URL but repointed. It used to run ``refresh_analytics``,
    which rebuilt ``PriceStatistics`` — a table no view, serializer or service
    ever read. The button therefore did nothing observable. It now runs the local
    half of the pipeline, which is what "refresh analytics" always implied.
    """
    _spawn("run_pipeline", skip_fetch=True, cadence="full")
    return _accepted("run_pipeline")


@extend_schema(
    tags=["Admin · Jobs"],
    request=None,
    responses={202: JobAcceptedSerializer},
    description="Rebuild per-ad DealScoreCache (the best-deal board). Operator-only.",
)
@api_view(["POST"])
@permission_classes([IsAdminUser])
def trigger_deal_scores(request):
    """POST /api/admin/jobs/deal-scores/ — rebuild DealScoreCache (async)."""
    try:
        opts = {**_opt(request.data, "model", int)}
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    _spawn("compute_deal_scores", **opts)
    return _accepted("compute_deal_scores")


@extend_schema(
    tags=["Admin · Jobs"],
    responses={200: OpenApiTypes.OBJECT},
    description=(
        "Raw provenance for one ad: the stored payload and every version's "
        "payload. Operator-only — this is the whole scraped record, which is why "
        "it is no longer part of the public ad serializer."
    ),
)
@api_view(["GET"])
@permission_classes([IsAdminUser])
def ad_provenance(request, code: str):
    """GET /api/admin/ads/<code>/provenance/ — the unabridged record."""
    ad = get_object_or_404(Ad, code=code)
    versions = (
        AdVersion.objects.filter(ad=ad)
        .order_by("first_observed_at")
        .values("id", "semantic_hash", "raw_hash", "origin", "first_observed_at", "payload")
    )
    return Response({
        "code": ad.code,
        "quality_flags": ad.quality_flags,
        "cohort_flags": ad.cohort_flags,
        "raw_payload": ad.raw_payload,
        "versions": list(versions),
    })


@extend_schema(
    tags=["Admin · Jobs"],
    responses={200: OpenApiTypes.OBJECT},
    description="Recent scheduled-job outcomes, newest first, including skips.",
)
@api_view(["GET"])
@permission_classes([IsAdminUser])
def jobs_overview(request):
    """GET /api/admin/jobs/overview/ — did the scheduled work actually run?

    The question JobRun exists to answer. A step that was skipped because its
    prerequisite failed shows up here as ``skipped``, distinct from both success
    and silence.
    """
    limit = min(int(request.query_params.get("limit", 50)), 200)
    rows = JobRun.objects.all()[:limit].values(
        "name", "status", "triggered_by", "started_at", "finished_at",
        "duration_s", "detail", "error",
    )
    latest: dict[str, dict] = {}
    for row in rows:
        latest.setdefault(row["name"], row)
    return Response({"latest_per_job": list(latest.values()), "recent": list(rows)})


@extend_schema(
    tags=["Admin · Jobs"],
    responses={200: OpenApiResponse(description="All checks passed."),
               503: OpenApiResponse(description="At least one check failed.")},
    description=(
        "Crawl health: sweep freshness, failed runs, ingest-reject spikes, "
        "coverage gaps, ingest progress. Read-only. Operator-only."
    ),
)
@api_view(["GET"])
@permission_classes([IsAdminUser])
def crawl_health(request):
    """GET /api/admin/jobs/crawl-health/ — is the crawler actually working?

    Synchronous and read-only, unlike its POST siblings: it runs a handful of
    aggregate queries and the answer is the point, so there is nothing to poll.
    Returns 503 when unhealthy so an uptime monitor can watch this URL directly.
    """
    checks = run_checks()
    ok = all(c.ok for c in checks)
    return Response(
        {
            "ok": ok,
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail, "data": c.data}
                for c in checks
            ],
        },
        status=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@extend_schema(
    tags=["Admin · Jobs"],
    responses={200: OpenApiTypes.OBJECT},
    description="Database size, catalog counts, recent ingest rejects, crawl checks.",
)
@api_view(["GET"])
@permission_classes([IsAdminUser])
def system_health(request):
    """GET /api/admin/health/ — the one-screen state of the installation.

    Moved here from the deleted accounts admin API, minus the plan-limits block:
    there are no plans any more.
    """
    with connection.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database())")
        db_size = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
        )
        connections = cur.fetchone()[0]

    from django.db.migrations.recorder import MigrationRecorder

    since = timezone.now() - timedelta(hours=24)
    latest_fetch = (
        FetchRun.objects.filter(source=FetchRun.Source.LIVE_FETCH)
        .order_by("-finished_at", "-started_at")
        .values(
            "mode", "status", "stop_reason", "reached_end", "pages_fetched",
            "deepest_rank", "finished_at",
        )
        .first()
    )
    depth = known_feed_depth()
    gaps = find_gaps(
        since=timezone.now() - timedelta(hours=COVERAGE_WINDOW_HOURS),
        max_rank=depth,
    ) if depth else []
    status_counts = {
        row["status"]: row["n"]
        for row in Ad.objects.values("status").annotate(n=Count("code"))
    }
    return Response({
        "database": {
            "size_bytes": db_size,
            "connections": connections,
            "migrations_applied": MigrationRecorder.Migration.objects.count(),
        },
        "catalog": {
            "ads": Ad.objects.count(),
            "active_ads": status_counts.get(Ad.Status.ACTIVE, 0),
            "removed_ads": status_counts.get(Ad.Status.REMOVED, 0),
            "brands": Brand.objects.count(),
            "models": Model.objects.count(),
            "unconfirmed_brands": Brand.objects.filter(is_confirmed=False).count(),
            "unconfirmed_models": Model.objects.filter(is_confirmed=False).count(),
            "rejects_24h": IngestReject.objects.filter(observed_at__gte=since).count(),
            "reject_rules_24h": list(
                IngestReject.objects.filter(observed_at__gte=since)
                .values("rule").annotate(n=Count("id")).order_by("-n")
            ),
        },
        "fetch": {
            "latest": latest_fetch,
            "coverage_depth": depth,
            "coverage_gap_count": len(gaps),
            "coverage_window_hours": COVERAGE_WINDOW_HOURS,
        },
        "crawl": [
            {"name": c.name, "ok": c.ok, "detail": c.detail} for c in run_checks()
        ],
    })
