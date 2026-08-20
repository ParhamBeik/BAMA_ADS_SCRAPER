"""Root URL configuration."""

from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path

from apps.jobs import views as jobs_views


def health(_request):
    return JsonResponse({"status": "ok"})


def db_health(_request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception:
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
    path("api/", include("apps.core.urls")),
]
