"""Account URL configuration.

The accounts app is mounted at ``/api/`` (see config/urls.py), so the auth
routes are self-prefixed with ``auth/`` and resolve at ``/api/auth/...``, while
engagement routes resolve at ``/api/<resource>/``.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import auth_views, views

app_name = "accounts"

router = DefaultRouter()
router.register("favorites", views.FavoriteViewSet, basename="favorite")
router.register("alerts", views.AlertViewSet, basename="alert")
router.register("notifications", views.NotificationViewSet, basename="notification")

urlpatterns = [
    path("auth/register/", auth_views.CookieRegisterView.as_view(), name="register"),
    path("auth/login/", auth_views.CookieLoginView.as_view(), name="login"),
    path("auth/refresh/", auth_views.CookieRefreshView.as_view(), name="refresh"),
    path("auth/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("auth/logout-all/", auth_views.LogoutAllView.as_view(), name="logout-all"),
    path("auth/me/", auth_views.MeView.as_view(), name="me"),
    path("auth/verify/", auth_views.VerifyEmailView.as_view(), name="verify"),
    path("auth/resend-verification/", auth_views.ResendVerificationView.as_view(), name="resend-verification"),
    path("auth/password-reset/", auth_views.PasswordResetRequestView.as_view(), name="password-reset"),
    path("auth/password-reset/confirm/", auth_views.PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    path("auth/delete/", auth_views.DeleteAccountView.as_view(), name="delete-account"),
    path("auth/restore/", auth_views.RestoreAccountView.as_view(), name="restore-account"),
    path("auth/usage/", auth_views.AccountUsageView.as_view(), name="account-usage"),
    path("auth/pro-request/", auth_views.ProRequestView.as_view(), name="pro-request"),
    path("", include(router.urls)),
]
