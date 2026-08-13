"""Saved-ads URL configuration.

Mounted at ``/api/`` (see config/urls.py), so the router resolves at
``/api/favorites/``. The auth routes that used to live here are gone with the
rest of the SaaS layer.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

app_name = "accounts"

router = DefaultRouter()
router.register("favorites", views.FavoriteViewSet, basename="favorite")

urlpatterns = [
    path("", include(router.urls)),
]
