"""Verification rules that compare an ad to its own previous state.

``verify.py`` judges one row in isolation, which is structurally blind to a whole
class of corruption: every individual value can sit inside its plausible band
while the *transition* between two sightings is impossible. A price that goes
15,000,000,000 → 1,500,000,000 overnight is two perfectly legal prices and one
unit switch.

These rules need the previously-stored row, so unlike ``verify.RULES`` they run
after the dimension resolver rather than as a pure function of the payload. They
reuse ``verify.Rejection`` so the flag vocabulary stays in one shape and
``quality.verified`` remains the single read-side gate.

All three are soft. When a transition is impossible we know *one* of the two
observations is wrong, but not which one — quarantining the ad on that basis
would discard a good row half the time.
"""

from __future__ import annotations

from apps.parsing import normalize_model_year

from .verify import Rejection, stored_mileage

# A price that changes by this factor or more between two sightings of the same
# ad. Rials are 10x tomans, so a unit switch lands well outside; genuine market
# moves and even aggressive price cuts land well inside. The band is deliberately
# wide — this rule exists to catch corruption, not to referee pricing.
PRICE_JUMP_FACTOR = 3.0

# Odometers only go up. The tolerance absorbs a seller correcting a typo
# (120,000 → 119,500) without absorbing a rollback or a parse error.
MILEAGE_REGRESSION_TOLERANCE_KM = 1_000


def _rule_price_jump(extracted: dict, payload: dict, previous, dims: dict) -> Rejection | None:
    old = previous.current_price
    new = extracted.get("current_price")
    if not old or not new or old <= 0 or new <= 0:
        return None
    ratio = new / old
    if ratio >= PRICE_JUMP_FACTOR or ratio <= 1 / PRICE_JUMP_FACTOR:
        return Rejection(
            "price_jump", f"price {old} -> {new} (x{ratio:.2f}) between sightings", False
        )
    return None


def _rule_mileage_regression(
    extracted: dict, payload: dict, previous, dims: dict
) -> Rejection | None:
    old = previous.mileage
    # Not extracted["mileage"]: that is parsed with positive=True, so a genuine
    # zero arrives as None and the 50,000 -> 0 rollback — the loudest case there
    # is — would be invisible here.
    new = stored_mileage(extracted, payload)
    if old is None or new is None:
        return None
    if new < old - MILEAGE_REGRESSION_TOLERANCE_KM:
        return Rejection(
            "mileage_regression", f"odometer {old} -> {new} km between sightings", False
        )
    return None


def _rule_identity_mutation(
    extracted: dict, payload: dict, previous, dims: dict
) -> Rejection | None:
    """The listing code now describes a different car.

    Bama codes are recycled. Left unnoticed this silently rewrites an ad's whole
    history: the price series of a Pride continues into a Peugeot, and the ad's
    apparent time-on-market spans two unrelated cars.

    Compares model *year* and brand, deliberately not model or variant. The first
    draft compared model_id and fired on 137 live ads, none of which were recycled
    codes — every one was Bama renaming the model in the ad title, e.g.
    "تیگو 8 پرو مکس (F8 PRO MAX)" becoming "تیگو 8 پرو مکس (F8)". Model and variant
    names are free text controlled by the source and they move; a car's model year
    does not, and neither does its make. Missing a recycled code that happens to
    reuse the same brand and year is the acceptable direction to be wrong in.

    (The renames are a real problem of their own — they leave two catalog rows for
    one car — but that is a catalog-merge question, not an ad-quality one. See
    ``manage.py confirm_dimensions``, which reports codes seen under two models.)
    """
    changes = []
    detail = (payload or {}).get("detail") or {}
    year_jalali, _, _ = normalize_model_year(detail.get("year", extracted.get("year")))
    if year_jalali and previous.year_jalali and year_jalali != previous.year_jalali:
        changes.append(f"model year {previous.year_jalali} -> {year_jalali}")
    brand = dims.get("brand")
    if brand is not None and previous.brand_id and brand.pk != previous.brand_id:
        changes.append(f"brand {previous.brand_id} -> {brand.pk}")
    if not changes:
        return None
    return Rejection("identity_mutation", "; ".join(changes), False)


RULES = (_rule_price_jump, _rule_mileage_regression, _rule_identity_mutation)


def verify_against_previous(
    extracted: dict, payload: dict, previous, dims: dict
) -> list[Rejection]:
    """Run every temporal rule. Returns [] on the first sighting. Never raises."""
    if previous is None:
        return []
    rejections = []
    for rule in RULES:
        try:
            result = rule(extracted or {}, payload or {}, previous, dims or {})
        except Exception as exc:  # a broken rule must never block ingestion
            result = Rejection(f"{rule.__name__.removeprefix('_rule_')}_errored", repr(exc), False)
        if result is not None:
            rejections.append(result)
    return rejections
