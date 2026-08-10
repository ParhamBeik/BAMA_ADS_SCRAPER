"""Pure-Python Bama parsing package (zero Django imports).

Public API re-exported here for convenience. All functions depend only on the
stdlib (plus ``jdatetime``); none of them touch Django, the ORM, or any DB
session. Persistence is the consuming app's responsibility.

Re-exported:
    extract_ad, parse_int, normalize_digits, parse_publish_time,
    parse_absolute_jalali, fingerprint, semantic_payload, payload_hashes,
    diff_payloads, categories_for, pure_ad, unpack_payload,
    normalize_model_year, parse_mileage, JALALI_GREGORIAN_OFFSET,
    VOLATILE_DETAIL_KEYS, REAPPEAR_AFTER_SECONDS
"""

from __future__ import annotations

from apps.parsing.constants import (
    REAPPEAR_AFTER_SECONDS,
    SEMANTIC_HASH_VERSION,
    VOLATILE_DETAIL_KEYS,
    VOLATILE_PAYLOAD_PATHS,
)
from apps.parsing.diff import categories_for, diff_payloads
from apps.parsing.digits import normalize_digits, parse_int
from apps.parsing.extract import extract_ad
from apps.parsing.hashes import fingerprint, payload_hashes, semantic_payload
from apps.parsing.normalize import (
    JALALI_GREGORIAN_OFFSET,
    normalize_model_year,
    parse_mileage,
)
from apps.parsing.payload import pure_ad, unpack_payload
from apps.parsing.time_parser import parse_absolute_jalali, parse_publish_time

__all__ = [
    "extract_ad",
    "parse_int",
    "normalize_digits",
    "parse_publish_time",
    "parse_absolute_jalali",
    "fingerprint",
    "semantic_payload",
    "payload_hashes",
    "diff_payloads",
    "categories_for",
    "pure_ad",
    "unpack_payload",
    "normalize_model_year",
    "parse_mileage",
    "JALALI_GREGORIAN_OFFSET",
    "VOLATILE_DETAIL_KEYS",
    "VOLATILE_PAYLOAD_PATHS",
    "SEMANTIC_HASH_VERSION",
    "REAPPEAR_AFTER_SECONDS",
]
