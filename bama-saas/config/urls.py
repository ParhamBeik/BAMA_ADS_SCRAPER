"""Root URL configuration."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from apps.jobs import views as jobs_views


from django.db import connection

def health(_request):
    return JsonResponse({"status": "ok"})


def db_health(_request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception as exc:
        return JsonResponse({"status": "error", "detail": str(exc)}, status=500)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/db/health/", db_health, name="db-health"),  # wired to a real DB check in Phase 2
    # Auto-generated OpenAPI schema + docs UI (Phase 6).
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/", include("apps.accounts.urls")),
    path("api/admin/jobs/", include("apps.jobs.urls")),
    # Staff-only replacement for the raw_payload that used to ride along on every
    # public ad response.
    path(
        "api/admin/ads/<str:code>/provenance/",
        jobs_views.ad_provenance,
        name="ad-provenance",
    ),
    path("api/", include("apps.core.urls")),
]
