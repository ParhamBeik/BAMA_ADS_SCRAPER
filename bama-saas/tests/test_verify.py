"""Unit tests for the ingest verification rules.

Test type: unit. Each rule is an independent pure predicate over an
already-extracted dict, so a dict-in/list-out test is the cheapest level that
proves a rule fires exactly when it should — no DB, no fixtures.
"""

from __future__ import annotations

import ast
import copy
import inspect
from datetime import datetime, timezone

import pytest

from apps.jobs.services import verify as V
from apps.parsing import extract_ad

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
    assert len(V.RULES) == 14


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


def test_price_too_high_is_hard():
    """Above 20bn toman is a typo or unit switch, not a supercar, so HARD."""
    payload = clean_payload()
    payload["price"]["price"] = "21,000,000,000"
    extracted = extract_ad(payload, OBSERVED_AT)
    rejections = V.verify_extracted(extracted, payload)
    assert "price_too_high" in [r.rule for r in rejections]
    assert next(r for r in rejections if r.rule == "price_too_high").hard is True
    assert "price_too_high" in V.HARD_RULE_IDS


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
