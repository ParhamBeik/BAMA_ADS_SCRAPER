"""Mark brands and models as confirmed-or-not so ingestion stops silently
growing the catalog.

Brand and model names are parsed out of free-text ad titles, so any change to
Bama's title format invents dimension rows without complaint, and every cohort
keyed on them is then wrong. New rows default to unconfirmed from here on.

Everything already in the table is confirmed by this migration: it is the catalog
as it stands after ~60k ingested ads, i.e. the known-good baseline the flag exists
to protect. Without this backfill the flag would read "the entire catalog is
unproven", which is both useless and untrue.
"""

from django.db import migrations, models


def confirm_existing(apps, schema_editor):
    apps.get_model("core", "Brand").objects.update(is_confirmed=True)
    apps.get_model("core", "Model").objects.update(is_confirmed=True)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_repair_lifecycle_dates'),
    ]

    operations = [
        migrations.AddField(
            model_name='brand',
            name='is_confirmed',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='model',
            name='is_confirmed',
            field=models.BooleanField(db_index=True, default=False),
        ),
        # Reverse is a no-op: dropping the columns discards the values anyway.
        migrations.RunPython(confirm_existing, migrations.RunPython.noop),
    ]
