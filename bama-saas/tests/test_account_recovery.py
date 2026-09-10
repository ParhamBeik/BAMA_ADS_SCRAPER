"""Password change and the followed-car digest.

API/integration: these facts live on the HTTP response (session cookie still
valid, old password refused, digest tenant-scoped). Unit tests would not see
the cookie or the permission gate.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.models import User, Watchlist
from apps.core.models import Brand, MarketIndex, Model
from tests.conftest import UTC
from tests.test_api import _register


@pytest.fixture
def member(db):
    client = APIClient()
    user = User.objects.create_user(email="digest-member@example.com", password="StrongPass1!")
    client.force_authenticate(user)
    return client, user


@pytest.fixture
def other_member(db):
    client = APIClient()
    user = User.objects.create_user(email="digest-other@example.com", password="StrongPass1!")
    client.force_authenticate(user)
    return client, user


@pytest.fixture
def a_model(db):
    brand = Brand.objects.create(slug="digestbrand", name_fa="برند تست")
    model = Model.objects.create(brand=brand, name_fa="مدل تست")
    return brand, model


@pytest.mark.django_db
def test_password_change_requires_a_session(anonymous_client):
    assert anonymous_client.post(
        "/api/auth/password/",
        {"current_password": "x", "new_password": "NewStrongPass2!"},
        format="json",
    ).status_code in (401, 403)


@pytest.mark.django_db
def test_password_change_keeps_this_session_and_rejects_the_old_secret(anonymous_client):
    _register(anonymous_client, "owner@example.com")

    wrong = anonymous_client.post(
        "/api/auth/password/",
        {"current_password": "wrong", "new_password": "NewStrongPass2!"},
        format="json",
    )
    assert wrong.status_code == 400

    same = anonymous_client.post(
        "/api/auth/password/",
        {"current_password": "StrongPass1!", "new_password": "StrongPass1!"},
        format="json",
    )
    assert same.status_code == 400

    weak = anonymous_client.post(
        "/api/auth/password/",
        {"current_password": "StrongPass1!", "new_password": "123"},
        format="json",
    )
    assert weak.status_code == 400

    ok = anonymous_client.post(
        "/api/auth/password/",
        {"current_password": "StrongPass1!", "new_password": "NewStrongPass2!"},
        format="json",
    )
    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    assert anonymous_client.get("/api/auth/me/").json()["authenticated"] is True

    other = APIClient()
    assert other.post(
        "/api/auth/login/",
        {"email": "owner@example.com", "password": "StrongPass1!"},
        format="json",
    ).status_code == 401
    assert other.post(
        "/api/auth/login/",
        {"email": "owner@example.com", "password": "NewStrongPass2!"},
        format="json",
    ).status_code == 200


@pytest.mark.django_db
def test_password_change_blacklists_jwt_tokens():
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
    from rest_framework_simplejwt.tokens import RefreshToken

    user = User.objects.create_user(email="pw_jwt@example.com", password="StrongPass1!")
    refresh = RefreshToken.for_user(user)
    client = APIClient()
    client.force_authenticate(user)
    resp = client.post(
        "/api/auth/password/",
        {"current_password": "StrongPass1!", "new_password": "NewStrongPass2!"},
        format="json",
    )
    assert resp.status_code == 200
    assert BlacklistedToken.objects.filter(token__token=str(refresh)).exists()


@pytest.mark.django_db
def test_watchlist_digest_requires_a_session(anonymous_client):
    assert anonymous_client.get("/api/watchlists/digest/").status_code in (401, 403)


@pytest.mark.django_db
def test_watchlist_digest_carries_the_index_move(member, a_model):
    client, _ = member
    brand, model = a_model
    today = datetime.now(UTC).date()
    for offset in range(5):
        MarketIndex.objects.create(
            scope="model", scope_id=str(model.pk),
            date=today - timedelta(days=4 - offset),
            index_value=100.0 + offset,
            return_pct=None, cohort_count=8, ad_count=40,
        )
    client.post(
        "/api/watchlists/",
        {"model": model.pk, "brand_slug": brand.slug},
        format="json",
    )
    body = client.get("/api/watchlists/digest/").json()
    assert body["count"] == 1
    row = body["results"][0]
    assert row["available"] is True
    assert row["change_pct"] == 4.0
    assert "model=" in row["analyse_path"]
    assert len(row["spark"]) == 5


@pytest.mark.django_db
def test_watchlist_digest_is_invisible_to_another_account(member, other_member, a_model):
    client, _ = member
    other_client, _ = other_member
    _, model = a_model
    client.post("/api/watchlists/", {"model": model.pk}, format="json")
    assert other_client.get("/api/watchlists/digest/").json()["results"] == []


@pytest.mark.django_db
def test_watchlist_digest_cost_does_not_grow_with_the_number_followed(member, a_model):
    """The digest must not cost a query per followed car.

    It did: measured at fifteen followed scopes it ran seventeen queries, one per
    row. Brand and model scopes read a persisted series, and those are now one
    query per scope *kind* — so tripling the rows must not move the count at all.

    Trim and model-year scopes have no persisted series and are computed from
    daily snapshots. They stay per scope by design: each is an index range scan
    over one cohort (``snap_cohort_date_idx``), and they share the Analyse
    screen's cache key, so the repeat cost across readers and reloads is zero.
    This test is about the row-shaped half, which is the one that was unbounded.
    """
    client, user = member
    brand, _ = a_model
    models = [Model.objects.create(brand=brand, name_fa=f"مدل {i}") for i in range(12)]

    def cost(n_models: int) -> int:
        Watchlist.objects.filter(user=user).delete()
        Watchlist.objects.create(user=user, brand_slug=brand.slug)
        for m in models[:n_models]:
            Watchlist.objects.create(user=user, model=m)
        # Cold cache each time, or the second measurement would be cheap for the
        # wrong reason — the point is the shape of the work, not a warm Redis.
        cache.clear()
        with CaptureQueriesContext(connection) as captured:
            assert client.get("/api/watchlists/digest/").status_code == 200
        return len(captured.captured_queries)

    assert cost(12) == cost(4)
