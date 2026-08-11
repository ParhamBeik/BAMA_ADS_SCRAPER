"""HTTP-only cookie helpers for JWT session auth."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponseBase


def _cookie_kwargs(max_age: int) -> dict:
    return {
        "httponly": True,
        "secure": settings.AUTH_COOKIE_SECURE,
        "samesite": settings.AUTH_COOKIE_SAMESITE,
        "domain": settings.AUTH_COOKIE_DOMAIN,
        "max_age": max_age,
        "path": "/",
    }


def set_auth_cookies(response: HttpResponseBase, access: str, refresh: str) -> None:
    access_lifetime = int(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds())
    refresh_lifetime = int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds())
    response.set_cookie(settings.AUTH_ACCESS_COOKIE, access, **_cookie_kwargs(access_lifetime))
    response.set_cookie(settings.AUTH_REFRESH_COOKIE, refresh, **_cookie_kwargs(refresh_lifetime))


def clear_auth_cookies(response: HttpResponseBase) -> None:
    for name in (settings.AUTH_ACCESS_COOKIE, settings.AUTH_REFRESH_COOKIE):
        response.delete_cookie(name, path="/", domain=settings.AUTH_COOKIE_DOMAIN)
