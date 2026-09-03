"""Drop three GIN indexes that never served a query.

Measured on production before removal (`pg_stat_user_indexes`, stats never
reset — the primary key shows 727M scans, so the zeroes are real):

    ad_raw_gin      131 MB   0 scans
    ad_quality_gin  1.76 MB  3 scans
    ad_cohort_gin   1.77 MB  0 scans

`ad_raw_gin` was 54% of this table's index weight and 5.3% of the whole
database. Nothing in the codebase has ever queried `raw_payload` with a jsonb
operator; the single reference is `__isnull=False`, which GIN cannot serve.

The two flag indexes could not have worked even in principle. Every consumer
asked the negated question — "not quarantined", "not an outlier" — and GIN
accelerates positive containment only. Migration 0030 replaced those reads with
the `is_verified` / `has_high_outlier` / `has_low_outlier` generated columns,
which is what actually made them index-backed, and left these three with no
caller at all.

They were not free while they sat there: 97% of writes to `catalog_ad` are
non-HOT (2.9% HOT across 1.08M updates), so every update maintained all three,
and every autovacuum had to scan them.

`RemoveIndexConcurrently` rather than `RemoveIndex`, hence `atomic = False`.
A plain DROP INDEX takes ACCESS EXCLUSIVE on the table and would queue behind —
and then block — the crawler, which writes continuously on a 15-minute tick.
The concurrent form takes only SHARE UPDATE EXCLUSIVE. Same reason migration
0030 is non-atomic.
"""

from django.contrib.postgres.operations import RemoveIndexConcurrently
from django.db import migrations


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("core", "0030_ad_has_high_outlier_ad_has_low_outlier_and_more"),
    ]

    operations = [
        RemoveIndexConcurrently(model_name="ad", name="ad_raw_gin"),
        RemoveIndexConcurrently(model_name="ad", name="ad_quality_gin"),
        RemoveIndexConcurrently(model_name="ad", name="ad_cohort_gin"),
    ]
