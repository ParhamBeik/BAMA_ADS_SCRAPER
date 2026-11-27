"""Covering index for the turnover join.

`research._turnover_tally` joins every recent `ListingEpisode` to `catalog_ad`
and reads only `code`, `is_verified` and `model_id` from the ad. Without this
index Postgres reads the whole 405 MB heap for those three columns: 2.7 s a call,
16k calls and 2 TB read between 2026-09-25 and 2026-11-27 (`pg_stat_statements`).

`CONCURRENTLY` so the worker keeps writing ads while it builds (~10 MB, seconds).
That needs `atomic = False`. If a build fails it leaves an INVALID index; drop it
with `DROP INDEX CONCURRENTLY ad_verified_model_cov;` and re-run `migrate`.

Rollback: `manage.py migrate core 0039` (drops the index concurrently). Nothing
reads the index by name, so dropping it only restores the old plan.
"""

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("core", "0039_refresh_price_basis_unclear"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="ad",
            index=models.Index(
                fields=("code",),
                include=("model",),
                condition=models.Q(is_verified=True),
                name="ad_verified_model_cov",
            ),
        ),
    ]
