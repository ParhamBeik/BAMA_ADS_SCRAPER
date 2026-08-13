"""Operator job-trigger URL configuration."""

from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("fetch/", views.trigger_fetch, name="trigger-fetch"),
    path("refresh-analytics/", views.trigger_refresh, name="trigger-refresh"),
    path("deal-scores/", views.trigger_deal_scores, name="trigger-deal-scores"),
    # Read-only GETs, unlike the POST triggers above.
    path("crawl-health/", views.crawl_health, name="crawl-health"),
    path("overview/", views.jobs_overview, name="jobs-overview"),
]
