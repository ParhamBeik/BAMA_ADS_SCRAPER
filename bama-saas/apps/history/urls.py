"""History URL configuration.

Routes are mounted under /api/ (see config/urls.py), so the prefixes below are
relative to /api/.
"""

from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AdChangesView,
    AdTimelineView,
    AdVersionsView,
    ChangeViewSet,
    FetchRunViewSet,
    ObservationViewSet,
)

app_name = "history"

router = DefaultRouter()
router.register(r"changes", ChangeViewSet, basename="change")
router.register(r"observations", ObservationViewSet, basename="observation")
router.register(r"fetch-runs", FetchRunViewSet, basename="fetchrun")

urlpatterns = [
    # Per-ad provenance (nested under ads/<code>/).
    path("ads/<str:code>/versions/", AdVersionsView.as_view(), name="ad-versions"),
    path("ads/<str:code>/changes/", AdChangesView.as_view(), name="ad-changes"),
    path("ads/<str:code>/timeline/", AdTimelineView.as_view(), name="ad-timeline"),
] + router.urls
