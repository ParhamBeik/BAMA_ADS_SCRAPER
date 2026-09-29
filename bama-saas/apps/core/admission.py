"""Catalog admission from the current immutable version and local photo bytes."""

import hashlib
from pathlib import Path

from django.conf import settings

from apps.core.models import Ad, AdVersionPhoto


def verified_local_photo(photo: AdVersionPhoto) -> bool:
    """The referenced archive file exists on the VPS with its recorded bytes."""
    if photo.state != AdVersionPhoto.State.VERIFIED or not photo.asset_id:
        return False
    asset = photo.asset
    root = Path(settings.PHOTO_ARCHIVE_ROOT).resolve()
    path = (root / asset.relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return False
    if path.stat().st_size != asset.byte_size:
        return False
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest() == asset.sha256


def sync_admission(ad: Ad) -> str:
    if not ad.current_version_id:
        state = Ad.Admission.LEGACY
    elif (ad.price_type != "lumpsum" or (ad.current_price or 0) <= 0
          or ad.price_basis_unclear):
        state = Ad.Admission.HISTORY_ONLY
    else:
        photos = AdVersionPhoto.objects.filter(version_id=ad.current_version_id)
        if any(verified_local_photo(photo) for photo in
               photos.filter(state=AdVersionPhoto.State.VERIFIED,
                             asset__isnull=False).select_related("asset")):
            state = (Ad.Admission.READY if
                     ad.current_version.classification_state == "verified" else
                     Ad.Admission.PENDING)
        elif not photos.exists() or not photos.filter(
                state__in=[AdVersionPhoto.State.PENDING, AdVersionPhoto.State.BLOCKED,
                           AdVersionPhoto.State.VERIFIED]
        ).exists():
            state = Ad.Admission.REJECTED
        else:
            state = Ad.Admission.PENDING
    if ad.admission_state != state:
        Ad.objects.filter(pk=ad.pk).update(admission_state=state)
        ad.admission_state = state
    return state
