"""Phase 5 — engagement CRUD, alerts, notifications, digest.

Self-contained DB-backed tests for the premium feature set. Reuses the same
JWT ``APIClient`` auth pattern as test_api.py (register → login → bearer
token) but re-implements the small auth helper locally to avoid importing
private helpers from a sibling test module.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.test import override_settings
from rest_framework.test import APIClient

from apps.accounts.models import (
    Alert,
    Favorite,
    Notification,
    Subscription,
    User,
)
from apps.core.models import Ad, Brand, City, Model, Variant
from apps.core.models import PriceDropEvent


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _register_and_login(client: APIClient, email: str, password: str = "Sup3rSecret!") -> str:
    """Register then log in, returning a JWT access token."""
    client.post(
        "/api/auth/register/",
        {"email": email, "password": password, "full_name": "Tester"},
        format="json",
    )
    resp = client.post(
        "/api/auth/login/", {"email": email, "password": password}, format="json"
    )
    assert resp.status_code == 200, resp.content
    return resp.json()["access"]


def _authed_client(email: str) -> tuple[APIClient, User, str]:
    client = APIClient()
    token = _register_and_login(client, email)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    user = User.objects.get(email=email)
    if not user.email_verified_at:
        from django.utils import timezone
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])
    return client, user, token


@pytest.fixture
def catalog(db):
    """Minimal catalog slice: brand, model, variant, city, two priced ads."""
    brand = Brand.objects.create(slug="brand1", name_fa="برند")
    model = Model.objects.create(brand=brand, name_fa="مدل")
    variant = Variant.objects.create(model=model, name_fa="دنده‌ای")
    city = City.objects.create(name_fa="تهران", province="تهران")
    ad1 = Ad.objects.create(
        code="c1", brand=brand, model=model, variant=variant, city=city,
        title="آگهی یک", year=1399, mileage=100_000,
        current_price=1_000_000_000, publish_at="2026-07-10T00:00:00Z",
    )
    ad2 = Ad.objects.create(
        code="c2", brand=brand, model=model, variant=variant, city=city,
        title="آگهی دو", year=1398, mileage=120_000,
        current_price=1_100_000_000, publish_at="2026-07-11T00:00:00Z",
    )
    return {"brand": brand, "model": model, "variant": variant,
            "city": city, "ad1": ad1, "ad2": ad2}


# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_favorites_require_auth(catalog):
    client = APIClient()
    resp = client.get("/api/favorites/")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_favorite_add_list_remove(catalog):
    client, user, _ = _authed_client("fav@example.com")

    # add
    resp = client.post("/api/favorites/", {"code": "c1"}, format="json")
    assert resp.status_code == 201, resp.content
    assert resp.json()["code"] == "c1"

    # idempotent: re-adding returns 201 (get_or_create returns existing row)
    resp = client.post("/api/favorites/", {"code": "c1"}, format="json")
    assert resp.status_code == 201
    assert Favorite.objects.filter(user=user, ad__code="c1").count() == 1

    # list
    resp = client.get("/api/favorites/")
    assert resp.status_code == 200
    body = resp.json()
    rows = body["results"] if isinstance(body, dict) else body
    assert len(rows) == 1 and rows[0]["code"] == "c1"

    # remove
    resp = client.delete("/api/favorites/c1/")
    assert resp.status_code == 204
    assert not Favorite.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_favorite_add_missing_code_is_400(catalog):
    client, _, _ = _authed_client("fav2@example.com")
    resp = client.post("/api/favorites/", {}, format="json")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_favorites_are_owner_scoped(catalog):
    """User A's favorites never appear in user B's list."""
    client_a, _, _ = _authed_client("a@example.com")
    client_a.post("/api/favorites/", {"code": "c1"}, format="json")

    client_b, user_b, _ = _authed_client("b@example.com")
    resp = client_b.get("/api/favorites/")
    rows = resp.json()["results"] if isinstance(resp.json(), dict) else resp.json()
    assert rows == []
    assert Favorite.objects.filter(user=user_b).count() == 0


# ---------------------------------------------------------------------------
# Alerts (shape validation)
# ---------------------------------------------------------------------------
def test_alert_price_drop_valid(catalog):
    client, user, _ = _authed_client("al@example.com")
    resp = client.post(
        "/api/alerts/",
        {"alert_type": "price_drop", "ad": catalog["ad1"].code, "threshold": 1.0},
        format="json",
    )
    assert resp.status_code == 201, resp.content


@pytest.mark.django_db
def test_alert_price_drop_missing_target_is_400(catalog):
    """price_drop without an ad is rejected at the serializer level."""
    client, _, _ = _authed_client("al2@example.com")
    resp = client.post(
        "/api/alerts/",
        {"alert_type": "price_drop", "threshold": 5.0},
        format="json",
    )
    assert resp.status_code == 400, resp.content


@pytest.mark.django_db
def test_alert_undervalued_missing_model_is_400(catalog):
    client, _, _ = _authed_client("al3@example.com")
    resp = client.post(
        "/api/alerts/", {"alert_type": "undervalued"}, format="json"
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_alert_patch_toggle_enabled(catalog):
    client, user, _ = _authed_client("al5@example.com")
    create = client.post(
        "/api/alerts/",
        {"alert_type": "price_drop", "ad": catalog["ad1"].code},
        format="json",
    )
    aid = create.json()["id"]
    resp = client.patch(
        f"/api/alerts/{aid}/", {"enabled": False}, format="json"
    )
    assert resp.status_code == 200, resp.content
    assert Alert.objects.get(id=aid).enabled is False


@pytest.mark.django_db
def test_alert_channels_normalize_in_app_alias(catalog):
    """Legacy clients sent 'in_app'; the enum value is 'inapp'."""
    client, user, _ = _authed_client("alias@example.com")
    resp = client.post(
        "/api/alerts/",
        {
            "alert_type": "price_drop",
            "ad": catalog["ad1"].code,
            "channels": ["in_app", "email"],
        },
        format="json",
    )
    assert resp.status_code == 201, resp.content
    assert resp.json()["channels"] == ["inapp", "email"]
    assert Alert.objects.get(id=resp.json()["id"]).channels == ["inapp", "email"]


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_evaluate_alerts_normalizes_legacy_in_app_channel(catalog):
    from apps.accounts.alerts import evaluate_alerts

    client, user, _ = _authed_client("legacych@example.com")
    PriceDropEvent.objects.create(
        ad=catalog["ad1"], old_price=1_000_000_000, new_price=900_000_000,
        drop_amount=100_000_000, drop_pct=10.0, observed_at="2026-07-15T00:00:00Z",
    )
    Alert.objects.create(
        user=user, alert_type=Alert.Type.PRICE_DROP, ad=catalog["ad1"],
        threshold=5.0, channels=["in_app"],
    )
    summary = evaluate_alerts()
    assert summary["delivered"] >= 1
    channels = set(
        Notification.objects.filter(user=user).values_list("channel", flat=True)
    )
    assert channels == {"inapp"}
    assert "in_app" not in channels


# ---------------------------------------------------------------------------
# Notifications inbox
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_notification_inbox_owner_scoped(catalog):
    client_a, user_a, _ = _authed_client("n1@example.com")
    Notification.objects.create(user=user_a, channel="inapp", subject="hi")

    client_b, user_b, _ = _authed_client("n2@example.com")
    resp = client_b.get("/api/notifications/")
    rows = resp.json()["results"]
    assert rows == []


# ---------------------------------------------------------------------------
# Alerts evaluator + dedupe
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_evaluate_alerts_creates_notification_and_dedupes(catalog):
    from apps.accounts.alerts import evaluate_alerts

    client, user, _ = _authed_client("ev@example.com")
    # Favorited ad + a price drop on it.
    Favorite.objects.create(user=user, ad=catalog["ad1"])
    PriceDropEvent.objects.create(
        ad=catalog["ad1"], old_price=1_000_000_000, new_price=900_000_000,
        drop_amount=100_000_000, drop_pct=10.0, observed_at="2026-07-15T00:00:00Z",
    )
    Alert.objects.create(
        user=user, alert_type=Alert.Type.PRICE_DROP, ad=catalog["ad1"],
        threshold=5.0, channels=["inapp", "email"],
    )

    s1 = evaluate_alerts()
    assert s1["delivered"] >= 2  # one per channel
    n_after_first = Notification.objects.filter(user=user).count()
    assert n_after_first >= 2

    # Re-run: dedupe_key prevents duplicates.
    s2 = evaluate_alerts()
    assert s2["notifications"] == n_after_first
    assert s2["delivered"] == 0
    assert Notification.objects.filter(user=user).count() == n_after_first


# ---------------------------------------------------------------------------
# Digest command
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_send_digest_emits_one_email(catalog):
    from django.core.management import call_command

    client, user, _ = _authed_client("dig@example.com")
    Favorite.objects.create(user=user, ad=catalog["ad1"])
    PriceDropEvent.objects.create(
        ad=catalog["ad1"], old_price=1_000_000_000, new_price=900_000_000,
        drop_amount=100_000_000, drop_pct=10.0, observed_at="2026-07-18T00:00:00Z",
    )

    mail.outbox.clear()
    call_command("send_digest", "--kind", "daily")
    # One digest email for this user.
    sent = [m for m in mail.outbox if user.email in m.to]
    assert len(sent) == 1


# ---------------------------------------------------------------------------
# Throttle: free user with a tight monthly quota gets 429
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_free_user_monthly_quota_is_429(catalog):
    """A FREE subscription with monthly_api_limit=1 rejects the 2nd write."""
    client, user, _ = _authed_client("qt@example.com")
    sub = user.subscriptions.get()
    sub.monthly_api_limit = 1
    sub.save()

    # First write consumes the single allowed request.
    r1 = client.post("/api/favorites/", {"code": "c1"}, format="json")
    assert r1.status_code == 201

    # Second write exceeds the quota -> 429.
    r2 = client.post("/api/favorites/", {"code": "c2"}, format="json")
    assert r2.status_code == 429
