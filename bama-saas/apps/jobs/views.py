"""Operator endpoints: trigger a job, read health, read the raw record.

Triggers run in a daemon thread (no Celery — the compose worker loop plus flock
is the scheduler) and return 202 immediately; the caller polls
``GET /api/admin/jobs/overview/``. Every endpoint is explicitly staff-gated: the
network boundary is defence in depth, not the authorization model.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from apps.core.models import Ad, AdVersion, Brand, FetchRun, IngestReject, JobRun, Model
from apps.jobs import jobs, pipeline
from apps.jobs.fetcher import COVERAGE_WINDOW_HOURS, find_gaps, known_feed_depth

logger = logging.getLogger("bama.jobs")

# Bounds for operator-supplied fetch options, so a typo cannot start a 10-hour run.
FETCH_BOUNDS = {"max_ads": (1, None), "page_pause": (0.0, 60.0), "request_timeout": (1, 120)}


def _spawn(label: str, work) -> None:
    """Run ``work()`` in a daemon thread, always closing the DB connection.

    Django hands each thread its own connection; a detached thread outliving the
    request must release it or the pool leaks one per job. The work always goes
    through the pipeline, so an operator-triggered job leaves the same JobRun
    trace as a scheduled one — without that these threads returned 202 and then
    vanished, and a job that died was indistinguishable from one that finished.
    """
    def _run() -> None:
        try:
            work()
        except Exception:  # noqa: BLE001 — already recorded on the JobRun
            logger.exception("admin job %s failed", label)
        finally:
            connection.close()

    threading.Thread(target=_run, daemon=True).start()


def _step(job: str, **opts):
    return lambda: pipeline.run_step(job, triggered_by=JobRun.Trigger.ADMIN, **opts)


def _accepted(label: str) -> Response:
    return Response({"status": "started", "command": label,
                     "poll": "GET /api/admin/jobs/overview/"},
                    status=status.HTTP_202_ACCEPTED)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def trigger_fetch(request):
    """POST /api/admin/jobs/fetch/ — start an async live Bama fetch."""
    if FetchRun.objects.filter(source=FetchRun.Source.LIVE_FETCH,
                               status=FetchRun.Status.RUNNING).exists():
        return Response({"detail": "A live fetch is already running."},
                        status=status.HTTP_409_CONFLICT)
    opts = {}
    for key, cast in (("max_ads", int), ("page_pause", float), ("request_timeout", int)):
        raw = request.data.get(key)
        if raw in (None, "", "null"):
            continue
        try:
            value = cast(raw)
        except (TypeError, ValueError):
            return Response({"detail": f"{key} must be a {cast.__name__}"},
                            status=status.HTTP_400_BAD_REQUEST)
        lower, upper = FETCH_BOUNDS[key]
        upper = settings.BAMA_MAX_ADS if upper is None else upper
        if not lower <= value <= upper:
            return Response({"detail": f"{key} must be between {lower} and {upper}."},
                            status=status.HTTP_400_BAD_REQUEST)
        opts[key] = value
    _spawn("fetch", _step("fetch", **opts))
    return _accepted("fetch")


@api_view(["POST"])
@permission_classes([IsAdminUser])
def trigger_refresh(request):
    """POST /api/admin/jobs/refresh-analytics/ — rebuild every derived analytic.

    Runs the local half of the pipeline: snapshots, index, deal scores. No fetch.
    """
    _spawn("refresh-analytics", lambda: pipeline.run(
        cadence="full", skip_fetch=True, triggered_by=JobRun.Trigger.ADMIN,
    ))
    return _accepted("refresh-analytics")


@api_view(["POST"])
@permission_classes([IsAdminUser])
def trigger_deal_scores(request):
    """POST /api/admin/jobs/deal-scores/ — rebuild the deal board (async)."""
    raw = request.data.get("model")
    opts = {}
    if raw not in (None, "", "null"):
        try:
            opts["model"] = int(raw)
        except (TypeError, ValueError):
            return Response({"detail": "model must be an integer"},
                            status=status.HTTP_400_BAD_REQUEST)
    _spawn("deal_scores", _step("deal_scores", **opts))
    return _accepted("deal_scores")


@api_view(["GET"])
@permission_classes([IsAdminUser])
def jobs_overview(request):
    """GET /api/admin/jobs/overview/ — did the scheduled work actually run?

    A step skipped because its prerequisite failed shows up as ``skipped``,
    distinct from both success and silence.
    """
    limit = min(int(request.query_params.get("limit", 50)), 200)
    rows = list(JobRun.objects.all()[:limit].values(
        "name", "status", "triggered_by", "started_at", "finished_at",
        "duration_s", "detail", "error",
    ))
    latest: dict[str, dict] = {}
    for row in rows:
        latest.setdefault(row["name"], row)
    return Response({"latest_per_job": list(latest.values()), "recent": rows})


@api_view(["GET"])
@permission_classes([IsAdminUser])
def crawl_health(request):
    """GET /api/admin/jobs/crawl-health/ — is the crawler actually working?

    503 when unhealthy, so an uptime monitor can watch this URL directly.
    """
    result = jobs.health()
    return Response(result, status=status.HTTP_200_OK if result["ok"]
                    else status.HTTP_503_SERVICE_UNAVAILABLE)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def ad_provenance(request, code: str):
    """GET /api/admin/ads/<code>/provenance/ — the unabridged record.

    Operator-only: this is the whole scraped payload, which is why it is no
    longer part of the public ad serializer.
    """
    ad = get_object_or_404(Ad, code=code)
    return Response({
        "code": ad.code,
        "quality_flags": ad.quality_flags,
        "cohort_flags": ad.cohort_flags,
        "raw_payload": ad.raw_payload,
        "versions": list(
            AdVersion.objects.filter(ad=ad).order_by("first_observed_at")
            .values("id", "semantic_hash", "raw_hash", "origin",
                    "first_observed_at", "payload")
        ),
    })


@api_view(["GET"])
@permission_classes([IsAdminUser])
def system_health(request):
    """GET /api/admin/health/ — the one-screen state of the installation."""
    with connection.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database())")
        db_size = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
        connections = cur.fetchone()[0]

    from django.db.migrations.recorder import MigrationRecorder

    since = timezone.now() - timedelta(hours=24)
    latest_fetch = (
        FetchRun.objects.filter(source=FetchRun.Source.LIVE_FETCH)
        .order_by("-finished_at", "-started_at")
        .values("mode", "status", "stop_reason", "reached_end", "pages_fetched",
                "deepest_rank", "finished_at").first()
    )
    depth = known_feed_depth()
    gaps = find_gaps(since=timezone.now() - timedelta(hours=COVERAGE_WINDOW_HOURS),
                     max_rank=depth) if depth else []
    status_counts = {
        row["status"]: row["n"]
        for row in Ad.objects.values("status").annotate(n=Count("code"))
    }
    rejects = IngestReject.objects.filter(observed_at__gte=since)
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
            # Ads nobody has seen for two windows that coverage could not prove
            # anything about. A non-zero value here means the crawler is blind,
            # not that the market shrank.
            "unverified_ads": status_counts.get(Ad.Status.UNVERIFIED, 0),
            "brands": Brand.objects.count(),
            "models": Model.objects.count(),
            "unconfirmed_brands": Brand.objects.filter(is_confirmed=False).count(),
            "unconfirmed_models": Model.objects.filter(is_confirmed=False).count(),
            "rejects_24h": rejects.count(),
            "reject_rules_24h": list(
                rejects.values("rule").annotate(n=Count("id")).order_by("-n")
            ),
        },
        "fetch": {
            "latest": latest_fetch,
            "coverage_depth": depth,
            "coverage_gap_count": len(gaps),
            "coverage_window_hours": COVERAGE_WINDOW_HOURS,
        },
        "crawl": jobs.health()["checks"],
    })
