"""Analytics URL configuration."""

from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("insights/<int:model_id>/<str:kind>/", views.insight, name="insight"),
]
