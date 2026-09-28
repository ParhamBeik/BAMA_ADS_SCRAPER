"""Acknowledge photo bytes verified on the owner's Mac."""

from __future__ import annotations

import re

from django.db import transaction
from django.utils import timezone

from apps.core.admission import sync_admission
from apps.core.models import Ad, ArchivedImage

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def confirm_manifest(lines) -> dict:
    """The Mac has read and hashed every listed file before sending this manifest."""
    entries: dict[str, int] = {}
    for number, line in enumerate(lines, 1):
        parts = line.strip().split()
        if len(parts) != 2 or not _SHA256.fullmatch(parts[0]):
            raise ValueError(f"invalid manifest line {number}")
        try:
            size = int(parts[1])
        except ValueError as exc:
            raise ValueError(f"invalid size on line {number}") from exc
        if size <= 0 or (parts[0] in entries and entries[parts[0]] != size):
            raise ValueError(f"invalid or conflicting size on line {number}")
        entries[parts[0]] = size
        if len(entries) > 500_000:
            raise ValueError("manifest exceeds 500000 photos")

    known: list[str] = []
    for start in range(0, len(entries), 1000):
        batch = list(entries)[start:start + 1000]
        for asset in ArchivedImage.objects.filter(sha256__in=batch).only("sha256", "byte_size"):
            if asset.byte_size != entries[asset.sha256]:
                raise ValueError(f"size mismatch for {asset.sha256}")
            known.append(asset.sha256)

    with transaction.atomic():
        at = timezone.now()
        affected_ids: set[str] = set()
        for start in range(0, len(known), 1000):
            batch = known[start:start + 1000]
            ArchivedImage.objects.filter(sha256__in=batch).update(
                backed_up_at=at,
            )
            affected_ids.update(Ad.objects.filter(
                current_version__photos__asset_id__in=batch,
            ).exclude(admission_state=Ad.Admission.READY).values_list("pk", flat=True))
        affected = (Ad.objects.filter(pk__in=affected_ids)
                    .select_related("current_version"))
        newly_ready = sum(sync_admission(ad) == Ad.Admission.READY for ad in affected)
    return {"verified_on_mac": len(known), "unknown_older_files": len(entries) - len(known),
            "newly_ready": newly_ready}
