"""Payload fingerprinting and semantic hashing.

Ported from ``bama-saas/app/services/ingestion.py``. ``fingerprint`` and
``semantic_payload`` here are byte-for-byte equivalent to the authoritative
``ingestion.py`` versions (and match the original collector's hashing).

Note: the original collector factors the canonical-JSON encoding into
a separate ``canonical_json`` helper; ``ingestion.py`` inlines the same
``json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"))``
call inside ``fingerprint``. The output bytes are identical, so we keep the
``ingestion.py`` form (a single ``fingerprint`` helper) as authoritative.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from apps.parsing.constants import VOLATILE_DETAIL_KEYS


def fingerprint(value: Any) -> str:
    """Stable sha256 over canonical (sorted-key, compact) JSON of ``value``."""
    packed = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(packed.encode()).hexdigest()


def semantic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop fields that change every fetch but do not mean the ad content changed."""
    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    detail = normalized.get("detail")
    if isinstance(detail, dict):
        for key in VOLATILE_DETAIL_KEYS:
            detail.pop(key, None)
    return normalized


def payload_hashes(payload: dict[str, Any]) -> tuple[str, str]:
    """Return ``(raw_hash, semantic_hash)`` for change detection.

    ``raw_hash`` covers the whole payload; ``semantic_hash`` ignores volatile
    ``detail.time`` / ``detail.rank`` observation-only fields.
    """
    return fingerprint(payload), fingerprint(semantic_payload(payload))
