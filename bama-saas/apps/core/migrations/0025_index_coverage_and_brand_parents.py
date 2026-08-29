"""Two unrelated schema needs plus the brand-taxonomy repair, in one migration.

Together because they land in the same deploy and the brand rewrite invalidates
every derived analytic keyed on a model id — which the next pipeline tick
rebuilds anyway, so splitting them would only widen the window where the two
disagree.
"""

from django.db import migrations, models
from django.utils.text import slugify

from apps.jobs.ingest import BRAND_PARENT


def _merge_variants(Variant, Ad, old_model, new_model):
    """Move one model's trims onto another, folding same-named ones together."""
    for variant in Variant.objects.filter(model=old_model):
        twin = Variant.objects.filter(model=new_model, name_fa=variant.name_fa).first()
        if twin is None:
            variant.model = new_model
            variant.save(update_fields=["model"])
        else:
            Ad.objects.filter(variant=variant).update(variant=twin)
            variant.delete()


def to_parent_makes(apps, schema_editor):
    """File model-named brand rows under the manufacturer that builds them.

    Bama's `brand_fa` is flat, so "دنا" and "پراید" arrived as top-level brands
    and one maker's inventory ended up split across several unrelated rows.
    `ingest.BRAND_PARENT` fixes new ads; this fixes the ones already stored.

    Written defensively because it moves live rows: a name absent from the
    catalogue is skipped, a model that already exists under the parent is merged
    rather than duplicated, and the old rows are deleted only once nothing
    points at them.
    """
    Brand = apps.get_model("core", "Brand")
    Model = apps.get_model("core", "Model")
    Variant = apps.get_model("core", "Variant")
    Ad = apps.get_model("core", "Ad")

    for child_name, parent_name in BRAND_PARENT.items():
        child = Brand.objects.filter(name_fa=child_name).first()
        if child is None:
            continue
        parent = Brand.objects.filter(name_fa=parent_name).first()
        if parent is None:
            parent = Brand.objects.create(
                slug=slugify(parent_name, allow_unicode=True) or parent_name,
                name_fa=parent_name,
                # A manufacturer this migration names explicitly is not a row
                # ingestion guessed at, so it does not need a human glance.
                is_confirmed=True,
            )
        if parent.pk == child.pk:
            continue

        for model in Model.objects.filter(brand=child):
            twin = Model.objects.filter(brand=parent, name_fa=model.name_fa).first()
            if twin is None:
                model.brand = parent
                model.save(update_fields=["brand"])
            else:
                _merge_variants(Variant, Ad, model, twin)
                Ad.objects.filter(model=model).update(model=twin)
                model.delete()

        Ad.objects.filter(brand=child).update(brand=parent)
        child.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0024_ad_price_basis_unclear'),
    ]

    operations = [
        migrations.AddField(
            model_name='marketindex',
            name='gap_days',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='marketindex',
            name='low_coverage',
            field=models.BooleanField(default=False),
        ),
        # Irreversible on purpose. The rewrite folds rows together, so there is
        # no record of which of the merged models an ad came from — a "reverse"
        # would have to invent one. Undoing it means restoring from a dump.
        migrations.RunPython(to_parent_makes, migrations.RunPython.noop),
    ]
