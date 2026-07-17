"""Digit normalization and integer parsing for Bama payloads.

This module owns the single canonical ``normalize_digits`` implementation used
by the whole parsing package (including ``time_parser``). Persian digits are
translated to ASCII so downstream regex/int parsing works unchanged.

Ported from ``bama-saas/app/services/time_parser.py`` (``normalize_digits``)
and ``bama-saas/app/services/ingestion.py`` (``parse_int``).
"""

from __future__ import annotations

import re
from typing import Any

# Persian (Eastern Arabic) digits ۰..۹ mapped to ASCII 0..9.
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def normalize_digits(value: str) -> str:
    """Translate Persian digits in ``value`` to ASCII digits."""
    return value.translate(PERSIAN_DIGITS)


def parse_int(value: Any, *, positive: bool = False) -> int | None:
    """Best-effort int extraction from Bama free-form strings.

    Normalizes Persian digits, strips everything except ASCII digits and ``-``,
    then converts to int. Returns ``None`` for empty/bool/unsatisfiable input.
    When ``positive`` is set, non-positive results return ``None``.
    """
    if value is None or isinstance(value, bool):
        return None
    digits = re.sub(r"[^0-9-]", "", normalize_digits(str(value)))
    if not digits or digits == "-":
        return None
    number = int(digits)
    return number if not positive or number > 0 else None
