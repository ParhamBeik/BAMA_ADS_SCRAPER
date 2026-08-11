"""Backfill description / image fields from raw_payload."""

from django.core.management.base import BaseCommand

from apps.core.models import Ad
from apps.jobs.services.ingest import _presentation_fields


class Command(BaseCommand):
    help = "Promote description and image URLs from raw_payload onto Ad rows."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--batch-size", type=int, default=500)

    def handle(self, *args, **options):
        qs = Ad.objects.exclude(raw_payload=None).order_by("code")
        limit = options["limit"]
        if limit:
            qs = qs[:limit]
        updated = 0
        batch = []
        for ad in qs.iterator(chunk_size=options["batch_size"]):
            detail = (ad.raw_payload or {}).get("detail") or {}
            fields = _presentation_fields(detail)
            changed = False
            for k, v in fields.items():
                if getattr(ad, k, None) != v:
                    setattr(ad, k, v)
                    changed = True
            if changed:
                batch.append(ad)
            if len(batch) >= options["batch_size"]:
                Ad.objects.bulk_update(
                    batch,
                    ["description", "primary_image_url", "image_urls",
                     "image_count", "description_length", "seller_authenticated",
                     "source_modified_at"],
                )
                updated += len(batch)
                batch = []
        if batch:
            Ad.objects.bulk_update(
                batch,
                ["description", "primary_image_url", "image_urls",
                 "image_count", "description_length", "seller_authenticated",
                 "source_modified_at"],
            )
            updated += len(batch)
        self.stdout.write(self.style.SUCCESS(f"Updated {updated} ads"))
