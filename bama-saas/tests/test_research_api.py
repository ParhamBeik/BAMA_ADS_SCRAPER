"""Research endpoints: access tiers and the provenance envelope.

Test type: API/integration.

The envelope tests matter as much as the access ones. These numbers come from a
crawl that can be incomplete, and a survival curve computed across a coverage
hole reads crawler downtime as cars leaving the market. A number served without
its provenance cannot be checked by the person acting on it.
"""

from datetime import datetime, timedelta, timezone

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Subscription, User
from apps.core.models import Ad, Brand, City, FetchRun, Model, Variant

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def catalog(db):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    model = Model.objects.create(brand=brand, name_fa="207", is_confirmed=True)
    return {
        "brand": brand, "model": model,
        "variant": Variant.objects.create(model=model, name_fa="پانوراما"),
        "city": City.objects.create(name_fa="تهران"),
    }


@pytest.fixture
def anon():
    return APIClient()


@pytest.fixture
def member(db):
    user = User.objects.create_user(email="member@example.com", password="pw")
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def subscriber(db):
    user = User.objects.create_user(email="pro@example.com", password="pw")
    Subscription.objects.create(user=user, status=Subscription.Status.ACTIVE)
    client = APIClient()
    client.force_authenticate(user)
    return client


# --- access tiers -----------------------------------------------------------

@pytest.mark.django_db
def test_the_market_overview_is_public(anon, catalog):
    assert anon.get("/api/analytics/overview/").status_code == 200


@pytest.mark.django_db
def test_research_requires_an_account(anon, catalog):
    resp = anon.get(f"/api/research/liquidity/{catalog['model'].pk}/")
    assert resp.status_code in (401, 403)


@pytest.mark.django_db
def test_research_requires_a_subscription(member, catalog):
    """IsActiveSubscription existed and was referenced nowhere; premium gating was
    throttle-only, so a free account could pull every cohort computation."""
    resp = member.get(f"/api/research/liquidity/{catalog['model'].pk}/")
    assert resp.status_code == 403


@pytest.mark.django_db
def test_a_subscriber_gets_through(subscriber, catalog):
    assert subscriber.get(f"/api/research/liquidity/{catalog['model'].pk}/").status_code == 200


@pytest.mark.django_db
def test_per_listing_tools_need_only_an_account(member, catalog):
    """A buyer checking one car should not need a research subscription."""
    Ad.objects.create(
        code="buyer001", brand=catalog["brand"], model=catalog["model"],
        variant=catalog["variant"], city=catalog["city"], year_jalali=1400,
        current_price=1_000_000_000, first_seen_at=NOW, last_seen_at=NOW,
        publish_at=NOW,
    )
    assert member.get("/api/ads/buyer001/fair-price/").status_code == 200
    assert member.get("/api/ads/buyer001/identity/").status_code == 200


# --- the provenance envelope ------------------------------------------------

@pytest.mark.django_db
def test_every_answer_carries_its_provenance(subscriber, catalog):
    body = subscriber.get(f"/api/research/liquidity/{catalog['model'].pk}/").json()

    assert "as_of" in body
    assert "coverage" in body
    assert "methodology_version" in body


@pytest.mark.django_db
def test_coverage_says_so_when_no_sweep_has_completed(subscriber, catalog):
    """The honest answer when the crawl has never finished: not a number with an
    invisible asterisk."""
    body = subscriber.get(f"/api/research/liquidity/{catalog['model'].pk}/").json()

    assert body["coverage"]["complete_sweep"] is False


@pytest.mark.django_db
def test_coverage_reports_staleness(subscriber, catalog):
    FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH, mode=FetchRun.Mode.FULL,
        status=FetchRun.Status.SUCCEEDED, reached_end=True,
        pages_fetched=1000, fetched_count=50_000, deepest_rank=30_000,
        started_at=NOW - timedelta(days=4),
    )
    body = subscriber.get(f"/api/research/liquidity/{catalog['model'].pk}/").json()

    assert body["coverage"]["complete_sweep"] is True
    assert body["coverage"]["stale"] is True


@pytest.mark.django_db
def test_a_thin_cohort_refuses_rather_than_inventing_a_number(subscriber, catalog):
    body = subscriber.get(f"/api/research/liquidity/{catalog['model'].pk}/").json()

    assert body["available"] is False
    assert body["reason"] == "insufficient_episodes"


@pytest.mark.django_db
def test_identity_says_plainly_when_there_is_no_evidence(member, catalog):
    Ad.objects.create(
        code="nopics01", brand=catalog["brand"], model=catalog["model"],
        variant=catalog["variant"], city=catalog["city"], year_jalali=1400,
        current_price=1_000_000_000, first_seen_at=NOW, last_seen_at=NOW,
        publish_at=NOW,
    )
    body = member.get("/api/ads/nopics01/identity/").json()

    assert body["identified"] is False
    assert "evidence" in body["reason"]
