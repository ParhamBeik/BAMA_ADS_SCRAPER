"""Bama publish-time parsing.

Ported from ``bama-saas/app/services/time_parser.py``. The only change is that
``normalize_digits`` is imported from ``apps.parsing.digits`` rather than
redefined here, per the package's dedup rule. Behavior is identical.

Parsing order (must be preserved):
1. Absolute Jalali date ``YYYY/MM/DD`` -> UTC midnight of that Gregorian day.
2. Curated relative phrases (``RELATIVE_UNITS``) -> ``observed_at - delta``.
3. Numeric relative phrases like ``"۲ ساعت پیش"`` -> ``observed_at - delta``.
4. Otherwise invoke ``on_unknown`` (if provided) and return ``None``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import jdatetime

from apps.parsing.digits import normalize_digits

JALALI_DATE_PATTERN = re.compile(r"(?P<year>1[34]\d{2})/(?P<month>\d{1,2})/(?P<day>\d{1,2})")
RELATIVE_UNITS = {
    "لحظاتی پیش": timedelta(), "دقایقی پیش": timedelta(minutes=5),
    "نیم ساعت پیش": timedelta(minutes=30), "یک ساعت پیش": timedelta(hours=1),
    "دیروز": timedelta(days=1), "پریروز": timedelta(days=2),
}


# ---------------------------------------------------------------------------
# Bama publish-time parsing
# ---------------------------------------------------------------------------

def parse_absolute_jalali(value: str) -> datetime | None:
    """Convert an absolute Jalali date phrase into UTC midnight."""
    match = JALALI_DATE_PATTERN.search(normalize_digits(value.strip()))
    if not match:
        return None
    try:
        date = jdatetime.date(*(int(match.group(k)) for k in ("year", "month", "day")))
    except ValueError:
        return None
    return datetime.combine(date.togregorian(), datetime.min.time(), tzinfo=timezone.utc)


def parse_publish_time(
    value: str | None,
    observed_at: datetime,
    on_unknown: Callable[[str], Any] | None = None,
) -> datetime | None:
    """Parse absolute dates, common relative phrases, and numeric relative phrases."""
    if not value:
        return None
    absolute = parse_absolute_jalali(value)
    if absolute:
        return absolute
    text = normalize_digits(value.strip())
    if text in RELATIVE_UNITS:
        return observed_at - RELATIVE_UNITS[text]
    match = re.fullmatch(r"(\d+)\s+(دقیقه|ساعت|روز|هفته|ماه)\s+پیش", text)
    if match:
        number = int(match.group(1))
        unit = match.group(2)
        kwargs = {"دقیقه": {"minutes": number}, "ساعت": {"hours": number}, "روز": {"days": number},
                  "هفته": {"weeks": number}, "ماه": {"days": number * 30}}[unit]
        return observed_at - timedelta(**kwargs)
    if on_unknown:
        on_unknown(value.strip())
    return None
