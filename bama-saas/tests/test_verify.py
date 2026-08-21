"""Verification rules, and the catalog-pollution guard.

The rules are pure ``(extracted, payload) -> Rejection | None`` functions and are
unit-tested as such. The catalog guard spans the dimension resolver and the
ingest flow and its whole point is what lands in the database, so it is tested
against a real one.
"""

from __future__ import annotations

import ast
import copy
import inspect
from datetime import datetime, timezone

import pytest

from apps.core.models import Ad, Brand, FetchRun, Model
from apps.jobs import verify as V
from apps.jobs.ingest import ingest_ad, reset_cache, resolve_dimensions
from apps.jobs.parsing import extract_ad


OBSERVED_AT = datetime(2025, 7, 1, tzinfo=timezone.utc)


def clean_payload() -> dict:
    """A real (abridged) Bama ad that passes every rule."""
    return copy.deepcopy(
        {
            "detail": {
                "code": "6mnwbfv5", "title": "آئودی، A3L", "brand_fa": "آئودی",
                "year": "2025", "mileage": "صفر کیلومتر", "type": "car",
                "time": "4 روز پیش", "location": "تهران / بهشتی",
                "transmission": "اتوماتیک", "trim": "1.5 لیتر توربو",
                "url": "/car/detail-6mnwbfv5-audi-a3",
            },
            "price": {
                "price": "15,000,000,000", "type": "lumpsum", "payment": "0",
                "prepayment": "0", "installments": "0",
            },
        }
    )


def run(payload: dict) -> list[str]:
    """Extract then verify; returns the fired rule ids."""
    extracted = extract_ad(payload, OBSERVED_AT) or {"code": payload["detail"].get("code")}
    return [r.rule for r in V.verify_extracted(extracted, payload)]


def test_clean_payload_has_no_rejections():
    assert run(clean_payload()) == []


def test_every_rule_is_registered():
    assert len(V.RULES) == 13


def test_hard_flag_agrees_with_hard_rule_ids():
    """"Hard" is declared twice; this asserts the two never drift apart.

    Each ``Rejection(...)`` passes a literal ``hard`` bool, while
    ``HARD_RULE_IDS`` lists the same fact separately. Ingest quarantines using
    the bool (``r.hard``) but analytics excludes using the frozenset
    (``quality.verified``), so a divergence is silent and asymmetric: a rule
    that is hard-but-unlisted deletes rows analytics would have happily used,
    and listed-but-soft keeps rows every statistic then drops.

    Walks the AST instead of firing the rules, so a new rule is covered the
    moment it is written rather than when someone remembers to test it.
    """
    tree = ast.parse(inspect.getsource(V))
    declared = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Rejection"):
            continue
        # Only literal (rule_id, ..., hard) constructions are checkable; the
        # synthetic "<rule>_errored" fallback interpolates its id at runtime.
        if len(node.args) != 3 or not isinstance(node.args[0], ast.Constant):
            continue
        rule_id, hard = node.args[0].value, node.args[2].value
        assert isinstance(hard, bool), f"{rule_id}: hard must be a literal bool"
        declared[rule_id] = hard

    assert declared, "found no Rejection constructions to check"
    for rule_id, hard in sorted(declared.items()):
        assert hard == (rule_id in V.HARD_RULE_IDS), (
            f"{rule_id!r} is constructed with hard={hard} but "
            f"{'is' if rule_id in V.HARD_RULE_IDS else 'is not'} in HARD_RULE_IDS"
        )

    # Nothing may be listed as hard that no rule actually emits.
    assert V.HARD_RULE_IDS <= set(declared), (
        f"HARD_RULE_IDS names rules that no rule emits: "
        f"{sorted(V.HARD_RULE_IDS - set(declared))}"
    )


# --- one test per rule ------------------------------------------------------

@pytest.mark.parametrize("code", ["", "AB12345", "short", "way-too-long-code-x"])
def test_code_missing(code):
    payload = clean_payload()
    payload["detail"]["code"] = code
    assert "code_missing" in run(payload)


def test_price_type_unknown():
    payload = clean_payload()
    payload["price"]["type"] = "auction"
    assert "price_type_unknown" in run(payload)


def test_price_missing_for_lumpsum():
    payload = clean_payload()
    payload["price"]["price"] = "0"
    assert "price_missing_for_lumpsum" in run(payload)


