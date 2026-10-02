"""Durable photo admission. Network failures never masquerade as missing photos."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

import requests
from django.conf import settings
from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.common.parsing import HEADERS, is_cdn_url
from apps.core.admission import sync_admission
from apps.core.coverage import consecutive_blocks
from apps.core.images import ALLOWED_IMAGE_TYPES
from apps.core.models import AdVersionPhoto, ArchivedImage

RETRY_DELAYS = (timedelta(hours=8), timedelta(hours=24))


def _valid_image(content_type: str, body: bytes) -> bool:
    if content_type in ("image/jpeg", "image/jpg"):
        return body.startswith(b"\xff\xd8\xff") and body.endswith(b"\xff\xd9")
    if content_type == "image/png":
        return body.startswith(b"\x89PNG\r\n\x1a\n") and b"IEND" in body[-32:]
    if content_type == "image/webp":
        return body.startswith(b"RIFF") and body[8:12] == b"WEBP"
    if content_type == "image/avif":
        return len(body) > 16 and body[4:8] == b"ftyp" and b"avif" in body[8:32]
    return False


def _fetch(url: str) -> tuple[str, bytes] | int:
    """Return image bytes or HTTP status; 0 means transport/validation failure."""
    if not is_cdn_url(url):
        return 0
    try:
        with requests.get(url, headers=HEADERS, timeout=10, stream=True,
                          allow_redirects=False) as response:
            if not is_cdn_url(response.url):
                return 0
            if response.status_code != 200:
                return response.status_code
            kind = response.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
            if kind not in ALLOWED_IMAGE_TYPES:
                return 0
            data = bytearray()
            for chunk in response.iter_content(64 * 1024):
                data.extend(chunk)
                if len(data) > settings.IMAGE_MAX_BYTES:
                    return 0
            body = bytes(data)
            return (kind, body) if _valid_image(kind, body) else 0
    except requests.RequestException:
        return 0


def _archive_bytes(kind: str, body: bytes) -> ArchivedImage | None:
    digest = hashlib.sha256(body).hexdigest()
    existing = ArchivedImage.objects.filter(pk=digest).first()
    if existing:
        path = Path(settings.PHOTO_ARCHIVE_ROOT) / existing.relative_path
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"archive copy missing or corrupt: {digest}")
        return existing
    root = Path(settings.PHOTO_ARCHIVE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    used = ArchivedImage.objects.aggregate(n=Sum("byte_size"))["n"] or 0
    if (used + len(body) > settings.PHOTO_ARCHIVE_CAP_BYTES or
            shutil.disk_usage(root).free - len(body) < settings.PHOTO_ARCHIVE_MIN_FREE_BYTES):
        return None
    relative = f"{digest[:2]}/{digest}"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            try:
                tmp.write(body)
                tmp.flush()
                os.fsync(tmp.fileno())
                os.replace(tmp.name, path)
            finally:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)
    asset, _ = ArchivedImage.objects.get_or_create(
        sha256=digest,
        defaults={"relative_path": relative, "content_type": kind, "byte_size": len(body)},
    )
    return asset


def _update_admission(photo: AdVersionPhoto) -> None:
    ad = photo.version.ad
    if ad.current_version_id != photo.version_id:
        return
    sync_admission(ad)


def archive_one(photo: AdVersionPhoto, *, now=None) -> str:
    """One bounded attempt; source outages and disk pressure keep the row pending."""
    now = now or timezone.now()
    if photo.state in (AdVersionPhoto.State.VERIFIED, AdVersionPhoto.State.FAILED):
        return photo.state
    if consecutive_blocks():
        if photo.pause_started_at is None:
            photo.pause_started_at = now
            photo.save(update_fields=["pause_started_at"])
        return "source_blocked"
    if photo.pause_started_at is None and photo.next_retry_at and now < photo.next_retry_at:
        return "not_due"
    previous = (AdVersionPhoto.objects.filter(source_url=photo.source_url,
                                               state=AdVersionPhoto.State.VERIFIED)
                .exclude(pk=photo.pk).select_related("asset").first())
    if previous and previous.asset_id:
        copied_path = Path(settings.PHOTO_ARCHIVE_ROOT) / previous.asset.relative_path
        if (not copied_path.is_file() or
                hashlib.sha256(copied_path.read_bytes()).hexdigest() != previous.asset_id):
            raise RuntimeError(f"archive copy missing or corrupt: {previous.asset_id}")
        photo.asset = previous.asset
        photo.state = AdVersionPhoto.State.VERIFIED
        photo.save(update_fields=["asset", "state"])
        _update_admission(photo)
        return "verified"
    result = _fetch(photo.source_url)
    if result in (429, 503):
        photo.state = AdVersionPhoto.State.BLOCKED
        photo.last_http_status = result
        photo.next_retry_at = now + timedelta(hours=1)
        photo.pause_started_at = photo.pause_started_at or now
        photo.save(update_fields=["state", "last_http_status", "next_retry_at",
                                  "pause_started_at"])
        return "source_blocked"
    if photo.pause_started_at is not None:
        if photo.first_attempt_at is not None:
            photo.first_attempt_at += now - photo.pause_started_at
        photo.pause_started_at = None
        if photo.attempts:
            photo.next_retry_at = photo.first_attempt_at + RETRY_DELAYS[photo.attempts - 1]
        else:
            photo.next_retry_at = None
        photo.save(update_fields=["first_attempt_at", "pause_started_at", "next_retry_at"])
        if photo.next_retry_at and now < photo.next_retry_at:
            return "not_due"
    if isinstance(result, tuple):
        asset = _archive_bytes(*result)
        if asset is None:
            return "capacity_paused"
        photo.asset = asset
        photo.state = AdVersionPhoto.State.VERIFIED
        photo.last_attempt_at = now
        photo.save(update_fields=["asset", "state", "last_attempt_at",
                                  "first_attempt_at", "pause_started_at"])
        _update_admission(photo)
        return "verified"
    photo.attempts += 1
    photo.first_attempt_at = photo.first_attempt_at or now
    photo.last_attempt_at = now
    photo.last_http_status = result or None
    if photo.attempts >= 3:
        photo.state = AdVersionPhoto.State.FAILED
        photo.next_retry_at = None
    else:
        photo.state = AdVersionPhoto.State.PENDING
        photo.next_retry_at = photo.first_attempt_at + RETRY_DELAYS[photo.attempts - 1]
    photo.save(update_fields=["attempts", "first_attempt_at", "last_attempt_at",
                              "last_http_status", "state", "next_retry_at",
                              "pause_started_at"])
    _update_admission(photo)
    return photo.state


def archive_pending(*, limit: int = 50) -> dict:
    """Worker job; each version remains independently inspectable."""
    now = timezone.now()
    due = (AdVersionPhoto.objects.filter(state__in=["pending", "blocked"])
           .filter(Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))
           .select_related("version__ad"))
    # Storage policy: only the current version's front photo is archived.
    current = list(due.filter(position=0, version_id=F("version__ad__current_version_id"))
                   .order_by("-pk")[:limit])
    counts: dict[str, int] = {}
    for photo in current:
        outcome = archive_one(photo, now=now)
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome == "capacity_paused":
            break
    return counts
