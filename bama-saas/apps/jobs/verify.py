"""Rules every extracted ad is checked against before it is persisted.

Each rule is a plain ``(extracted, payload) -> Rejection | None``, so the set is
introspectable and each is testable in isolation.

* ``hard=True``  — the row is unusable and unrepairable (Bama itself sent the
  bad value). It never reaches the Ad table; the payload is quarantined in
  ``IngestReject``, kept and replayable.
* ``hard=False`` — usable but suspicious. Flag only. A spike in one soft rule is
  the signal that Bama changed their schema, and an unparseable publish phrase
  must not remove an otherwise perfect price from the market statistics.

Fired rule ids land in ``Ad.quality_flags``; ``apps.core.quality.verified`` is
the single read-side chokepoint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from apps.jobs.parsing import (
    normalize_model_year, parse_int, parse_mileage, parse_publish_time,
)

# Bama ad codes are short lowercase alphanumerics, e.g. "6mnwbfv5".
CODE_PATTERN = re.compile(r"^[a-z0-9]{6,12}$")

# The only price types present across all 57,262 seed ads. "" means Bama sent no
# type at all, which is tolerated; anything else is a schema change.
KNOWN_PRICE_TYPES = frozenset({"lumpsum", "negotiable", "installment", ""})

# Prices are tomans. Deliberately wide: this catches a unit switch (rials are
# 10x tomans) or a parser regression, not merely an unusual car.
MIN_PLAUSIBLE_PRICE = 10_000_000

MIN_JALALI_YEAR, MAX_JALALI_YEAR = 1300, 1420
MIN_GREGORIAN_YEAR, MAX_GREGORIAN_YEAR = 1900, 2100
MAX_PLAUSIBLE_MILEAGE = 2_000_000

# A zero-km car that is still zero-km years later is not automatically a bug:
# Iranian buyers genuinely hold unused cars as an inflation hedge. Set well past
# that behaviour so the rule fires on data errors, not on a market quirk.
ZERO_KM_MAX_AGE_YEARS = 5

# Iranian taxi/inter-city use reaches ~60k km/year, so only a parse error or a
# transposed digit trips this.
MAX_KM_PER_YEAR = 100_000

# A model year this far ahead of today is a parser artefact, not a pre-order.
MAX_YEARS_AHEAD = 2

# Bump on any change below, so a backfill can tell which rows were judged by
# which generation of the rule set.
RULE_VERSION = 3


@dataclass(frozen=True)
class Rejection:
    rule: str
    detail: str
    hard: bool


def _now_year() -> int:
    return datetime.now(timezone.utc).year


def _model_year_gregorian(extracted: dict, payload: dict) -> int | None:
    """The model year on one comparable scale, or None when unusable.

    ``detail.year`` arrives in either calendar depending on brand, so no
    cross-field rule involving age can read it raw.
    """
    detail = (payload or {}).get("detail") or {}
    _, gregorian, _ = normalize_model_year(detail.get("year", extracted.get("year")))
    return gregorian


def stored_mileage(extracted: dict, payload: dict) -> int | None:
    """Mileage as ingest will actually store it.

    ``extract_ad`` parses with ``positive=True``, which collapses a genuine
    "صفر کیلومتر" to None — so a rule reading ``extracted["mileage"]`` can never
    see a zero. These rules must judge the value that gets stored.
    """
    detail = (payload or {}).get("detail") or {}
    mileage = parse_mileage(detail.get("mileage"))
    return mileage if mileage is not None else extracted.get("mileage")


# --- hard rules ------------------------------------------------------------

def _code_missing(extracted, payload):
    code = extracted.get("code") or ""
    if not CODE_PATTERN.match(code):
        return Rejection("code_missing", f"code={code!r} is not ^[a-z0-9]{{6,12}}$", True)


def _price_missing_for_lumpsum(extracted, payload):
    # Only lumpsum needs a price: negotiable ads legitimately carry "0" (12,394
    # of 57,262 seed ads) and installment ads carry it in prepayment.
    if extracted.get("price_type") != "lumpsum":
        return None
    price = extracted.get("current_price")
    if price is None or price <= 0:
        return Rejection("price_missing_for_lumpsum", f"lumpsum ad with price={price!r}", True)


def _price_too_low(extracted, payload):
    # A sub-10M-toman car price is a unit switch or a parser regression. Left in
    # the sample it drags every mean down, so the row must not be averaged.
    price = extracted.get("current_price")
    if price is not None and price < MIN_PLAUSIBLE_PRICE:
        return Rejection("price_too_low", f"price={price} below {MIN_PLAUSIBLE_PRICE} tomans", True)


def _brand_missing(extracted, payload):
    brand = (extracted.get("brand") or "").strip()
    model = (extracted.get("model") or "").strip()
    if not brand or not model:
        return Rejection("brand_missing", f"brand={brand!r} model={model!r}", True)


# --- soft rules ------------------------------------------------------------

def _price_type_unknown(extracted, payload):
    price_type = extracted.get("price_type") or ""
    if price_type not in KNOWN_PRICE_TYPES:
        return Rejection("price_type_unknown", f"price_type={price_type!r}", False)


def _price_sentinel(extracted, payload):
    # Soft: Bama encodes "no price" as "0" or "-1" on negotiable ads, and the
    # negative never reaches current_price (the extractor keeps only positives),
    # so it cannot poison a statistic. A -1 on a lumpsum ad is a real problem,
    # but _price_missing_for_lumpsum already fires hard on it.
    raw = ((payload or {}).get("price") or {}).get("price")
    value = parse_int(raw)
    if value is not None and value < 0:
        return Rejection("price_sentinel", f"raw price={raw!r} parses to {value}", False)


def _year_unknown(extracted, payload):
    year = extracted.get("year")
    if year is None:
        return None
    if not (MIN_JALALI_YEAR <= year <= MAX_JALALI_YEAR
            or MIN_GREGORIAN_YEAR <= year <= MAX_GREGORIAN_YEAR):
        return Rejection("year_unknown", f"year={year} is neither Jalali nor Gregorian", False)


def _year_implausible_future(extracted, payload):
    # _year_unknown only checks the value falls in *a* known band, so 2099 passes.
    gregorian = _model_year_gregorian(extracted, payload)
    if gregorian is None:
        return None
    limit = _now_year() + MAX_YEARS_AHEAD
    if gregorian > limit:
        return Rejection("year_implausible_future", f"model year {gregorian} is beyond {limit}", False)


def _mileage_implausible(extracted, payload):
    mileage = extracted.get("mileage")
    if mileage is not None and not 0 <= mileage <= MAX_PLAUSIBLE_MILEAGE:
        return Rejection(
            "mileage_implausible",
            f"mileage={mileage} outside 0..{MAX_PLAUSIBLE_MILEAGE} km", False,
        )


def _mileage_zero_on_old_car(extracted, payload):
    # Each field is individually legal; the pair is the contradiction.
    if stored_mileage(extracted, payload) != 0:
        return None
    gregorian = _model_year_gregorian(extracted, payload)
    if gregorian is None:
        return None
    age = _now_year() - gregorian
    if age > ZERO_KM_MAX_AGE_YEARS:
        return Rejection("mileage_zero_on_old_car", f"0 km on a {age}-year-old car ({gregorian})", False)


def _mileage_implausible_for_age(extracted, payload):
    # _mileage_implausible bounds the absolute number; this bounds it against
    # the car's age, which is what catches 900,000 km on a two-year-old car.
    mileage = stored_mileage(extracted, payload)
    if not mileage or mileage <= 0:
        return None
    gregorian = _model_year_gregorian(extracted, payload)
    if gregorian is None:
        return None
    # A car sold new in its model year still has one year of legitimate use.
    age = max(_now_year() - gregorian, 1)
    per_year = mileage / age
    if per_year > MAX_KM_PER_YEAR:
        return Rejection(
            "mileage_implausible_for_age",
            f"{mileage} km over {age} year(s) = {per_year:,.0f} km/year", False,
        )


def _installment_without_prepayment(extracted, payload):
    # An installment ad carries its money in prepayment/payment rather than
    # price. One with neither states no price at all.
    if extracted.get("price_type") != "installment":
        return None
    if extracted.get("current_prepayment") or extracted.get("current_payment"):
        return None
    return Rejection("installment_without_prepayment",
                     "installment ad with no prepayment or payment", False)


def _publish_unparseable(extracted, payload):
    phrase = (extracted.get("publish_phrase") or "").strip()
    if phrase and parse_publish_time(phrase, datetime.now(timezone.utc)) is None:
        return Rejection("publish_unparseable", f"publish_phrase={phrase!r}", False)


RULES: tuple[Callable[[dict, dict], Rejection | None], ...] = (
    _code_missing,
    _price_type_unknown,
    _price_missing_for_lumpsum,
    _price_sentinel,
    _price_too_low,
    _year_unknown,
    _year_implausible_future,
    _mileage_implausible,
    _mileage_zero_on_old_car,
    _mileage_implausible_for_age,
    _installment_without_prepayment,
    _publish_unparseable,
    _brand_missing,
)

# Rule ids whose presence makes a row unusable. Analytics excludes exactly
# these (apps.core.quality.verified).
HARD_RULE_IDS = frozenset({
    "code_missing", "price_missing_for_lumpsum", "price_too_low", "brand_missing",
})

# Flags raised outside RULES because they need database state the rule signature
# deliberately does not carry. Listed here so the full flag vocabulary is
# discoverable in one place.
#
# ``unknown_dimension`` — the ad minted a Brand or Model that did not exist
# before. Brand/model are parsed out of the title, so a Bama format change would
# otherwise invent catalog rows in silence. Raised by apps/jobs/ingest.py.
EXTERNAL_FLAG_IDS = frozenset({"unknown_dimension"})


def verify_extracted(extracted: dict[str, Any], payload: dict[str, Any]) -> list[Rejection]:
    """Run every rule. Returns [] when the ad is clean. Never raises."""
    extracted = extracted or {}
    payload = payload or {}
    rejections = []
    for rule in RULES:
        try:
            result = rule(extracted, payload)
        except Exception as exc:  # a broken rule must never block ingestion
            result = Rejection(f"{rule.__name__.lstrip('_')}_errored", repr(exc), False)
        if result is not None:
            rejections.append(result)
    return rejections
