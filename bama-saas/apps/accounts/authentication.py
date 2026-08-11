"""JWT auth from HTTP-only cookies, with Bearer header fallback."""

from __future__ import annotations

from django.conf import settings
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken


class CookieJWTAuthentication(JWTAuthentication):
    """Read the access token from a cookie first, then Authorization header."""

    def authenticate(self, request):
        raw = request.COOKIES.get(settings.AUTH_ACCESS_COOKIE)
        if raw:
            try:
                validated = self.get_validated_token(raw)
            except InvalidToken:
                return None
            return self.get_user(validated), validated
        return super().authenticate(request)
