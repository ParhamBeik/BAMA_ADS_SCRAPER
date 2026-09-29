"""Dry-run-first repair of version photo manifests and reviewed vehicle identity."""

from __future__ import annotations

import json
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from apps.common.parsing import extract_ad, image_urls, payload_hashes
from apps.core.models import Ad, AdVersion, AdVersionPhoto, SourceModelAlias
from apps.core.normalization import search_document
from apps.jobs.ingest import (
    BRAND_PARENT,
    PEUGEOT_FAMILIES,
    canonical_peugeot_model,
    resolve_dimensions,
)


class Command(BaseCommand):
    help = "Prepare version photos and canonical identities; --apply is required to write"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--pilot", action="store_true",
                            help="Only Peugeot 206/207 source versions")
        parser.add_argument("--known-mixed", action="store_true",
                            help="Only the known mixed Iranian source families")
        parser.add_argument("--limit", type=int, default=1000)

    def handle(self, *args, **options):
        if options["pilot"] and options["known_mixed"]:
            raise CommandError("choose --pilot or --known-mixed")
        mixed_families = {"سمند", "رانا", "شاهین", "سهند", "ساینا",
                          "تیبا", "پراید", "اطلس", "کوییک"}
        aliases = set(SourceModelAlias.objects.filter(reviewed=True)
                      .values_list("source_family", flat=True))
        rows = AdVersion.objects.order_by("ad_id", "first_observed_at")
        if options["pilot"]:
            peugeot_title = Q()
            for token in ("206", "207", "۲۰۶", "۲۰۷"):
                peugeot_title |= (Q(ad__title__icontains=token) |
                                  Q(ad__model__name_fa__icontains=token))
            rows = rows.filter(peugeot_title)
        elif options["known_mixed"]:
            rows = rows.filter(ad__raw_payload__detail__brand_fa__in=mixed_families)
        rows = (rows.select_related("ad") if options["apply"] else
                rows.only("id", "ad_id", "payload", "first_observed_at"))
        counts = {"examined": 0, "photo_rows": 0, "classified": 0,
                  "needs_review": 0, "current_versions_linked": 0,
                  "model_reassigned": 0, "affected_models": set()}
        families = Counter()
        for version in rows.iterator(chunk_size=200):
            if counts["examined"] >= options["limit"]:
                break
            payload = version.payload or {}
            extracted = extract_ad(payload, version.first_observed_at)
            if extracted is None:
                continue
            source = (extracted.get("brand") or "").strip()
            if options["pilot"] and not (
                source in PEUGEOT_FAMILIES or
                (source == "پژو" and canonical_peugeot_model(extracted.get("model")))
            ):
                continue
            if options["known_mixed"] and source not in mixed_families:
                continue
            counts["examined"] += 1
            families[source] += 1
            primary, gallery = image_urls(payload)
            counts["photo_rows"] += len(gallery)
            clear = bool(source in aliases or source in BRAND_PARENT or
                         source in PEUGEOT_FAMILIES or
                         (source == "پژو" and canonical_peugeot_model(extracted.get("model"))))
            counts["classified" if clear else "needs_review"] += 1
            if not options["apply"]:
                continue
            with transaction.atomic():
                AdVersionPhoto.objects.bulk_create(
                    [AdVersionPhoto(version=version, position=i, source_url=url)
                     for i, url in enumerate(gallery)], ignore_conflicts=True,
                )
                ad = version.ad
                is_current = (ad.current_version_id == version.pk or
                              (ad.current_version_id is None and ad.raw_payload and
                               payload_hashes(ad.raw_payload)[1] == version.semantic_hash))
                if not is_current and ad.current_version_id is None:
                    # Historical semantic hashes use earlier normalization rules.
                    # A matching raw hash, or the latest version carrying this
                    # ad's source identity, is the strongest remaining evidence.
                    source_version = ((version.payload or {}).get("detail") or {}).get(
                        "brand_fa", ""
                    ).strip()
                    current_source = ((ad.raw_payload or {}).get("detail") or {}).get(
                        "brand_fa", ""
                    ).strip()
                    if source_version and source_version == current_source:
                        raw_match = bool(ad.raw_payload and
                                         payload_hashes(ad.raw_payload)[0] == version.raw_hash)
                        later_same_source = False
                        if not raw_match:
                            for later in AdVersion.objects.filter(
                                ad_id=ad.pk, first_observed_at__gt=version.first_observed_at,
                            ).only("payload").iterator(chunk_size=100):
                                if (((later.payload or {}).get("detail") or {}).get(
                                        "brand_fa", "").strip() == current_source):
                                    later_same_source = True
                                    break
                        is_current = raw_match or not later_same_source
                if is_current:
                    counts["current_versions_linked"] += 1
                if not clear:
                    if is_current:
                        Ad.objects.filter(pk=ad.pk).update(
                            current_version=version, source_family=source,
                            primary_image_url=primary, image_urls=gallery,
                        )
                    continue
                dims = resolve_dimensions(
                    brand_name=source, model_name=extracted.get("model"),
                    trim_name=extracted.get("trim"), city_location=None,
                )
                AdVersion.objects.filter(pk=version.pk).update(
                    classified_model=dims["model"], classified_variant=dims["variant"],
                    classification_state="verified",
                    classification_rule=dims["classification_rule"],
                )
                counts["affected_models"].add(dims["model"].pk)
                if is_current:
                    if ad.model_id != dims["model"].pk:
                        counts["model_reassigned"] += 1
                    updates = {
                        "current_version": version, "source_family": source,
                        "brand": dims["brand"], "model": dims["model"],
                        "variant": dims["variant"],
                        "primary_image_url": primary, "image_urls": gallery,
                        "search_text": search_document(
                            ad.title, dims["model"].name_fa, dims["brand"].name_fa,
                            ad.description,
                        ),
                    }
                    if ad.current_version_id is None:
                        updates["admission_state"] = Ad.Admission.LEGACY
                    Ad.objects.filter(pk=ad.pk).update(**updates)
        counts["affected_models"] = sorted(counts["affected_models"])
        counts["source_families"] = families.most_common(30)
        counts["mode"] = "apply" if options["apply"] else "dry_run"
        counts["next"] = ("Run photo_archive and verify local bytes, then rebuild "
                          "snapshots, deal scores and model evaluation for affected models.")
        self.stdout.write(json.dumps(counts, ensure_ascii=False))
