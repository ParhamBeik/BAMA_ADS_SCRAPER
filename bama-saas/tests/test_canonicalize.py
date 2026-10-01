"""canonicalize_catalog: re-file stored ads and their history under the taxonomy."""

import json
from datetime import date
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.common.parsing import extract_ad
from apps.core.models import Ad, Brand, DailyInventorySnapshot, FetchRun, Model, Variant
from apps.jobs.ingest import ingest_ad, reset_cache


def _run(**kwargs):
    out = StringIO()
    call_command("canonicalize_catalog", stdout=out, **kwargs)
    return json.loads(out.getvalue())


@pytest.mark.django_db
def test_family_shaped_rows_are_refiled_with_their_history(make_payload):
    reset_cache()
    now = timezone.now()
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    ad = ingest_ad(extract_ad(make_payload("pride131", 900_000_000, brand="پراید",
                                           model="131", trim="SE"), now),
                   run=run, observed_at=now, publish_at=now).ad
    # The 09-28 shape: maker brand, family model, "label · trim" variant ...
    saipa = Brand.objects.create(slug="saipa-old", name_fa="سایپا", is_confirmed=True)
    family = Model.objects.create(brand=saipa, name_fa="پراید")
    family_variant = Variant.objects.create(model=family, name_fa="131 · SE")
    Ad.objects.filter(pk=ad.pk).update(brand=saipa, model=family, variant=family_variant)
    # ... and pre-09-28 history on a row no ad uses any more.
    old_model = Model.objects.create(brand=saipa, name_fa="131", is_confirmed=True)
    old_variant = Variant.objects.create(model=old_model, name_fa="SE")
    DailyInventorySnapshot.objects.create(model=old_model, variant=old_variant,
                                          year_jalali=1399, date=date(2026, 8, 1),
                                          ad_count=7, median_price=800_000_000)

    dry = _run()
    assert dry["ads_changed"] == 1
    assert Ad.objects.get(pk=ad.pk).model_id == family.pk, "dry run must roll back"

    applied = _run(apply=True)
    ad.refresh_from_db()
    assert (ad.brand.name_fa, ad.model.name_fa, ad.variant.name_fa) == ("پراید", "131 دنده ای", "SE")
    snap = DailyInventorySnapshot.objects.get(date=date(2026, 8, 1))
    assert snap.variant_id == ad.variant_id and snap.ad_count == 7
    assert not Model.objects.filter(pk__in=[family.pk, old_model.pk]).exists()
    assert not Brand.objects.filter(pk=saipa.pk).exists()
    assert applied["unconfirmed_models_with_ads"] == 0

    assert _run(apply=True)["ads_changed"] == 0


@pytest.mark.django_db
def test_image_sweep_hides_a_dead_cover_and_restores_a_revived_one(make_payload, monkeypatch):
    from apps.core import images
    from apps.core.pricing import scorable_rows
    from apps.jobs.jobs import image_sweep

    reset_cache()
    now = timezone.now()
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    for code in ("alive001", "dead0001", "flaky001"):
        ingest_ad(extract_ad(make_payload(code, 900_000_000, brand="پراید", model="131"), now),
                  run=run, observed_at=now, publish_at=now)
    answers = {"alive001": "alive", "dead0001": "dead", "flaky001": "unknown"}
    monkeypatch.setattr(images, "cover_status",
                        lambda url: next(v for k, v in answers.items() if k in url))

    assert image_sweep() == {"alive": 1, "dead": 1, "unknown": 1}
    visible = set(scorable_rows().values_list("code", flat=True))
    assert "dead0001" not in visible
    assert {"alive001", "flaky001"} <= visible, "an unknown answer must hide nothing"
    assert Ad.objects.get(code="flaky001").image_checked_at is None

    answers["dead0001"] = "alive"
    Ad.objects.update(image_checked_at=None)
    image_sweep()
    assert "dead0001" in set(scorable_rows().values_list("code", flat=True))


@pytest.mark.django_db
def test_purge_removes_only_non_cash_and_below_floor_ads(make_payload):
    reset_cache()
    now = timezone.now()
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    codes = [f"cash{i:04d}" for i in range(9)] + ["deposit1", "voucher1"]
    for code in codes:
        price = 100_000_000 if code == "deposit1" else 1_000_000_000
        ingest_ad(extract_ad(make_payload(code, price, brand="پراید", model="131"), now),
                  run=run, observed_at=now, publish_at=now)
    Ad.objects.filter(code="voucher1").update(price_basis_unclear=True)

    dry = json.loads(_purge())
    assert dry["ads"] == 2 and Ad.objects.count() == len(codes)
    assert dry["by_reason"] == {"not_cash": 1, "under_cohort_floor": 1}

    _purge(apply=True)
    assert set(Ad.objects.values_list("code", flat=True)) == set(codes) - {"deposit1",
                                                                           "voucher1"}


def _purge(**kwargs):
    out = StringIO()
    call_command("purge_ineligible", stdout=out, **kwargs)
    return out.getvalue()
