"""Payload diffing and human-readable change categorization.

Ported from ``bama-saas/app/services/ingestion.py`` (``_summary``,
``diff_payloads``, ``categories_for``). These are identical to the
original collector's versions; the only cosmetic difference is that
``history.py`` computes the canonical-JSON byte length via its ``canonical_json``
helper, while ``ingestion.py`` inlines the same ``json.dumps(...)`` call. The
length comparison (``<= 1000``) and output shape are the same, so the
``ingestion.py`` form is kept as authoritative.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _summary(value: Any) -> Any:
    """Return ``value`` itself, or a sha256/bytes stub when it is very large."""
    packed = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(packed) <= 1000:
        return value
    return {"sha256": hashlib.sha256(packed).hexdigest(), "bytes": len(packed)}


def diff_payloads(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    """Return path-based differences for the history/change APIs."""
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}/{key}"
            if key not in before:
                changes.append({"path": child, "before": None, "after": _summary(after[key])})
            elif key not in after:
                changes.append({"path": child, "before": _summary(before[key]), "after": None})
            else:
                changes.extend(diff_payloads(before[key], after[key], child))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        return [] if before == after else [{"path": path or "/", "before": _summary(before), "after": _summary(after)}]
    return [] if before == after else [{"path": path or "/", "before": _summary(before), "after": _summary(after)}]


def categories_for(paths: list[str]) -> list[str]:
    """Group low-level JSON paths into user-facing change categories."""
    categories: set[str] = set()
    for path in paths:
        if path.startswith("/price/"):
            categories.add("price/payment")
        elif path == "/detail/description":
            categories.add("description")
        elif path == "/detail/mileage":
            categories.add("mileage")
        elif path == "/detail/location":
            categories.add("location")
        elif path.startswith("/images") or path.startswith("/videos") or path == "/detail/image":
            categories.add("media")
        elif path.startswith("/dealer") or "/seller" in path:
            categories.add("seller/dealer")
        elif path.startswith("/promotion") or path in {"/detail/pin", "/detail/badge", "/detail/specialcase"}:
            categories.add("promotion")
        elif path in {"/detail/type", "/detail/title", "/detail/brand", "/detail/brand_fa", "/detail/trim"}:
            categories.add("route/category")
        elif path.startswith("/detail/") or path.startswith("/specs/"):
            categories.add("vehicle attributes")
        else:
            categories.add("other")
    return sorted(categories)
