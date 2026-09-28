"""Verify historical versions whose exact source brand/model is already confirmed."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.common.parsing import extract_ad
from apps.core.admission import sync_admission
from apps.core.models import Ad, AdVersion, Brand, Model, Variant
from apps.core.normalization import search_document


class Command(BaseCommand):
    help = "Classify exact confirmed brand/model pairs; --apply is required to write"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=500000)

    def handle(self, *args, **options):
        brands = {brand.name_fa: brand for brand in Brand.objects.filter(is_confirmed=True)}
        models = {(model.brand_id, model.name_fa): model for model in
                  Model.objects.filter(is_confirmed=True)}
        rows = (AdVersion.objects.filter(classification_state="needs_review")
                .order_by("pk").select_related("ad"))
        counts = {"examined": 0, "verified": 0, "still_review": 0,
                  "current_ads_updated": 0, "affected_models": set()}
        for version in rows.iterator(chunk_size=200):
            if counts["examined"] >= options["limit"]:
                break
            extracted = extract_ad(version.payload or {}, version.first_observed_at)
            if extracted is None:
                continue
            counts["examined"] += 1
            source = (extracted.get("brand") or "").strip()
            model_name = (extracted.get("model") or "").strip()
            brand = brands.get(source)
            model = models.get((brand.pk, model_name)) if brand else None
            if model is None:
                counts["still_review"] += 1
                continue
            counts["verified"] += 1
            counts["affected_models"].add(model.pk)
            if not options["apply"]:
                continue
            trim = (extracted.get("trim") or "default").strip() or "default"
            with transaction.atomic():
                variant, _ = Variant.objects.get_or_create(model=model, name_fa=trim)
                AdVersion.objects.filter(pk=version.pk).update(
                    classified_model=model, classified_variant=variant,
                    classification_state="verified", classification_rule="confirmed_model",
                )
                ad = version.ad
                if ad.current_version_id == version.pk:
                    Ad.objects.filter(pk=ad.pk).update(
                        brand=brand, model=model, variant=variant,
                        search_text=search_document(
                            ad.title, model.name_fa, brand.name_fa, ad.description,
                        ),
                    )
                    ad.current_version = version
                    version.classification_state = "verified"
                    sync_admission(ad)
                    counts["current_ads_updated"] += 1
        counts["affected_models"] = sorted(counts["affected_models"])
        counts["mode"] = "apply" if options["apply"] else "dry_run"
        self.stdout.write(json.dumps(counts, ensure_ascii=False))
