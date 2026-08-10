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

from apps.parsing.constants import VOLATILE_PAYLOAD_PATHS


def fingerprint(value: Any) -> str:
    """Stable sha256 over canonical (sorted-key, compact) JSON of ``value``."""
    packed = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(packed.encode()).hexdigest()


def _drop_path(node: Any, parts: tuple[str, ...]) -> None:
    """Remove one dotted path from a nested dict, in place. Missing is fine."""
    for part in parts[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            return
    if isinstance(node, dict):
        node.pop(parts[-1], None)


def semantic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop fields that change every fetch but do not mean the ad content changed.

    See ``VOLATILE_PAYLOAD_PATHS`` for which fields and the measured reason why.
    """
    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    for path in VOLATILE_PAYLOAD_PATHS:
        _drop_path(normalized, tuple(path.split(".")))
    return normalized


def payload_hashes(payload: dict[str, Any]) -> tuple[str, str]:
    """Return ``(raw_hash, semantic_hash)`` for change detection.

    ``raw_hash`` covers the whole payload byte for byte and is the permanent
    record; ``semantic_hash`` ignores the observation-only and dealer-wide fields
    listed in ``VOLATILE_PAYLOAD_PATHS``, and is what decides "is this a new
    version of the ad".
    """
    return fingerprint(payload), fingerprint(semantic_payload(payload))
