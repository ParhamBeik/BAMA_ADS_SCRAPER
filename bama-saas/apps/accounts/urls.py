"""Saved-ads and session-login URL configuration.

Mounted at ``/api/`` (see config/urls.py), so the router resolves at
``/api/favorites/`` and auth at ``/api/auth/*``.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views, views_auth

app_name = "accounts"

router = DefaultRouter()
router.register("favorites", views.FavoriteViewSet, basename="favorite")

urlpatterns = [
    path("auth/me/", views_auth.MeView.as_view(), name="auth-me"),
    path("auth/register/", views_auth.RegisterView.as_view(), name="auth-register"),
    path("auth/login/", views_auth.LoginView.as_view(), name="auth-login"),
    path("auth/logout/", views_auth.LogoutView.as_view(), name="auth-logout"),
    path("", include(router.urls)),
]
