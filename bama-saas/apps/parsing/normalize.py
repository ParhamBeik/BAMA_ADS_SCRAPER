"""Calendar-aware model year and mileage normalization for Bama payloads.

Bama publishes ``detail.year`` in **two** calendars depending on brand: Iranian
brands use Jalali ("1399"), imported brands use Gregorian ("2025"), and 20+
brands use both across their own listings. Storing the raw number collapses
1399 and 2025 into one column, which destroys ``(model, variant, year)`` peer
cohorts. ``normalize_model_year`` splits the raw value into both calendars plus
a classification tag so cohorts can key on a single canonical calendar.

``detail.mileage`` is the string "صفر کیلومتر" for ~33% of ads. ``parse_int``
returns ``None`` for it, so a third of the corpus loses a *known* mileage of 0.
``parse_mileage`` preserves 0 as a meaningful value.

Pure Python: no Django, no ORM, stdlib only (plus ``apps.parsing.digits``).
"""

from __future__ import annotations

import re
from typing import Any

from apps.parsing.digits import normalize_digits

# Iranian car-listing convention: Jalali 1404 <-> Gregorian 2025. The true
# offset drifts by one around Nowruz, but listings quote the model year as a
# flat delta, so a constant is what matches the source data.
JALALI_GREGORIAN_OFFSET = 621

# Plausibility bands. Anything outside both is unclassifiable, not a guess.
JALALI_MIN, JALALI_MAX = 1300, 1420
GREGORIAN_MIN, GREGORIAN_MAX = 1900, 2100

# Persian marker for a brand-new car ("zero kilometers"). "صفر" alone is the
# load-bearing token; the "کیلومتر"/"km" suffix varies.
_ZERO_KM_MARKER = "صفر"

_NON_NUMERIC = re.compile(r"[^0-9-]")
# Leading number only: ranges like "1399-1400" yield 1399 instead of crashing
# int() (which is what a bare digit-strip would do).
_LEADING_NUMBER = re.compile(r"-?\d+")


def _to_int(raw: Any) -> int | None:
    """Digits-only int extraction. ``None`` when there is nothing to parse."""
    if raw is None or isinstance(raw, bool):
        return None
    match = _LEADING_NUMBER.search(_NON_NUMERIC.sub("", normalize_digits(str(raw))))
    return int(match.group()) if match else None


def normalize_model_year(raw: Any) -> tuple[int | None, int | None, str]:
    """Return ``(year_jalali, year_gregorian, calendar)``.

    ``calendar`` is one of ``"jalali"``, ``"gregorian"``, ``"unknown"``.

    - 1300..1420 is read as Jalali    -> gregorian = jalali + 621
    - 1900..2100 is read as Gregorian -> jalali = gregorian - 621
    - anything else (None, empty, garbage, 0, negative, out of both bands)
      returns ``(None, None, "unknown")``

    Accepts ``int`` or ``str``, Persian or ASCII digits, with or without
    separators.
    """
    value = _to_int(raw)
    if value is None:
        return None, None, "unknown"
    if JALALI_MIN <= value <= JALALI_MAX:
        return value, value + JALALI_GREGORIAN_OFFSET, "jalali"
    if GREGORIAN_MIN <= value <= GREGORIAN_MAX:
        return value - JALALI_GREGORIAN_OFFSET, value, "gregorian"
    return None, None, "unknown"


def parse_mileage(raw: Any) -> int | None:
    """Kilometers as an int, where ``0`` is a meaningful value.

    - "صفر کیلومتر" (any zero-km phrasing) -> ``0``, never ``None``
    - "120,000" / "۱۲۰,۰۰۰" / "120000 کیلومتر" -> ``120000``
    - ``None``, empty, or no digits and no zero-km marker -> ``None``
    - negative results -> ``None`` (sentinel garbage)
    """
    value = _to_int(raw)
    if value is None:
        # No digits at all: the zero-km phrase is the only other known signal.
        return 0 if raw is not None and _ZERO_KM_MARKER in str(raw) else None
    return value if value >= 0 else None
