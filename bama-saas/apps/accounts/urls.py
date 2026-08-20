"""Mounted at ``/api/``: auth at ``/api/auth/*``, saved ads at ``/api/favorites/``."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.accounts import views

app_name = "accounts"

router = DefaultRouter()
router.register("favorites", views.FavoriteViewSet, basename="favorite")

urlpatterns = [
    path("auth/me/", views.MeView.as_view(), name="auth-me"),
    path("auth/register/", views.RegisterView.as_view(), name="auth-register"),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("", include(router.urls)),
]
