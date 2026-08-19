"""Verification rules applied to every extracted ad before it is persisted.

Each rule is a plain ``(extracted, payload) -> Rejection | None`` function, and
``RULES`` is the ordered tuple of them, so the rule set is introspectable and
each rule can be tested in isolation.

Hard vs soft:
- ``hard=True``  -> the row is unusable; the caller also writes an ``IngestReject``
  row so the payload is quarantined (kept, never dropped) and replayable.
- ``hard=False`` -> the row is usable but suspicious; flag only. A spike in one
  soft rule is the signal that Bama changed their schema.

Either way the caller stores the fired rule ids in ``Ad.quality_flags``;
``apps.core.services.quality.verified`` is the single read-side chokepoint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from apps.parsing import normalize_model_year, parse_int, parse_mileage, parse_publish_time

# Bama ad codes are short lowercase alphanumerics, e.g. "6mnwbfv5".
CODE_PATTERN = re.compile(r"^[a-z0-9]{6,12}$")

# The only price types present across all 57,262 seed ads. "" means Bama sent
# no type at all, which is tolerated; anything else is a schema change.
KNOWN_PRICE_TYPES = frozenset({"lumpsum", "negotiable", "installment", ""})

# Prices are tomans. The band is deliberately wide: it catches a unit switch
# (rials are 10x tomans) or a parser regression, not merely an unusual car.
MIN_PLAUSIBLE_PRICE = 10_000_000
# Jalali years (~1300..1420) and Gregorian years both appear in detail.year.
MIN_JALALI_YEAR, MAX_JALALI_YEAR = 1300, 1420
MIN_GREGORIAN_YEAR, MAX_GREGORIAN_YEAR = 1900, 2100

MAX_PLAUSIBLE_MILEAGE = 2_000_000

# Cross-field bounds. Each individual field below is legal on its own; only the
# combination is impossible, which is exactly what a per-field rule cannot see.
#
# A zero-kilometre car that is still zero-kilometre years later is not automatically
# a bug here: Iranian buyers genuinely hold unused cars as an inflation hedge, so
# "0 km on a 3-year-old car" is a real listing. The threshold is set well past that
# behaviour so the rule fires on data errors rather than on a market quirk.
ZERO_KM_MAX_AGE_YEARS = 5

# Above this the odometer contradicts the model year. Iranian taxi/inter-city use
# reaches ~60k km/year, so the bound is set high enough that only a parse error or
# a transposed digit trips it.
MAX_KM_PER_YEAR = 100_000

# A model year this far ahead of today is a parser artefact, not a pre-order.
MAX_YEARS_AHEAD = 2

# Rule-set version. Bump on any change to the rules below so that a backfill can
# tell which rows were judged by which generation of the rule set.
RULE_VERSION = 3


@dataclass(frozen=True)
class Rejection:
    rule: str
    detail: str
    hard: bool


def _rule_code_missing(extracted: dict, payload: dict) -> Rejection | None:
    code = extracted.get("code") or ""
    if not CODE_PATTERN.match(code):
        return Rejection("code_missing", f"code={code!r} is not ^[a-z0-9]{{6,12}}$", True)
    return None


def _rule_price_type_unknown(extracted: dict, payload: dict) -> Rejection | None:
    price_type = extracted.get("price_type") or ""
    if price_type not in KNOWN_PRICE_TYPES:
        return Rejection("price_type_unknown", f"price_type={price_type!r}", False)
    return None


def _rule_price_missing_for_lumpsum(extracted: dict, payload: dict) -> Rejection | None:
    # Only lumpsum needs a price. negotiable ads legitimately carry "0"
    # (12,394 of 57,262 seed ads) and installment ads carry it in prepayment.
    if extracted.get("price_type") != "lumpsum":
        return None
    price = extracted.get("current_price")
    if price is None or price <= 0:
        return Rejection("price_missing_for_lumpsum", f"lumpsum ad with price={price!r}", True)
    return None


def _rule_price_sentinel(extracted: dict, payload: dict) -> Rejection | None:
    # Soft, not hard. Bama encodes "no price" as either "0" or "-1" on negotiable
    # ads; the negative never reaches ``current_price`` (the extractor keeps only
    # positives), so it cannot poison a statistic. A -1 on a *lumpsum* ad is a
    # real problem, but price_missing_for_lumpsum already fires hard on it, so
    # treating this as hard only quarantined perfectly usable negotiable ads.
    raw = ((payload or {}).get("price") or {}).get("price")
    value = parse_int(raw)
    if value is not None and value < 0:
        return Rejection("price_sentinel", f"raw price={raw!r} parses to {value}", False)
    return None


def _rule_price_too_low(extracted: dict, payload: dict) -> Rejection | None:
    # Hard: a sub-10M-toman car price is a unit switch (rials) or a parser
    # regression, essentially never a real listing. Left in the sample it drags
    # every mean and every Bollinger band down, so the row must not be averaged.
    price = extracted.get("current_price")
    if price is None:
        return None
    if price < MIN_PLAUSIBLE_PRICE:
        return Rejection(
            "price_too_low", f"price={price} below {MIN_PLAUSIBLE_PRICE} tomans", True
        )
    return None


def _rule_year_unknown(extracted: dict, payload: dict) -> Rejection | None:
    year = extracted.get("year")
    if year is None:
        return None
    jalali = MIN_JALALI_YEAR <= year <= MAX_JALALI_YEAR
    gregorian = MIN_GREGORIAN_YEAR <= year <= MAX_GREGORIAN_YEAR
    if not (jalali or gregorian):
        return Rejection("year_unknown", f"year={year} is neither Jalali nor Gregorian", False)
    return None


def _rule_mileage_implausible(extracted: dict, payload: dict) -> Rejection | None:
    mileage = extracted.get("mileage")
    if mileage is None:
        return None
    if mileage < 0 or mileage > MAX_PLAUSIBLE_MILEAGE:
        return Rejection(
            "mileage_implausible", f"mileage={mileage} outside 0..{MAX_PLAUSIBLE_MILEAGE} km", False
        )
    return None


def _rule_publish_unparseable(extracted: dict, payload: dict) -> Rejection | None:
    phrase = extracted.get("publish_phrase") or ""
    if not phrase.strip():
        return None
    if parse_publish_time(phrase, datetime.now(timezone.utc)) is None:
        return Rejection("publish_unparseable", f"publish_phrase={phrase!r}", False)
    return None


def _model_year_gregorian(extracted: dict, payload: dict) -> int | None:
    """The model year on one comparable scale, or None when it is unusable.

    ``detail.year`` arrives in either calendar depending on the brand, so no
    cross-field rule involving age can read it raw.
    """
    detail = (payload or {}).get("detail") or {}
    _, gregorian, _ = normalize_model_year(detail.get("year", extracted.get("year")))
    return gregorian


def stored_mileage(extracted: dict, payload: dict) -> int | None:
    """Mileage as ingest will actually store it.

    ``extract_ad`` parses mileage with ``positive=True``, which collapses a genuine
    "صفر کیلومتر" to None — so a rule reading ``extracted["mileage"]`` can never see
    a zero. ``parse_mileage`` is what ingest uses for the stored column, and these
    rules must judge the value that gets stored.
    """
    detail = (payload or {}).get("detail") or {}
    mileage = parse_mileage(detail.get("mileage"))
    return mileage if mileage is not None else extracted.get("mileage")


def _rule_year_implausible_future(extracted: dict, payload: dict) -> Rejection | None:
    # year_unknown only checks that the value falls in *a* known calendar band, so
    # a year of 2099 passes it. Soft: the band is wide enough that anything caught
    # here is a parse artefact, but it is not worth discarding a whole ad over.
    gregorian = _model_year_gregorian(extracted, payload)
    if gregorian is None:
        return None
    limit = datetime.now(timezone.utc).year + MAX_YEARS_AHEAD
    if gregorian > limit:
        return Rejection(
            "year_implausible_future", f"model year {gregorian} is beyond {limit}", False
        )
    return None


def _rule_mileage_zero_on_old_car(extracted: dict, payload: dict) -> Rejection | None:
    # Each field is individually legal; the pair is the contradiction. Soft,
    # because a genuinely stored-unused car is a real thing in this market — see
    # ZERO_KM_MAX_AGE_YEARS.
    if stored_mileage(extracted, payload) != 0:
        return None
    gregorian = _model_year_gregorian(extracted, payload)
    if gregorian is None:
        return None
    age = datetime.now(timezone.utc).year - gregorian
    if age > ZERO_KM_MAX_AGE_YEARS:
        return Rejection(
            "mileage_zero_on_old_car", f"0 km on a {age}-year-old car ({gregorian})", False
        )
    return None


def _rule_mileage_implausible_for_age(extracted: dict, payload: dict) -> Rejection | None:
    # mileage_implausible bounds the absolute number; this bounds it against the
    # car's age, which is what catches 900,000 km on a two-year-old car.
    mileage = stored_mileage(extracted, payload)
    if not mileage or mileage <= 0:
        return None
    gregorian = _model_year_gregorian(extracted, payload)
    if gregorian is None:
        return None
    # A car sold new in its model year still has one year of legitimate use.
    age = max(datetime.now(timezone.utc).year - gregorian, 1)
    per_year = mileage / age
    if per_year > MAX_KM_PER_YEAR:
        return Rejection(
            "mileage_implausible_for_age",
            f"{mileage} km over {age} year(s) = {per_year:,.0f} km/year",
            False,
        )
    return None


def _rule_installment_without_prepayment(extracted: dict, payload: dict) -> Rejection | None:
    # An installment ad carries its money in prepayment/payment rather than price
    # (which is why price_missing_for_lumpsum deliberately skips it). One with
    # neither states no price at all, so it cannot enter any price statistic.
    if extracted.get("price_type") != "installment":
        return None
    if extracted.get("current_prepayment") or extracted.get("current_payment"):
        return None
    return Rejection(
        "installment_without_prepayment", "installment ad with no prepayment or payment", False
    )


def _rule_brand_missing(extracted: dict, payload: dict) -> Rejection | None:
    brand = (extracted.get("brand") or "").strip()
    model = (extracted.get("model") or "").strip()
    if not brand or not model:
        return Rejection("brand_missing", f"brand={brand!r} model={model!r}", True)
    return None


RULES: tuple[Callable[[dict, dict], Rejection | None], ...] = (
    _rule_code_missing,
    _rule_price_type_unknown,
    _rule_price_missing_for_lumpsum,
    _rule_price_sentinel,
    _rule_price_too_low,
    _rule_year_unknown,
    _rule_year_implausible_future,
    _rule_mileage_implausible,
    _rule_mileage_zero_on_old_car,
    _rule_mileage_implausible_for_age,
    _rule_installment_without_prepayment,
    _rule_publish_unparseable,
    _rule_brand_missing,
)

# Flags raised outside the RULES tuple because they need database state the rule
# signature deliberately does not carry. Listed here so the full flag vocabulary is
# discoverable in one place.
#
# ``unknown_dimension`` — the ad minted a Brand or Model that did not exist before.
# Brand/model are parsed out of the title (apps/parsing/extract.py), so a Bama title
# format change would otherwise invent catalog rows in silence and every cohort keyed
# on them would be wrong. Raised by apps/jobs/services/ingest.py.
EXTERNAL_FLAG_IDS = frozenset({"unknown_dimension"})

# Rule ids whose presence makes a row unusable. Analytics excludes exactly these
# (see apps.core.services.quality.verified); soft flags stay in the data and only
# serve monitoring, so one unparseable publish phrase never removes an otherwise
# perfectly good price from the market statistics.
HARD_RULE_IDS = frozenset({
    "code_missing",
    "price_missing_for_lumpsum",
    "price_too_low",
    "brand_missing",
})


def verify_extracted(extracted: dict[str, Any], payload: dict[str, Any]) -> list[Rejection]:
    """Run every rule. Returns [] when the ad is clean. Never raises."""
    extracted = extracted or {}
    payload = payload or {}
    rejections = []
    for rule in RULES:
        try:
            result = rule(extracted, payload)
        except Exception as exc:  # a broken rule must never block ingestion
            result = Rejection(f"{rule.__name__.removeprefix('_rule_')}_errored", repr(exc), False)
        if result is not None:
            rejections.append(result)
    return rejections
