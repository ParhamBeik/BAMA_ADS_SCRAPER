"""Fixtures shared across the suite.

Everything here was duplicated in three or more files before. Cohort fixtures
stay local to their module on purpose: the brand/model names they assert on are
part of what the test is saying.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
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
def api_client() -> APIClient:
    """Anonymous DRF client."""
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


@pytest.fixture
def make_payload():
    """Build a raw Bama payload the way the feed sends one."""
    def build(code, price, phrase="2 ساعت پیش", brand="پژو", model="405", trim="دنده‌ای"):
        return {
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
