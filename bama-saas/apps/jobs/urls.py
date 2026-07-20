"""Admin job-trigger URL configuration (operator-only, IsStaff-gated)."""

from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("fetch/", views.trigger_fetch, name="trigger-fetch"),
    path("import/", views.trigger_import, name="trigger-import"),
    path("refresh-analytics/", views.trigger_refresh, name="trigger-refresh"),
    path("deal-scores/", views.trigger_deal_scores, name="trigger-deal-scores"),
    path("evaluate-alerts/", views.trigger_evaluate_alerts, name="trigger-evaluate-alerts"),
]
