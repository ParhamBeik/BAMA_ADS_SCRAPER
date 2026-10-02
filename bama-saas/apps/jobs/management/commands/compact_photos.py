"""Bring stored photos in line with docs/STORAGE-POLICY.md.

Keeps exactly one photo row per ad: position 0 of its current version. Gallery
rows and old-version rows are links that the Ad row already carries, so they are
deleted, then any archived file no row uses any more. Finally every current
version without a front-photo row gets one, so the archiver can fetch it.

    manage.py compact_photos --dry-run
    manage.py compact_photos
"""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import F, Q

from apps.common.parsing import front_photo, is_cdn_url
from apps.core.models import Ad, AdVersionPhoto, ArchivedImage
from apps.jobs.jobs import _batched_delete

_BATCH = 2000


class Command(BaseCommand):
    help = "Keep one front photo per ad; delete gallery/old-version photo rows and orphan files."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run: bool = False, **options):
        extra = AdVersionPhoto.objects.filter(
            Q(position__gt=0) | ~Q(version_id=F("version__ad__current_version_id"))
        )
        orphans = ArchivedImage.objects.filter(photo_uses__isnull=True)
        missing = (Ad.objects.exclude(current_version=None)
                   .exclude(current_version__photos__position=0))
        report = {"dry_run": dry_run, "extra_photo_rows": extra.count()}
        if dry_run:
            kept = AdVersionPhoto.objects.filter(
                position=0, version_id=F("version__ad__current_version_id"))
            doomed = ArchivedImage.objects.exclude(photo_uses__in=kept)
            report["orphan_files"] = doomed.count()
            report["front_rows_to_create"] = missing.count()
            self.stdout.write(json.dumps(report))
            return

        report["deleted_photo_rows"] = _batched_delete(extra)
        root = Path(settings.PHOTO_ARCHIVE_ROOT)
        files = 0
        for asset in orphans.iterator():
            (root / asset.relative_path).unlink(missing_ok=True)
            files += 1
        report["deleted_files"] = files
        _batched_delete(ArchivedImage.objects.filter(photo_uses__isnull=True))

        created = 0
        rows = missing.values_list("current_version_id", "primary_image_url", "image_urls")
        batch: list[AdVersionPhoto] = []
        for version_id, primary, gallery in rows.iterator(chunk_size=_BATCH):
            front = front_photo(primary, gallery or [])
            if front and is_cdn_url(front):
                batch.append(AdVersionPhoto(version_id=version_id, position=0, source_url=front))
            if len(batch) >= _BATCH:
                created += len(AdVersionPhoto.objects.bulk_create(batch, ignore_conflicts=True))
                batch = []
        created += len(AdVersionPhoto.objects.bulk_create(batch, ignore_conflicts=True))
        report["front_rows_created"] = created
        self.stdout.write(json.dumps(report))
