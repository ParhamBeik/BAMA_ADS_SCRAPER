"""Account URL configuration.

The accounts app is mounted at ``/api/`` (see config/urls.py), so the auth
routes are self-prefixed with ``auth/`` and resolve at ``/api/auth/...``, while
the Phase-5 engagement routes (favorites, watchlists, saved-searches, alerts,
notifications) resolve at the cleaner ``/api/<resource>/``.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from . import views

app_name = "accounts"

router = DefaultRouter()
router.register("favorites", views.FavoriteViewSet, basename="favorite")
router.register("watchlists", views.WatchlistViewSet, basename="watchlist")
router.register("saved-searches", views.SavedSearchViewSet, basename="saved-search")
router.register("alerts", views.AlertViewSet, basename="alert")
router.register("notifications", views.NotificationViewSet, basename="notification")

urlpatterns = [
    # Auth (under /api/auth/...).
    path("auth/register/", views.RegisterView.as_view(), name="register"),
    path("auth/login/", TokenObtainPairView.as_view(), name="login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="refresh"),
    path("auth/me/", views.MeView.as_view(), name="me"),
    # Phase-5 engagement routes (under /api/<resource>/).
    path("", include(router.urls)),
]
