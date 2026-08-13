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
from apps.accounts import admin_api
from apps.core.views import inspect as inspect_api


from django.db import connection

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
    path("api/db/health/", db_health, name="db-health"),  # wired to a real DB check in Phase 2
    # Auto-generated OpenAPI schema + docs UI (Phase 6).
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/", include("apps.accounts.urls")),
    path("api/admin/jobs/", include("apps.jobs.urls")),
    path("api/admin/users/", admin_api.AdminUsersView.as_view(), name="admin-users"),
    path("api/admin/users/<uuid:user_id>/", admin_api.AdminUserDetailView.as_view(), name="admin-user-detail"),
    path("api/admin/pro-requests/", admin_api.AdminProRequestsView.as_view(), name="admin-pro-requests"),
    path("api/admin/pro-requests/<uuid:request_id>/", admin_api.AdminProRequestActionView.as_view(), name="admin-pro-action"),
    path("api/admin/health/", admin_api.AdminHealthView.as_view(), name="admin-health"),
    path("api/admin/review/", admin_api.AdminReviewQueueView.as_view(), name="admin-review"),
    path("api/admin/review/confirm/", admin_api.AdminConfirmDimensionView.as_view(), name="admin-confirm"),
    path("api/admin/audit/", admin_api.AdminAuditLogView.as_view(), name="admin-audit"),
    # Staff-only replacement for the raw_payload that used to ride along on every
    # public ad response.
    path(
        "api/admin/ads/<str:code>/provenance/",
        jobs_views.ad_provenance,
        name="ad-provenance",
    ),
    path("api/admin/inspect/ads/", inspect_api.InspectAdsView.as_view(), name="inspect-ads"),
    path("api/admin/inspect/ads/<str:code>/", inspect_api.InspectAdDetailView.as_view(), name="inspect-ad"),
    path("api/admin/inspect/brands/", inspect_api.InspectBrandsView.as_view(), name="inspect-brands"),
    path("api/admin/inspect/models/", inspect_api.InspectModelsView.as_view(), name="inspect-models"),
    path("api/admin/inspect/variants/", inspect_api.InspectVariantsView.as_view(), name="inspect-variants"),
    path("api/admin/inspect/fetch-runs/", inspect_api.InspectFetchRunsView.as_view(), name="inspect-fetch-runs"),
    path(
        "api/admin/inspect/fetch-runs/<uuid:run_id>/pages/",
        inspect_api.InspectFetchRunPagesView.as_view(),
        name="inspect-fetch-run-pages",
    ),
    path("api/admin/inspect/gaps/", inspect_api.InspectGapsView.as_view(), name="inspect-gaps"),
    path("api/admin/inspect/rejects/", inspect_api.InspectRejectsView.as_view(), name="inspect-rejects"),
    path("api/", include("apps.core.urls")),
]