def test_price_sentinel_is_soft():
    """Bama writes "-1" as well as "0" for "no price" on negotiable ads, and the
    negative never reaches current_price — so this is a schema-watch signal, not
    grounds for discarding an otherwise complete ad."""
    payload = clean_payload()
    payload["price"]["type"] = "negotiable"
    payload["price"]["price"] = "-1"
    extracted = extract_ad(payload, OBSERVED_AT)
    rejections = V.verify_extracted(extracted, payload)
    assert "price_sentinel" in [r.rule for r in rejections]
    assert next(r for r in rejections if r.rule == "price_sentinel").hard is False
    assert "price_sentinel" not in V.HARD_RULE_IDS


def test_price_too_low_is_hard():
    """A sub-band price is a unit switch or parser bug, and it drags every mean
    down, so it must be HARD — i.e. excluded from analytics, not just flagged."""
    payload = clean_payload()
    payload["price"]["price"] = "9,000,000"
    extracted = extract_ad(payload, OBSERVED_AT)
    rejections = V.verify_extracted(extracted, payload)
    assert "price_too_low" in [r.rule for r in rejections]
    assert next(r for r in rejections if r.rule == "price_too_low").hard is True
    assert "price_too_low" in V.HARD_RULE_IDS


def test_high_price_is_not_quarantined_globally():
    """Only model-relative outlier detection may reject a high asking price."""
    payload = clean_payload()
    payload["price"]["price"] = "5,800,000,000,000"
    extracted = extract_ad(payload, OBSERVED_AT)
    rejections = V.verify_extracted(extracted, payload)
    assert "price_too_high" not in [r.rule for r in rejections]
    assert "price_too_high" not in V.HARD_RULE_IDS


def test_year_unknown():
    payload = clean_payload()
    payload["detail"]["year"] = "1500"
    assert "year_unknown" in run(payload)


@pytest.mark.parametrize("year", ["1403", "2025"])
def test_year_known_bands_do_not_fire(year):
    payload = clean_payload()
    payload["detail"]["year"] = year
    assert "year_unknown" not in run(payload)


def test_mileage_implausible():
    payload = clean_payload()
    payload["detail"]["mileage"] = "3,000,000 کیلومتر"
    assert "mileage_implausible" in run(payload)


def test_publish_unparseable():
    payload = clean_payload()
    payload["detail"]["time"] = "به زودی"
    assert "publish_unparseable" in run(payload)


@pytest.mark.parametrize("title,brand", [("آئودی", "آئودی"), ("", "")])
def test_brand_missing(title, brand):
    payload = clean_payload()
    payload["detail"]["title"] = title
    payload["detail"]["brand_fa"] = brand
    assert "brand_missing" in run(payload)


# --- cross-field rules ------------------------------------------------------
#
# These exist because every rule above judges one field in isolation, so a value
# that is legal on its own passes even when the row it sits in is impossible.

def test_year_implausible_future():
    """year_unknown only checks membership of a calendar band, and 2099 is inside
    the Gregorian one — so without this rule it passes clean."""
    payload = clean_payload()
    payload["detail"]["year"] = "2099"
    fired = run(payload)
    assert "year_unknown" not in fired
    assert "year_implausible_future" in fired


def test_mileage_zero_on_old_car():
    payload = clean_payload()
    payload["detail"]["year"] = "2015"
    payload["detail"]["mileage"] = "صفر کیلومتر"
    assert "mileage_zero_on_old_car" in run(payload)


def test_recent_zero_km_car_is_clean():
    """Cars held unused as an inflation hedge are a real feature of this market,
    so the rule must not fire on a merely new-ish zero-kilometre listing."""
    payload = clean_payload()
    payload["detail"]["year"] = str(datetime.now(timezone.utc).year - 1)
    payload["detail"]["mileage"] = "صفر کیلومتر"
    assert run(payload) == []


def test_mileage_implausible_for_age():
    """Under the absolute ceiling, over the per-year one."""
    payload = clean_payload()
    payload["detail"]["year"] = str(datetime.now(timezone.utc).year - 1)
    payload["detail"]["mileage"] = "900,000 کیلومتر"
    fired = run(payload)
    assert "mileage_implausible" not in fired
    assert "mileage_implausible_for_age" in fired


