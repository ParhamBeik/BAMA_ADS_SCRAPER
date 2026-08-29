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
            _RUNNING.discard(label)
            connection.close()

    _RUNNING.add(label)
    threading.Thread(target=_run, daemon=True).start()


# Jobs this process has in flight. A `set` is enough: these threads are spawned
# here and only here, and there is one web process — the same assumption the
# module docstring already makes about the worker loop being the scheduler.
_RUNNING: set[str] = set()


def _already_running(label: str, *steps: str) -> Response | None:
    """409 if this job is in flight, or was left running by an earlier request.

    `trigger_fetch` has always refused a second concurrent run; the other three
    accepted any number. Firing `deal_scores` three times in one second gave
    three 202s and three threads rebuilding the same tables — a full rebuild
    `DELETE`s every row before it writes, so two overlapping runs can leave the
    board empty for as long as the slower one takes.

    Both halves are needed: the in-process set catches the double-click before
    any row exists, and the JobRun check catches a job started by the worker
    loop or by a web process that has since been replaced.

    ``steps`` names the pipeline steps to look for when they are not the label
    itself — `refresh-analytics` runs several and writes a JobRun under each of
    their names, never under its own.
    """
    busy = label in _RUNNING or JobRun.objects.filter(
        name__in=steps or (label,),
        status=JobRun.Status.RUNNING,
        started_at__gte=timezone.now() - timedelta(hours=JOB_STALE_AFTER_HOURS),
    ).exists()
    if not busy:
        return None
    return Response({"detail": f"«{label}» is already running.", "job": label},
                    status=status.HTTP_409_CONFLICT)


# A RUNNING row older than this is a job whose process died mid-write, not a job
# still working. Without an upper bound one crash would lock the button forever.
JOB_STALE_AFTER_HOURS = 6


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
    # Guarded on `deal_scores` as well as on itself: that is the step this
    # shares with the button next to it, and it deletes every row on the board
    # before rebuilding, so two overlapping runs can leave it empty.
    if busy := _already_running("refresh-analytics", "refresh-analytics", "deal_scores"):
        return busy
    _spawn("refresh-analytics", lambda: pipeline.run(
        cadence="full", skip_fetch=True, triggered_by=JobRun.Trigger.ADMIN,
    ))
    return _accepted("refresh-analytics")


@api_view(["POST"])
@permission_classes([IsAdminUser])
def trigger_deal_scores(request):
    """POST /api/admin/jobs/deal-scores/ — rebuild the deal board (async)."""
    if busy := _already_running("deal_scores", "deal_scores", "refresh-analytics"):
        return busy
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


@api_view(["POST"])
@permission_classes([IsAdminUser])
def trigger_backfill_images(request):
    """POST /api/admin/jobs/backfill-images/ — refill photos from stored payloads.

    Local and idempotent (no network), but it walks every photoless ad, so it is
    an operator action rather than something to run on the hot cadence.
    """
    if busy := _already_running("backfill_images"):
        return busy
    _spawn("backfill_images", _step("backfill_images"))
    return _accepted("backfill_images")


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
    return Response({
        "latest_per_job": list(latest.values()),
        "recent": rows,
        # What is in flight right now, so the panel's buttons can say so. The
        # trigger endpoints return 202 the instant the thread starts, which the
        # UI was treating as "done" — the buttons never disabled, nothing span,
        # and firing one three times in a second was possible and did happen.
        "running": sorted(
            JobRun.objects.filter(
                status=JobRun.Status.RUNNING,
                started_at__gte=timezone.now() - timedelta(hours=JOB_STALE_AFTER_HOURS),
            ).values_list("name", flat=True).distinct()
        ),
        "fetch_running": FetchRun.objects.filter(
            source=FetchRun.Source.LIVE_FETCH, status=FetchRun.Status.RUNNING,
        ).exists(),
    })


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
