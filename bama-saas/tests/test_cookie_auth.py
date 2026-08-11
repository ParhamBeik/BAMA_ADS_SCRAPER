"""Cookie auth, verification, entitlements, and admin APIs."""

from __future__ import annotations

import pytest
from django.core import mail
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import ProAccessRequest, Subscription, User
from apps.accounts.services.email_auth import make_verification_token


@pytest.fixture
def client():
    return APIClient()


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
@pytest.mark.django_db
def test_register_sets_cookies_and_sends_verification(client):
    resp = client.post(
        "/api/auth/register/",
        {"email": "buyer@example.com", "password": "Sup3rSecret!", "full_name": "Buyer"},
        format="json",
    )
    assert resp.status_code == 201
    assert "bama_access" in resp.cookies
    assert "bama_refresh" in resp.cookies
    assert len(mail.outbox) == 1
    user = User.objects.get(email="buyer@example.com")
    assert user.email_verified_at is None


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
@pytest.mark.django_db
def test_verify_and_favorite_cap(client, db):
    from apps.core.models import Ad, Brand, Model, Variant, City
    brand = Brand.objects.create(slug="b", name_fa="ب")
    model = Model.objects.create(brand=brand, name_fa="م")
    variant = Variant.objects.create(model=model, name_fa="و")
    city = City.objects.create(name_fa="تهران")
    ads = [
        Ad.objects.create(
            code=f"c{i}", brand=brand, model=model, variant=variant, city=city,
            title=f"a{i}", year_jalali=1400, current_price=1_000_000_000,
            publish_at="2026-08-01T00:00:00Z",
        )
        for i in range(3)
    ]
    reg = client.post(
        "/api/auth/register/",
        {"email": "cap@example.com", "password": "Sup3rSecret!", "full_name": "C"},
        format="json",
    )
    assert reg.status_code == 201
    # Unverified cannot favorite
    denied = client.post("/api/favorites/", {"code": ads[0].code}, format="json")
    assert denied.status_code == 403
    user = User.objects.get(email="cap@example.com")
    token = make_verification_token(user.pk)
    assert client.post("/api/auth/verify/", {"token": token}, format="json").status_code == 200
    user.refresh_from_db()
    assert user.email_verified_at is not None
    ok = client.post("/api/favorites/", {"code": ads[0].code}, format="json")
    assert ok.status_code in (200, 201)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
@pytest.mark.django_db
def test_pro_request_and_staff_approve(client):
    client.post(
        "/api/auth/register/",
        {"email": "free@example.com", "password": "Sup3rSecret!", "full_name": "F"},
        format="json",
    )
    user = User.objects.get(email="free@example.com")
    user.email_verified_at = timezone.now()
    user.save(update_fields=["email_verified_at"])
    req = client.post("/api/auth/pro-request/", {"message": "please"}, format="json")
    assert req.status_code == 201
    staff = User.objects.create_superuser("staff@example.com", "Sup3rSecret!")
    staff_client = APIClient()
    login = staff_client.post(
        "/api/auth/login/", {"email": "staff@example.com", "password": "Sup3rSecret!"}, format="json"
    )
    assert login.status_code == 200
    pro_id = ProAccessRequest.objects.get(user=user).id
    action = staff_client.post(
        f"/api/admin/pro-requests/{pro_id}/",
        {"action": "approve", "days": 30},
        format="json",
    )
    assert action.status_code == 200
    sub = user.subscriptions.order_by("-started_at").first()
    assert sub.plan_type == Subscription.PlanType.PRO


@pytest.mark.django_db
def test_admin_health_requires_staff(client):
    client.post(
        "/api/auth/register/",
        {"email": "user@example.com", "password": "Sup3rSecret!", "full_name": "U"},
        format="json",
    )
    assert client.get("/api/admin/health/").status_code == 403
    staff = User.objects.create_superuser("admin2@example.com", "Sup3rSecret!")
    staff_client = APIClient()
    staff_client.post(
        "/api/auth/login/", {"email": "admin2@example.com", "password": "Sup3rSecret!"}, format="json"
    )
    assert staff_client.get("/api/admin/health/").status_code == 200
