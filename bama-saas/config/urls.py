"""Root URL configuration."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/db/health/", health, name="db-health"),  # wired to a real DB check in Phase 2
    path("api/auth/", include("apps.accounts.urls")),
    path("api/admin/jobs/", include("apps.jobs.urls")),
    path("api/", include("apps.catalog.urls")),
    path("api/", include("apps.market.urls")),
    path("api/", include("apps.history.urls")),
    path("api/", include("apps.analytics.urls")),
]
