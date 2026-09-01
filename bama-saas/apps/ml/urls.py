"""Routes for the learned layer, all under ``/api/``.

``prediction`` hangs off the ad rather than off ``/ml/`` because it is a fact
about one listing and the client already has the code; the rest are about the
models themselves.
"""

from django.urls import path

from apps.ml import views

app_name = "ml"

urlpatterns = [
    path("ml/models/", views.models_view, name="models"),
    path("ml/monitoring/", views.monitoring_view, name="monitoring"),
    path("ml/review-queue/", views.review_queue_view, name="review-queue"),
    path("ads/<str:code>/prediction/", views.prediction_view, name="ad-prediction"),
]
