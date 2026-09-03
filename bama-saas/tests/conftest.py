"""Fixtures shared across the suite.

Everything here was duplicated in three or more files before. Cohort fixtures
stay local to their module on purpose: the brand/model names they assert on are
part of what the test is saying.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from django.conf import settings
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.jobs.ingest import reset_cache, reset_price_cache

UTC = timezone.utc

# A fixed "now" so publish_at / observed_at derived from it are deterministic.
NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean_ingest_caches():
    """Both ingest caches are module-global and outlive a test.

    A cached AdVersion whose row was rolled back is the exact shape of the
    foreign-key violation that used to take a whole fetch run down, so every
    test starts and ends cold.
    """
    reset_cache()
    reset_price_cache()
    yield
    reset_cache()
    reset_price_cache()


@pytest.fixture(autouse=True)
def _reset_throttles():
    """DRF keeps throttle history in the process-wide cache, not the database.

    Without this, the 5/min register limit is consumed by whichever auth tests
    ran first and every later one gets a 429 — a green suite that silently stops
    testing what it claims to.
    """
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_client(db) -> APIClient:
    """DRF client for API tests.

    In open profile (API_PUBLIC_READS=1), anonymous requests are permitted.
    In hardened profile (API_PUBLIC_READS not set), authenticates as a regular user
    so business-logic tests continue to exercise endpoints under closed defaults.
    """
    client = APIClient()
    if not getattr(settings, "API_PUBLIC_READS", False):
        from apps.accounts.models import User

        user = User.objects.create_user(
            email="api_client_fixture@example.com", password="StrongPass1!"
        )
        client.force_authenticate(user)
    return client


@pytest.fixture
def anonymous_client() -> APIClient:
    """Always-unauthenticated DRF client."""
    return APIClient()


@pytest.fixture
def staff_client(db) -> APIClient:
    """Client authenticated as a superuser, for the operator endpoints."""
    from apps.accounts.models import User

    client = APIClient()
    client.force_authenticate(
        User.objects.create_superuser(email="ops@example.com", password="StrongPass1!")
    )
    return client


# The feed is crawled with `image=1&priced=1`, so every payload it returns
# carries a gallery. Fixtures must too, or they exercise a shape the crawler
# cannot produce — and `_photo_missing` (hard) would reject all of them.
CDN = "https://cdn-sth1.bama.ir/uploads/BamaImages/VehicleCarImages"


def gallery(code, n=3):
    """The top-level `images` block, one entry per photo at three widths."""
    return [
        {"large": f"{CDN}/{code}/{i}.jpg?x-img=v1/resize,w_600",
         "small": f"{CDN}/{code}/{i}.jpg?x-img=v1/resize,w_450",
         "thumb": f"{CDN}/{code}/{i}.jpg?x-img=v1/resize,w_90"}
        for i in range(n)
    ]


@pytest.fixture
def make_payload():
    """Build a raw Bama payload the way the feed sends one."""
    def build(code, price, phrase="2 ساعت پیش", brand="پژو", model="405", trim="دنده‌ای"):
        return {
            "images": gallery(code),
            "detail": {
                "code": code,
                "title": f"{brand}، {model}",
                "brand_fa": brand,
                "trim": trim,
                "year": "1399",
                "mileage": "120,000",
                "type": "car",
                "time": phrase,
                "url": f"https://bama.ir/cad/{code}",
                "location": "تهران - مرکز",
                "transmission": "دنده‌ای",
            },
            "price": {
                "price": str(price),
                "type": "lumpsum",
                "payment": "0",
                "prepayment": "0",
                "installments": "0",
            },
        }

    return build
