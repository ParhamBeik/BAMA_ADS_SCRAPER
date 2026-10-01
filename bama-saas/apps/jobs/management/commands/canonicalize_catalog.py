"""Re-file every stored ad under the reviewed taxonomy (``apps.core.taxonomy``).

Dry run unless ``--apply``: the dry run performs the whole rewrite inside one
transaction and rolls it back, so its report is exactly what ``--apply`` would
do. Row locks are held for the duration — stop ``bama-worker`` first.

History is re-keyed, never dropped: ``DailyInventorySnapshot`` rows follow their
variant to its new home. Variants with no ads left (history from before the
09-28 family repair) are matched by name to exactly one reviewed pair; a
variant matching zero or several pairs keeps its rows and is reported.
Models and variants that end with no references are deleted.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.db.models.fields.json import KT

from apps.core import taxonomy
from apps.core.models import Ad, AdVersion, Brand, DailyInventorySnapshot, Model, Variant
from apps.core.normalization import MANUFACTURER, ad_search_document
from apps.jobs.ingest import reset_cache, resolve_dimensions


class _Rollback(Exception):
    pass


class Command(BaseCommand):
    help = "Re-file ads, snapshots and catalog rows under the reviewed taxonomy"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        reset_cache()
        report: dict = {}
        try:
            with transaction.atomic():
                report = self._run()
                if not options["apply"]:
                    raise _Rollback
        except _Rollback:
            pass
        reset_cache()  # dry-run rows were rolled back; do not keep their ids
        report["mode"] = "apply" if options["apply"] else "dry_run (rolled back)"
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=1, default=str))

    def _run(self) -> dict:
        before = {"brands": Brand.objects.count(), "models": Model.objects.count(),
                  "variants": Variant.objects.count()}
        variant_moves: dict[int, Counter] = defaultdict(Counter)
        # (badge, Bama label, trim) → where today's ads with that identity went;
        # what history orphaned before 09-28 is matched against.
        by_identity: dict[tuple[str, str, str], Counter] = defaultdict(Counter)
        unmapped = Counter()
        reviewed_models: set[int] = set()
        changed = 0
        rows = (Ad.objects.annotate(brand_fa=KT("raw_payload__detail__brand_fa"),
                                    brand_en=KT("raw_payload__detail__brand"),
                                    trim_raw=KT("raw_payload__detail__trim"),
                                    gearbox=KT("raw_payload__detail__transmission"))
                .only("code", "title", "description", "brand_id", "model_id", "variant_id",
                      "current_version_id", "search_text"))
        batch: list[Ad] = []
        for ad in rows.iterator(chunk_size=2000):
            parts = [p.strip() for p in (ad.title or "").split("،", 1)]
            brand_name = ad.brand_fa or parts[0]
            model_name = parts[1] if len(parts) > 1 else None
            dims = resolve_dimensions(brand_name=brand_name, model_name=model_name,
                                      trim_name=ad.trim_raw, city_location=None,
                                      brand_en=ad.brand_en, transmission=ad.gearbox)
            if dims["classification_state"] == "verified":
                reviewed_models.add(dims["model"].pk)
            else:
                unmapped[f"{brand_name} / {model_name}"] += 1
            if ad.variant_id:
                variant_moves[ad.variant_id][dims["variant"].pk] += 1
            by_identity[(brand_name or "", model_name or "", (ad.trim_raw or "").strip())][
                dims["variant"].pk] += 1
            new = (dims["brand"].pk, dims["model"].pk, dims["variant"].pk)
            if new != (ad.brand_id, ad.model_id, ad.variant_id):
                changed += 1
            ad.brand_id, ad.model_id, ad.variant_id = new
            ad.search_text = ad_search_document(ad.title, dims["model"].name_fa,
                                                dims["brand"].name_fa, ad.description)
            batch.append(ad)
            if ad.current_version_id:
                AdVersion.objects.filter(pk=ad.current_version_id).update(
                    classified_model_id=new[1], classified_variant_id=new[2],
                    classification_state=dims["classification_state"],
                    classification_rule=dims["classification_rule"],
                )
            if len(batch) >= 1000:
                Ad.objects.bulk_update(batch, ["brand", "model", "variant", "search_text"])
                batch = []
        Ad.objects.bulk_update(batch, ["brand", "model", "variant", "search_text"])

        # An old variant whose ads now span several new ones (one trim name sold
        # with two gearboxes) hands its history to where most of its ads went.
        variant_map = {old: moves.most_common(1)[0][0] for old, moves in variant_moves.items()}
        split_variants = sorted(old for old, moves in variant_moves.items() if len(moves) > 1)
        inferred, unresolved = self._infer_orphans(variant_map, by_identity)
        variant_map.update(inferred)
        snapshots = self._rekey_snapshots(variant_map)

        # AdVersion.classified_* on history is diagnostic; clear it where it
        # pins a row that no ad and no snapshot uses any more.
        # A user's watch rule CASCADEs from its variant/model; never delete one.
        watched = [apps.get_model("accounts", name) for name in ("Watchlist", "AlertRule")]
        live_variant = Q(Exists(Ad.objects.filter(variant=OuterRef("pk")))) | Q(
            Exists(DailyInventorySnapshot.objects.filter(variant=OuterRef("pk"))))
        for rule in watched:
            live_variant |= Q(Exists(rule.objects.filter(variant=OuterRef("pk"))))
        dead_variants = Variant.objects.exclude(live_variant)
        AdVersion.objects.filter(classified_variant__in=dead_variants).update(
            classified_variant=None)
        deleted_variants = dead_variants.delete()[0]
        live_model = (Q(Exists(Ad.objects.filter(model=OuterRef("pk")))) |
                      Q(Exists(Variant.objects.filter(model=OuterRef("pk")))) |
                      Q(Exists(DailyInventorySnapshot.objects.filter(model=OuterRef("pk")))))
        for rule in watched:
            live_model |= Q(Exists(rule.objects.filter(model=OuterRef("pk"))))
        dead_models = Model.objects.exclude(live_model)
        AdVersion.objects.filter(classified_model__in=dead_models).update(classified_model=None)
        deleted_models = dead_models.delete()[0]
        deleted_brands = Brand.objects.filter(models__isnull=True, ads__isnull=True).delete()[0]

        Brand.objects.filter(name_fa__in=taxonomy.reviewed_brands()).update(is_confirmed=True)
        Model.objects.filter(pk__in=reviewed_models).update(is_confirmed=True)
        return {
            "before": before,
            "after": {"brands": Brand.objects.count(), "models": Model.objects.count(),
                      "variants": Variant.objects.count()},
            "ads_changed": changed,
            "unmapped_pairs": unmapped.most_common(50),
            "unmapped_ads": sum(unmapped.values()),
            "variants_split_across_new_variants": len(split_variants),
            "snapshots": snapshots,
            "orphan_variants_inferred": len(inferred),
            "orphan_variants_unresolved": unresolved[:50],
            "orphan_variants_unresolved_count": len(unresolved),
            "deleted": {"variants": deleted_variants, "models": deleted_models,
                        "brands": deleted_brands},
            "unconfirmed_models_with_ads": Model.objects.filter(
                is_confirmed=False, ads__isnull=False).distinct().count(),
        }

    def _infer_orphans(self, variant_map: dict[int, int],
                       by_identity: dict[tuple[str, str, str], Counter],
                       ) -> tuple[dict[int, int], list[str]]:
        """Map snapshot-only variants through today's ads with the same identity.

        Before 09-28 a row looked like brand سایپا / model 131 / variant SE; the
        09-28 repair made it پراید / پراید / "131 · SE". Either way the old names
        give a Bama label and trim; the old brand is the badge or its maker. If
        today's ads with that (badge, label, trim) all sit in one variant, the
        history goes there; otherwise it stays put and is reported.
        """
        orphans = (Variant.objects.filter(daily_snapshots__isnull=False)
                   .exclude(pk__in=variant_map).distinct().select_related("model__brand"))
        inferred, unresolved = {}, []
        for variant in orphans:
            old_brand, old_model = variant.model.brand.name_fa, variant.model.name_fa
            label, trim = old_model, variant.name_fa
            if " · " in trim:  # the 09-28 shape: family model, "label · trim" variant
                label, trim = trim.split(" · ", 1)
            trim = "" if trim == "default" else trim
            targets: Counter = Counter()
            for (brand, src_label, src_trim), moves in by_identity.items():
                if (src_label == label and src_trim == trim and
                        (brand in (old_brand, old_model) or
                         MANUFACTURER.get(brand) == old_brand)):
                    targets.update(moves)
            if len(targets) != 1:
                unresolved.append(f"{old_brand} / {old_model} / {variant.name_fa}"
                                  f" ({len(targets)} targets)")
                continue
            inferred[variant.pk] = next(iter(targets))
        return inferred, unresolved

    def _rekey_snapshots(self, variant_map: dict[int, int]) -> dict:
        """Move snapshot rows to their new variant; on a key collision keep the
        row with more ads (medians cannot be merged exactly)."""
        variant_map = {old: new for old, new in variant_map.items() if old != new}
        new_model = dict(Variant.objects.filter(pk__in=set(variant_map.values()))
                         .values_list("pk", "model_id"))
        rows = list(DailyInventorySnapshot.objects.filter(variant_id__in=variant_map)
                    .only("pk", "model_id", "variant_id", "year_jalali", "date", "ad_count"))
        untouched = set(DailyInventorySnapshot.objects.exclude(variant_id__in=variant_map)
                        .exclude(variant_id__isnull=True)
                        .values_list("variant_id", "year_jalali", "date"))
        winners: dict[tuple, DailyInventorySnapshot] = {}
        losers: list[int] = []
        for row in rows:
            variant = variant_map[row.variant_id]
            row.variant_id, row.model_id = variant, new_model[variant]
            key = (variant, row.year_jalali, row.date)
            if key in untouched:
                losers.append(row.pk)
                continue
            kept = winners.get(key)
            if kept is None or row.ad_count > kept.ad_count:
                if kept is not None:
                    losers.append(kept.pk)
                winners[key] = row
            else:
                losers.append(row.pk)
        DailyInventorySnapshot.objects.filter(pk__in=losers).delete()
        moved = list(winners.values())
        DailyInventorySnapshot.objects.bulk_update(moved, ["model", "variant"], batch_size=1000)
        return {"rekeyed": len(moved), "dropped_on_collision": len(losers)}
