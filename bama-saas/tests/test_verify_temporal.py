"""Temporal verification: rules that compare an ad to its own previous state.

Test type: integration for the rules themselves (they read a stored Ad row, so
there is nothing meaningful to test without one) and integration for the ingest
wiring, because the behaviour that matters — a unit switch not being published as
a price drop — only exists once the whole flow runs.

The per-field rules in test_verify.py cannot catch any of this: every value in
these fixtures is individually inside its plausible band. It is the transition
between two sightings that is impossible.
"""

from datetime import datetime, timedelta, timezone

import pytest

from apps.core.models import Ad, Brand, FetchRun, Model, PriceDropEvent, PriceObservation
from apps.jobs.services import verify_temporal as VT
from apps.jobs.services.dimensions import reset_cache
from apps.jobs.services.ingest import ingest_ad, reset_price_cache
from apps.parsing import extract_ad

from tests.test_importer import _ing, _ing3, make_payload

FIRST = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
LATER = FIRST + timedelta(days=1)


@pytest.fixture(autouse=True)
def _clear_caches():
    reset_cache()
    reset_price_cache()
    yield
    reset_cache()
    reset_price_cache()


@pytest.fixture
def catalog(db):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    Model.objects.create(brand=brand, name_fa="405", is_confirmed=True)
    reset_cache()
    return brand


def ingest(payload, observed, run=None):
    """Ingest one payload and return the ad as actually stored.

    ``ingest_ad`` updates existing rows with a bare SQL UPDATE and returns the
    pre-update instance, so reading flags off its return value without a reload
    reports the previous observation's verdict.
    """
    run = run or FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    ad, created, price_changed = _ing3(
        extract_ad(payload, observed), run=run, observed_at=observed, publish_at=observed
    )
    if ad is not None:
        ad.refresh_from_db()
    return ad, created, price_changed


# --- rules in isolation -----------------------------------------------------

@pytest.mark.django_db
def test_no_rules_fire_on_a_first_sighting():
    assert VT.verify_against_previous({"current_price": 1}, {}, None, {}) == []


@pytest.mark.django_db
def test_price_jump_detects_a_unit_switch(catalog):
    """Rials are 10x tomans. Both numbers are legal prices; the move is not."""
    ingest(make_payload("jump0001", 1_500_000_000), FIRST)
    ad, _, _ = ingest(make_payload("jump0001", 15_000_000_000), LATER)

    assert "price_jump" in ad.quality_flags


@pytest.mark.django_db
def test_an_ordinary_price_cut_is_not_a_jump(catalog):
    """The band has to be wide enough that real, even aggressive, pricing passes —
    otherwise the rule quietly deletes the price-cut signal it sits next to."""
    ingest(make_payload("cut00001", 15_000_000_000), FIRST)
    ad, _, _ = ingest(make_payload("cut00001", 12_000_000_000), LATER)

    assert "price_jump" not in ad.quality_flags


@pytest.mark.django_db
def test_mileage_regression(catalog):
    payload = make_payload("miles001", 15_000_000_000)
    payload["detail"]["mileage"] = "120,000"
    ingest(payload, FIRST)

    rolled_back = make_payload("miles001", 15_000_000_000)
    rolled_back["detail"]["mileage"] = "80,000"
    ad, _, _ = ingest(rolled_back, LATER)

    assert "mileage_regression" in ad.quality_flags


@pytest.mark.django_db
def test_mileage_rollback_to_zero_is_caught(catalog):
    """The loudest case, and the one a naive implementation misses: extract_ad
    parses mileage with positive=True, so a genuine zero arrives as None."""
    payload = make_payload("miles002", 15_000_000_000)
    payload["detail"]["mileage"] = "120,000"
    ingest(payload, FIRST)

    zeroed = make_payload("miles002", 15_000_000_000)
    zeroed["detail"]["mileage"] = "صفر کیلومتر"
    ad, _, _ = ingest(zeroed, LATER)

    assert ad.mileage == 0
    assert "mileage_regression" in ad.quality_flags


@pytest.mark.django_db
def test_a_typo_correction_is_within_tolerance(catalog):
    payload = make_payload("miles003", 15_000_000_000)
    payload["detail"]["mileage"] = "120,000"
    ingest(payload, FIRST)

    corrected = make_payload("miles003", 15_000_000_000)
    corrected["detail"]["mileage"] = "119,500"
    ad, _, _ = ingest(corrected, LATER)

    assert "mileage_regression" not in ad.quality_flags


@pytest.mark.django_db
def test_normal_odometer_growth_is_clean(catalog):
    payload = make_payload("miles004", 15_000_000_000)
    payload["detail"]["mileage"] = "120,000"
    ingest(payload, FIRST)

    driven = make_payload("miles004", 15_000_000_000)
    driven["detail"]["mileage"] = "125,000"
    ad, _, _ = ingest(driven, LATER)

    assert "mileage_regression" not in ad.quality_flags


