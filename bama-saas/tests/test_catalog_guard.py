"""The catalog-pollution guard: ingestion may not grow the catalog in silence.

Test type: integration. The guard spans the dimension resolver and the ingest
flow and its whole point is what lands in the database, so a pure unit test of
either half would prove nothing about the behaviour that matters.

Why this is guarded at all: brand and model are parsed out of a free-text ad
title (``apps/parsing/extract.py`` splits on "،"), so any change to Bama's title
format mints Brand/Model rows that look exactly like real ones. Every cohort key,
every market index scope and every deal-score peer group is built on those rows.
A silent invention is therefore not a cosmetic problem, it is a wrong number
nobody can trace.
"""

from datetime import datetime, timezone

import pytest

from apps.core.models import Ad, Brand, FetchRun, Model
from apps.jobs.services.dimensions import reset_cache, resolve_dimensions
from apps.jobs.services.ingest import ingest_ad, reset_price_cache
from apps.parsing import extract_ad

from tests.test_importer import _ing, _ing3, make_payload

OBSERVED = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_caches():
    reset_cache()
    reset_price_cache()
    yield
    reset_cache()
    reset_price_cache()


@pytest.mark.django_db
def test_new_brand_is_reported_as_minted_and_lands_unconfirmed():
    dims = resolve_dimensions(
        brand_name="برند تازه",
        model_name="مدل تازه",
        trim_name="پایه",
        city_location="تهران",
    )
    assert dims["minted"] == ["brand", "model"]
    assert dims["brand"].is_confirmed is False
    assert dims["model"].is_confirmed is False


@pytest.mark.django_db
def test_known_brand_is_not_reported_as_minted():
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    Model.objects.create(brand=brand, name_fa="405", is_confirmed=True)
    reset_cache()

    dims = resolve_dimensions(
        brand_name="پژو", model_name="405", trim_name="دنده‌ای", city_location="تهران"
    )
    assert dims["minted"] == []


@pytest.mark.django_db
def test_a_new_model_under_a_known_brand_is_still_reported():
    """The failure mode is per-level: a title change can keep the brand readable
    while turning the model half into garbage."""
    Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    reset_cache()

    dims = resolve_dimensions(
        brand_name="پژو", model_name="۴۰۵ جی ال ایکس آی", trim_name="", city_location="تهران"
    )
    assert dims["minted"] == ["model"]


@pytest.mark.django_db
def test_ad_that_mints_a_dimension_is_flagged():
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    payload = make_payload("newbrand1", 15_000_000_000, brand="برند ناشناخته")
    ad = _ing(
        extract_ad(payload, OBSERVED), run=run, observed_at=OBSERVED, publish_at=OBSERVED
    )

    assert ad is not None, "the ad itself is fine — it is the catalog that is unproven"
    assert "unknown_dimension" in ad.quality_flags


@pytest.mark.django_db
def test_ad_using_an_existing_dimension_is_not_flagged():
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    Model.objects.create(brand=brand, name_fa="405", is_confirmed=True)
    reset_cache()

    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    payload = make_payload("known001", 15_000_000_000)
    ad = _ing(
        extract_ad(payload, OBSERVED), run=run, observed_at=OBSERVED, publish_at=OBSERVED
    )

    assert "unknown_dimension" not in ad.quality_flags


@pytest.mark.django_db
def test_flag_clears_once_the_dimension_exists():
    """quality_flags is recomputed every observation, so the second sighting of an
    ad whose brand is now on record must come back clean — otherwise the flag
    would be a permanent scar rather than a statement about the present."""
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    payload = make_payload("newbrand2", 15_000_000_000, brand="برند ناشناخته")
    ad = _ing(
        extract_ad(payload, OBSERVED), run=run, observed_at=OBSERVED, publish_at=OBSERVED
    )
    assert "unknown_dimension" in ad.quality_flags

    reset_cache()  # a later run does not share the first run's memoisation
    later = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    ingest_ad(
        extract_ad(payload, OBSERVED), run=later, observed_at=OBSERVED, publish_at=OBSERVED
    )

    assert "unknown_dimension" not in Ad.objects.get(code="newbrand2").quality_flags


@pytest.mark.django_db
def test_unknown_dimension_is_soft():
    """Soft by construction: analytics excludes exactly HARD_RULE_IDS, and an ad
    with a genuinely new model is still a real, usable listing."""
    from apps.jobs.services.verify import EXTERNAL_FLAG_IDS, HARD_RULE_IDS

    assert "unknown_dimension" in EXTERNAL_FLAG_IDS
    assert "unknown_dimension" not in HARD_RULE_IDS
