"""The deal notifier: who gets announced, once, and who never does.

Integration over stored rows, because every rule it applies is a query against
the deal board rather than arithmetic. The property that matters most is
once-per-listing: a notifier that repeats gets muted, and a muted notifier is
worth exactly nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from apps.core.models import (
    Ad,
    Brand,
    City,
    DealScoreCache,
    Model,
    NotifiedAd,
    NotifierSettings,
    Variant,
)
from apps.core import notify as N

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def catalog(db):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    model = Model.objects.create(brand=brand, name_fa="207", is_confirmed=True)
    return {
        "brand": brand,
        "model": model,
        "variant": Variant.objects.create(model=model, name_fa="پانوراما"),
        "city": City.objects.create(name_fa="تهران"),
    }


@pytest.fixture
def cfg(db):
    settings = NotifierSettings.load()
    settings.enabled = True
    settings.telegram_chat_id = "12345"
    settings.save()
    return settings


def make_scored(catalog, code, *, discount=30.0, peers=20, price=1_000_000_000,
                model=None):
    ad = Ad.objects.create(
        code=code, brand=catalog["brand"], model=model or catalog["model"],
        variant=catalog["variant"], city=catalog["city"],
        year_jalali=1400, mileage=50_000, current_price=price,
        status=Ad.Status.ACTIVE, title="پژو، 207",
        first_seen_at=NOW - timedelta(days=1), last_seen_at=NOW, publish_at=NOW,
    )
    return DealScoreCache.objects.create(
        ad=ad, score=discount, discount_pct=discount, peer_median=price * 2,
        components={
            "peer_count": peers, "confidence": "high",
            "fair_value": price * 2, "price": price,
        },
    )


@pytest.fixture(autouse=True)
def _no_real_telegram(monkeypatch):
    """Never touch the network; record what would have been sent."""
    sent = []
    monkeypatch.setattr(N, "send_telegram", lambda text, chat_id: sent.append(text) or True)
    return sent


@pytest.mark.django_db
def test_disabled_notifier_sends_nothing(catalog, cfg):
    cfg.enabled = False
    cfg.save()
    make_scored(catalog, "deal0001")

    assert N.notify_deals() == {"enabled": False, "sent": 0, "candidates": 0}
    assert NotifiedAd.objects.count() == 0


@pytest.mark.django_db
def test_a_qualifying_deal_is_announced_once_ever(catalog, cfg):
    """The anti-noise property. A car that keeps qualifying is one piece of news."""
    make_scored(catalog, "deal0001", discount=30.0, peers=20)

    first = N.notify_deals()
    second = N.notify_deals()

    assert first["sent"] == 1
    assert second["sent"] == 0, "already announced"
    assert NotifiedAd.objects.count() == 1


@pytest.mark.django_db
def test_a_thin_cohort_is_never_announced(catalog, cfg):
    """A median over a handful of cars is not evidence of a bargain."""
    make_scored(catalog, "thin0001", discount=60.0, peers=cfg.min_peers - 1)

    assert N.notify_deals()["sent"] == 0


@pytest.mark.django_db
def test_an_installment_ad_is_never_announced(catalog, cfg):
    row = make_scored(catalog, "deposit01", discount=60.0, peers=30)
    row.ad.description = "مبلغ فوق پیش پرداخت است"
    row.ad.save(update_fields=["description"])

    assert N.notify_deals()["sent"] == 0


def test_toman_never_uses_the_old_tenfold_divisor():
    assert N.toman(2_200_000_000) == "2.20B"
    assert N.toman(220_000_000) == "220M"


@pytest.mark.django_db
def test_a_shallow_discount_is_never_announced(catalog, cfg):
    make_scored(catalog, "weak0001", discount=cfg.min_discount_pct - 1, peers=30)

    assert N.notify_deals()["sent"] == 0


@pytest.mark.django_db
def test_price_bounds_scope_the_notifier(catalog, cfg):
    cfg.price_min = 2_000_000_000
    cfg.price_max = 5_000_000_000
    cfg.save()
    make_scored(catalog, "cheap001", price=1_000_000_000)
    make_scored(catalog, "inband01", price=3_000_000_000)
    make_scored(catalog, "dear0001", price=9_000_000_000)

    N.notify_deals()

    assert set(NotifiedAd.objects.values_list("ad_id", flat=True)) == {"inband01"}


@pytest.mark.django_db
def test_model_scope_restricts_to_chosen_models(catalog, cfg):
    other = Model.objects.create(
        brand=catalog["brand"], name_fa="206", is_confirmed=True
    )
    cfg.model_ids = [other.pk]
    cfg.save()
    make_scored(catalog, "wanted01", model=other)
    make_scored(catalog, "ignored1")

    N.notify_deals()

    assert set(NotifiedAd.objects.values_list("ad_id", flat=True)) == {"wanted01"}


@pytest.mark.django_db
def test_a_failed_send_is_retried_next_tick(catalog, cfg, monkeypatch):
    """Recording on a failed send would swallow the listing forever."""
    monkeypatch.setattr(N, "send_telegram", lambda text, chat_id: False)
    make_scored(catalog, "deal0001")

    result = N.notify_deals()

    assert result["candidates"] == 1
    assert result["sent"] == 0
    assert NotifiedAd.objects.count() == 0, "not marked as sent"


@pytest.mark.django_db
def test_dry_run_reports_without_sending(catalog, cfg, _no_real_telegram):
    make_scored(catalog, "deal0001")

    result = N.notify_deals(dry_run=True)

    assert result["candidates"] == 1
    assert result["sent"] == 0
    assert _no_real_telegram == []
    assert NotifiedAd.objects.count() == 0


@pytest.mark.django_db
def test_one_run_cannot_flood_the_chat(catalog, cfg):
    """A lowered threshold or a fresh install must not dump the board at once."""
    for i in range(N.MAX_PER_RUN + 5):
        make_scored(catalog, f"deal{i:04d}")

    assert N.notify_deals()["sent"] == N.MAX_PER_RUN


@pytest.mark.django_db
def test_message_names_the_evidence(catalog, cfg, _no_real_telegram):
    """A ping the reader cannot judge is a ping they learn to ignore."""
    make_scored(catalog, "deal0001", discount=30.0, peers=20)

    N.notify_deals()

    text = _no_real_telegram[0]
    assert "30% below fair value" in text
    assert "20 peers" in text
    assert "high confidence" in text


@pytest.mark.django_db
def test_settings_endpoint_round_trips(client, db):
    resp = client.patch(
        "/api/notifier-settings/",
        data={"enabled": True, "min_discount_pct": 25, "min_peers": 12},
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.json()["min_discount_pct"] == 25
    assert NotifierSettings.load().enabled is True


@pytest.mark.django_db
def test_settings_endpoint_rejects_a_peer_floor_below_the_engines(client, db):
    resp = client.patch(
        "/api/notifier-settings/",
        data={"min_peers": 3},
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert "min_peers" in resp.json()


@pytest.mark.django_db
def test_settings_stay_a_singleton(db):
    NotifierSettings.load().save()
    NotifierSettings(min_peers=30).save()

    assert NotifierSettings.objects.count() == 1


@pytest.mark.django_db
def test_an_installment_ad_is_never_announced(catalog, cfg):
    """The audit's headline failure, as a regression test.

    74% of the top 200 board rows were installment listings advertising a down
    payment, and the notifier orders by discount — so these were the first
    messages a user would ever have received. Gated on the read side too because
    the cache is rebuilt on a schedule and can serve a stale row.
    """
    row = make_scored(catalog, "instal01", discount=48.0, peers=30)
    row.ad.description = "فروش خودرو به صورت نقد و اقساط، پیش پرداخت ۵۰٪"
    row.ad.save(update_fields=["description"])

    assert N.notify_deals()["sent"] == 0
    assert NotifiedAd.objects.count() == 0


@pytest.mark.django_db
def test_message_states_the_price_in_the_right_magnitude(catalog, cfg, _no_real_telegram):
    """This divided by 10_000_000 and labelled it "M", understating 10x.

    A 2.2B toman car was announced as "220M toman" while the same car read
    "2.20B" on the board it came from.
    """
    make_scored(catalog, "deal0042", discount=25.0, peers=30, price=2_200_000_000)

    N.notify_deals()

    (text,) = _no_real_telegram
    assert "2.20B toman" in text
    assert "220M" not in text
