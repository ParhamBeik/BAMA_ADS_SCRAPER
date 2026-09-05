"""Root URL configuration."""

import logging

from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path

from apps.jobs import views as jobs_views

log = logging.getLogger("bama.health")


def health(_request):
    return JsonResponse({"status": "ok"})


def db_health(_request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception:
        # Logged, not just answered. The body stays a bare "error" — this is
        # unauthenticated and a connection string in a probe response is a leak
        # — but the deploy smoke test and the operator both learn only that the
        # database is unreachable, and the reason has to survive somewhere. It
        # cannot reach `django.request`: that logger fires on an *unhandled*
        # exception, and this one is handled in order to return a 500 body at
        # all, so without this line the cause reaches nothing.
        log.exception("db health check failed")
        return JsonResponse({"status": "error"}, status=500)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/db/health/", db_health, name="db-health"),
    path("api/", include("apps.accounts.urls")),
    path("api/admin/jobs/", include("apps.jobs.urls")),
    path("api/admin/health/", jobs_views.system_health, name="admin-health"),
    # The raw payload that used to ride along on every public ad response.
    path("api/admin/ads/<str:code>/provenance/", jobs_views.ad_provenance,
         name="ad-provenance"),
    # Before `core`, which registers a DefaultRouter at `api/ads/` whose
    # catch-all detail route would otherwise swallow `api/ads/<code>/prediction/`.
    path("api/", include("apps.ml.urls")),
    path("api/", include("apps.core.urls")),
]
