"""Mounted at ``/api/``: auth at ``/api/auth/*``, saved ads at ``/api/favorites/``.

The SPA uses the cookie endpoints (``login`` / ``register`` / ``logout`` /
``me``) and never the token ones. ``token/*`` exists for API clients that have
nowhere to keep a cookie; both paths authenticate the same users against the
same permissions.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt import views as jwt

from apps.accounts import views

app_name = "accounts"

router = DefaultRouter()
router.register("favorites", views.FavoriteViewSet, basename="favorite")
# The per-user layer. All three are scoped to request.user in get_queryset, so
# an id in the URL can only ever reach the caller's own rows.
router.register("watchlists", views.WatchlistViewSet, basename="watchlist")
router.register("alert-rules", views.AlertRuleViewSet, basename="alert-rule")
router.register("alerts", views.AlertViewSet, basename="alert")


class _ThrottledTokenView(jwt.TokenObtainPairView):
    """Password-to-token exchange, throttled like the login form it mirrors.

    Without this the JWT endpoint is an unthrottled bypass around the brute-force
    guard on ``/api/auth/login/``.
    """

    throttle_classes = views.LoginView.throttle_classes
    throttle_scope = "login"


urlpatterns = [
    path("auth/me/", views.MeView.as_view(), name="auth-me"),
    path("auth/register/", views.RegisterView.as_view(), name="auth-register"),
    path("auth/email-available/", views.EmailAvailableView.as_view(),
         name="auth-email-available"),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/logout-everywhere/", views.LogoutEverywhereView.as_view(),
         name="auth-logout-everywhere"),
    path("auth/token/", _ThrottledTokenView.as_view(), name="auth-token"),
    path("auth/token/refresh/", jwt.TokenRefreshView.as_view(), name="auth-token-refresh"),
    path("auth/token/verify/", jwt.TokenVerifyView.as_view(), name="auth-token-verify"),
    path("", include(router.urls)),
]
