"""Persist the instalment/deposit verdict that used to be a regex on the read path.

The column is written at ingest from `quality.price_basis_unclear`. Every row
already in the table predates that, so the backfill below runs the *same*
predicate once, in SQL — the regex was deliberately written to behave
identically in Python's `re` and Postgres' POSIX engine, which is what makes
these two writers agree.
"""

from django.db import migrations, models

from apps.core.quality import FINANCE

BACKFILL = f"""
UPDATE catalog_ad
   SET price_basis_unclear = TRUE
 WHERE price_type = 'installment'
    OR current_prepayment > 0
    OR title ~ '{FINANCE}'
    OR description ~ '{FINANCE}'
"""


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0023_ad_district'),
    ]

    operations = [
        migrations.AddField(
            model_name='ad',
            name='price_basis_unclear',
            field=models.BooleanField(db_index=True, default=False),
        ),
        # Reverse is a no-op rather than an UPDATE: dropping the column is what
        # undoes this, and the field removal above already does that.
        migrations.RunSQL(BACKFILL, migrations.RunSQL.noop),
    ]