def test_high_mileage_on_an_old_car_is_clean():
    payload = clean_payload()
    payload["detail"]["year"] = "1995"
    payload["detail"]["mileage"] = "900,000 کیلومتر"
    assert "mileage_implausible_for_age" not in run(payload)


def test_installment_without_prepayment():
    payload = clean_payload()
    payload["price"].update(
        {"type": "installment", "price": "0", "prepayment": "0", "payment": "0"}
    )
    assert "installment_without_prepayment" in run(payload)


# --- critical false-positive regressions ------------------------------------

def test_negotiable_ad_with_zero_price_is_clean():
    payload = clean_payload()
    payload["price"].update({"type": "negotiable", "price": "0"})
    assert run(payload) == []


def test_installment_ad_with_zero_price_but_prepayment_has_no_price_rejection():
    payload = clean_payload()
    payload["price"].update(
        {"type": "installment", "price": "0", "prepayment": "800,000,000",
         "payment": "40,000,000", "installments": "36"}
    )
    assert run(payload) == []


def test_lumpsum_ad_with_zero_price_does_reject():
    payload = clean_payload()
    payload["price"].update({"type": "lumpsum", "price": "0"})
    assert run(payload) == ["price_missing_for_lumpsum"]


def test_missing_price_type_is_tolerated():
    payload = clean_payload()
    payload["price"]["type"] = ""
    assert run(payload) == []


def test_verify_never_raises_on_garbage():
    assert {r.rule for r in V.verify_extracted({}, {})} >= {"code_missing", "brand_missing"}
    assert V.verify_extracted(None, None)  # type: ignore[arg-type]


def test_hard_soft_classification():
    hard = {r.rule for r in V.verify_extracted({}, {}) if r.hard}
    assert hard == {"code_missing", "brand_missing"}


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
def test_ad_that_mints_a_dimension_is_flagged(make_payload):
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    payload = make_payload("newbrand1", 15_000_000_000, brand="برند ناشناخته")
    ad = ingest_ad(
        extract_ad(payload, OBSERVED_AT), run=run, observed_at=OBSERVED_AT, publish_at=OBSERVED_AT
    ).ad

    assert ad is not None, "the ad itself is fine — it is the catalog that is unproven"
    assert "unknown_dimension" in ad.quality_flags


@pytest.mark.django_db
def test_ad_using_an_existing_dimension_is_not_flagged(make_payload):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    Model.objects.create(brand=brand, name_fa="405", is_confirmed=True)
    reset_cache()

    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    payload = make_payload("known001", 15_000_000_000)
    ad = ingest_ad(
        extract_ad(payload, OBSERVED_AT), run=run, observed_at=OBSERVED_AT, publish_at=OBSERVED_AT
    ).ad

    assert "unknown_dimension" not in ad.quality_flags


@pytest.mark.django_db
def test_flag_clears_once_the_dimension_exists(make_payload):
    """quality_flags is recomputed every observation, so the second sighting of an
    ad whose brand is now on record must come back clean — otherwise the flag
    would be a permanent scar rather than a statement about the present."""
    run = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    payload = make_payload("newbrand2", 15_000_000_000, brand="برند ناشناخته")
    ad = ingest_ad(
        extract_ad(payload, OBSERVED_AT), run=run, observed_at=OBSERVED_AT, publish_at=OBSERVED_AT
    ).ad
    assert "unknown_dimension" in ad.quality_flags

    reset_cache()  # a later run does not share the first run's memoisation
    later = FetchRun.objects.create(source=FetchRun.Source.LIVE_FETCH)
    ingest_ad(
        extract_ad(payload, OBSERVED_AT), run=later, observed_at=OBSERVED_AT, publish_at=OBSERVED_AT
    )

    assert "unknown_dimension" not in Ad.objects.get(code="newbrand2").quality_flags


@pytest.mark.django_db
def test_unknown_dimension_is_soft():
    """Soft by construction: analytics excludes exactly HARD_RULE_IDS, and an ad
    with a genuinely new model is still a real, usable listing."""
    from apps.jobs.verify import EXTERNAL_FLAG_IDS, HARD_RULE_IDS

    assert "unknown_dimension" in EXTERNAL_FLAG_IDS
    assert "unknown_dimension" not in HARD_RULE_IDS
