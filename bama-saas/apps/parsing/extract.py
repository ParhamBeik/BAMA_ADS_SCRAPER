"""Flatten a single nested Bama payload into query-friendly ad columns.

Ported verbatim from ``bama-saas/app/services/ingestion.py``'s ``extract_ad``.
Only the ORM-writing helpers (``ingest_payload``, ``_record_change_events``,
``_record_unknown``) are intentionally NOT ported here; they belong to the
ORM/persistence layer of the consuming app.

Field source map (verified against ``ingestion.py``):
- ``code``           <- ``detail.code`` (missing -> return None)
- ``title``          <- ``detail.title``
- ``brand``          <- ``detail.brand_fa`` or the first ``title`` part
- ``model``          <- the second ``title`` part (split on ``،``)
- ``trim``           <- ``detail.trim``
- ``year``           <- ``detail.year`` via ``parse_int(positive=True)``
- ``mileage``        <- ``detail.mileage`` via ``parse_int(positive=True)``
- ``category``       <- ``detail.type``
- ``publish_phrase`` <- ``detail.time``
- ``current_price``  <- ``price.price`` via ``parse_int(positive=True)``
- ``current_payment``<- ``price.payment`` via ``parse_int(positive=True)``
- price is nested: ``payload["price"]["price"]`` (a string like
  ``"5,230,000,000 تومان"``), NOT a scalar.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from apps.parsing.digits import parse_int


def extract_ad(payload: dict[str, Any], observed_at: datetime) -> dict[str, Any] | None:
    """Flatten one Bama payload into query-friendly current ad columns."""
    detail = payload.get("detail") or {}
    code = detail.get("code")
    if not code:
        return None
    title = detail.get("title") or ""
    title_parts = [part.strip() for part in title.split("،", 1)]
    price = payload.get("price") or {}
    return {
        "code": str(code), "title": detail.get("title"),
        "brand": detail.get("brand_fa") or (title_parts[0] if title_parts else None),
        "model": title_parts[1] if len(title_parts) > 1 else None, "trim": detail.get("trim"),
        "year": parse_int(detail.get("year"), positive=True), "mileage": parse_int(detail.get("mileage"), positive=True),
        "location": detail.get("location"), "body_type": detail.get("body_type_fa") or detail.get("body_type"),
        "body_color": detail.get("body_color"), "body_status": detail.get("body_status"), "fuel": detail.get("fuel"),
        "transmission": detail.get("transmission"), "category": detail.get("type"), "url": detail.get("url"),
        "publish_phrase": detail.get("time"), "current_price": parse_int(price.get("price"), positive=True),
        "current_payment": parse_int(price.get("payment"), positive=True),
        "current_prepayment": parse_int(price.get("prepayment"), positive=True),
        "current_installments": parse_int(price.get("installments"), positive=True), "price_type": price.get("type"),
        "last_seen_at": observed_at, "raw_payload": payload,
    }
