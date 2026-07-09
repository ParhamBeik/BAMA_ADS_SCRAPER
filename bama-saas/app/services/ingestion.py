from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.schema import Ad, AdChangeEvent, AdMedia, AdMetadata, AdObservation, AdVersion, PriceObservation, UnknownTimePhrase
from app.services.time_parser import normalize_digits, parse_publish_time

VOLATILE_DETAIL_KEYS = {"time", "rank"}
REAPPEAR_AFTER_SECONDS = 14 * 86400


# ---------------------------------------------------------------------------
# Small normalization helpers
# ---------------------------------------------------------------------------

def parse_int(value: Any, *, positive: bool = False) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    digits = re.sub(r"[^0-9-]", "", normalize_digits(str(value)))
    if not digits or digits == "-":
        return None
    number = int(digits)
    return number if not positive or number > 0 else None


def fingerprint(value: Any) -> str:
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
    return fingerprint(payload), fingerprint(semantic_payload(payload))


def _summary(value: Any) -> Any:
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


# ---------------------------------------------------------------------------
# Bama payload extraction
# ---------------------------------------------------------------------------

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


def _record_unknown(db: Session, phrase: str, observed_at: datetime, run_id: uuid.UUID) -> None:
    """Count publish-time phrases that the parser cannot understand yet."""
    statement = insert(UnknownTimePhrase).values(
        phrase=phrase, occurrence_count=1, first_seen_at=observed_at, last_seen_at=observed_at,
        first_fetch_run_id=run_id, last_fetch_run_id=run_id,
    ).on_conflict_do_update(index_elements=[UnknownTimePhrase.phrase], set_={
        "occurrence_count": UnknownTimePhrase.occurrence_count + 1,
        "last_seen_at": observed_at, "last_fetch_run_id": run_id,
    })
    db.execute(statement)


# ---------------------------------------------------------------------------
# Main ingestion workflow
# ---------------------------------------------------------------------------

def ingest_payload(db: Session, payload: dict[str, Any], run_id: uuid.UUID, observed_at: datetime | None = None) -> str | None:
    """Store current state, immutable history, price changes, media, and metadata."""
    observed_at = observed_at or datetime.now(timezone.utc)
    row = extract_ad(payload, observed_at)
    if row is None:
        return None
    if db.scalar(select(AdObservation.id).where(AdObservation.fetch_run_id == run_id, AdObservation.ad_code == row["code"]).limit(1)):
        return None

    raw_hash, semantic_hash = payload_hashes(payload)
    row["publish_at"] = parse_publish_time(
        row["publish_phrase"], observed_at, lambda phrase: _record_unknown(db, phrase, observed_at, run_id)
    )

    # ads is the latest snapshot. History tables below keep the full timeline.
    ad = db.get(Ad, row["code"])
    status = "created" if ad is None else "updated"
    if ad is None:
        ad = Ad(first_seen_at=observed_at, **row)
        db.add(ad)
    else:
        for key, value in row.items():
            if key != "code":
                setattr(ad, key, value)
    db.flush()

    version = db.scalar(select(AdVersion).where(AdVersion.ad_code == ad.code, AdVersion.semantic_hash == semantic_hash))
    if version is None:
        # Versions are immutable and reused when semantic content repeats/reverts.
        version = AdVersion(
            ad_code=ad.code,
            semantic_hash=semantic_hash,
            raw_hash=raw_hash,
            payload=payload,
            origin="live_fetch",
            first_observed_at=observed_at,
        )
        db.add(version)
        db.flush()

    previous = db.scalar(
        select(AdObservation)
        .where(AdObservation.ad_code == ad.code)
        .order_by(AdObservation.observed_at.desc(), AdObservation.id.desc())
        .limit(1)
    )
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    observation = AdObservation(
        ad_code=ad.code,
        fetch_run_id=run_id,
        version_id=version.id,
        observed_at=observed_at,
        raw_hash=raw_hash,
        publish_phrase=str(detail.get("time") or ""),
        rank=str(detail.get("rank") or ""),
    )
    db.add(observation)
    db.flush()
    _record_change_events(db, ad.code, observation, previous, version, observed_at)

    # Price history is change-only, so repeated identical prices do not add rows.
    price_state = {key: row[key] for key in ("current_price", "current_payment", "current_prepayment", "current_installments", "price_type")}
    price_hash = fingerprint(price_state)
    latest_hash = db.scalar(select(PriceObservation.fingerprint).where(PriceObservation.ad_code == ad.code)
                            .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc()).limit(1))
    if latest_hash != price_hash:
        db.add(PriceObservation(ad_code=ad.code, fetch_run_id=run_id, observed_at=observed_at,
            price=row["current_price"], payment=row["current_payment"], prepayment=row["current_prepayment"],
            installments=row["current_installments"], price_type=row["price_type"], fingerprint=price_hash))
        status += ":price"

    # Media and metadata are current-state mirrors of the latest raw payload.
    db.execute(delete(AdMedia).where(AdMedia.ad_code == ad.code))
    for position, image in enumerate(payload.get("images") or []):
        if isinstance(image, dict):
            url = image.get("large") or image.get("small") or image.get("thumb")
            if url:
                db.add(AdMedia(ad_code=ad.code, media_type="image", position=position, url=url, variants=image))
    for position, video in enumerate(payload.get("videos") or []):
        if isinstance(video, dict) and (url := video.get("url")):
            db.add(AdMedia(ad_code=ad.code, media_type="video", position=position, url=url, variants=video))
    metadata = payload.get("metadata") or {}
    meta = db.get(AdMetadata, ad.code)
    values = {"canonical_url": metadata.get("canonical"), "title_tag": metadata.get("title_tag"),
              "description": metadata.get("description"), "keywords": metadata.get("keywords"), "raw_metadata": metadata}
    if meta is None:
        db.add(AdMetadata(ad_code=ad.code, **values))
    else:
        for key, value in values.items():
            setattr(meta, key, value)
    return status


def _record_change_events(
    db: Session,
    code: str,
    observation: AdObservation,
    previous: AdObservation | None,
    version: AdVersion,
    observed_at: datetime,
) -> None:
    """Create events for semantic content changes and long disappear/reappear gaps."""
    if previous is None:
        return

    if previous.version_id != version.id:
        before = semantic_payload(previous.version.payload)
        after = semantic_payload(version.payload)
        changes = diff_payloads(before, after)
        paths = [item["path"] for item in changes]
        db.add(AdChangeEvent(
            ad_code=code,
            observation_id=observation.id,
            previous_version_id=previous.version_id,
            new_version_id=version.id,
            event_type="content_changed",
            categories=categories_for(paths),
            changed_paths=paths,
            changes=changes,
            origin="live_fetch",
            created_at=observed_at,
        ))

    if (observed_at - previous.observed_at).total_seconds() >= REAPPEAR_AFTER_SECONDS:
        db.add(AdChangeEvent(
            ad_code=code,
            observation_id=observation.id,
            previous_version_id=previous.version_id,
            new_version_id=version.id,
            event_type="reappeared",
            categories=[],
            changed_paths=[],
            changes=[],
            origin="live_fetch",
            created_at=observed_at,
        ))