@pytest.mark.django_db
def test_identity_mutation_when_a_code_is_recycled(catalog):
    """Without this, one ad's price series silently continues into a different
    car and its apparent time-on-market spans both."""
    ingest(make_payload("recyc001", 15_000_000_000), FIRST)
    recycled = make_payload("recyc001", 15_000_000_000)
    recycled["detail"]["year"] = "1395"  # was 1399
    ad, _, _ = ingest(recycled, LATER)

    assert "identity_mutation" in ad.quality_flags


@pytest.mark.django_db
def test_a_brand_change_is_an_identity_mutation(catalog):
    ingest(make_payload("recyc002", 15_000_000_000), FIRST)
    ad, _, _ = ingest(
        make_payload("recyc002", 15_000_000_000, brand="تویوتا"), LATER
    )

    assert "identity_mutation" in ad.quality_flags


@pytest.mark.django_db
def test_the_source_renaming_a_model_is_not_an_identity_mutation(catalog):
    """Found against the live database. The first version of this rule compared
    model_id and fired on 137 ads, none of which were recycled codes — every one
    was Bama editing the model name in the title, e.g.
    "تیگو 8 پرو مکس (F8 PRO MAX)" becoming "تیگو 8 پرو مکس (F8)".

    Model names are free text the source controls and changes. Treating that as a
    different car would have marked a large, arbitrary slice of the market as
    corrupt.
    """
    ingest(make_payload("rename01", 15_000_000_000, model="تیگو 8 پرو مکس (F8 PRO MAX)"), FIRST)
    ad, _, _ = ingest(
        make_payload("rename01", 15_000_000_000, model="تیگو 8 پرو مکس (F8)"), LATER
    )

    assert "identity_mutation" not in ad.quality_flags


@pytest.mark.django_db
def test_same_car_reobserved_has_no_identity_flag(catalog):
    ingest(make_payload("stable01", 15_000_000_000), FIRST)
    ad, _, _ = ingest(make_payload("stable01", 15_000_000_000), LATER)

    assert "identity_mutation" not in ad.quality_flags


# --- ingest wiring ----------------------------------------------------------

@pytest.mark.django_db
def test_a_flagged_jump_does_not_become_a_price_drop_event(catalog):
    """The behaviour this whole layer exists for.

    A rial->toman switch reads as a 90% cut. PriceDropEvent is user-facing, so
    without this the site advertises a data error as the best deal available.
    """
    ingest(make_payload("fake0001", 15_000_000_000), FIRST)
    ingest(make_payload("fake0001", 1_500_000_000), LATER)

    assert PriceDropEvent.objects.filter(ad_id="fake0001").count() == 0


@pytest.mark.django_db
def test_a_real_price_cut_still_becomes_a_drop_event(catalog):
    """The other half: the guard must not swallow the genuine signal."""
    ingest(make_payload("real0001", 15_000_000_000), FIRST)
    ingest(make_payload("real0001", 13_000_000_000), LATER)

    assert PriceDropEvent.objects.filter(ad_id="real0001").count() == 1


@pytest.mark.django_db
def test_the_flagged_price_observation_is_kept_and_marked(catalog):
    """We know one of the two prices is wrong, not which, so the row stays —
    losing it would put a hole in the ad's price history."""
    ingest(make_payload("keep0001", 15_000_000_000), FIRST)
    ingest(make_payload("keep0001", 1_500_000_000), LATER)

    observations = PriceObservation.objects.filter(ad_id="keep0001").order_by("observed_at")
    assert observations.count() == 2
    assert observations.first().quality_flags == []
    assert observations.last().quality_flags == ["price_jump"]


@pytest.mark.django_db
def test_flags_clear_once_the_transition_is_normal_again(catalog):
    """quality_flags describes the present, not a permanent record of past sin."""
    ingest(make_payload("clear001", 1_500_000_000), FIRST)
    ingest(make_payload("clear001", 15_000_000_000), LATER)
    ad, _, _ = ingest(make_payload("clear001", 15_500_000_000), LATER + timedelta(days=1))

    assert "price_jump" not in Ad.objects.get(code="clear001").quality_flags


@pytest.mark.django_db
def test_temporal_flags_are_all_soft():
    """Soft by construction: an impossible transition proves one of the two
    sightings is wrong without saying which, so quarantining the ad would discard
    a good row half the time."""
    from apps.jobs.services.verify import HARD_RULE_IDS

    assert not {"price_jump", "mileage_regression", "identity_mutation"} & HARD_RULE_IDS


@pytest.mark.django_db
def test_a_broken_rule_never_blocks_ingestion(catalog, monkeypatch):
    def exploding(extracted, payload, previous, dims):
        raise RuntimeError("boom")

    monkeypatch.setattr(VT, "RULES", (exploding,))
    ingest(make_payload("safe0001", 15_000_000_000), FIRST)
    ad, _, _ = ingest(make_payload("safe0001", 15_000_000_000), LATER)

    assert ad is not None
    assert any(flag.endswith("_errored") for flag in ad.quality_flags)
