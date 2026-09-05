"""Rebuild the ad-search trigram index on `UPPER(search_text)`.

The index existed on the bare column, and the only query that could ever have
used it is `filters.filter_q`, which uses `__icontains`. Django compiles that to

    UPPER(search_text) LIKE UPPER(%s)

and a `gin_trgm_ops` index on `search_text` cannot serve a call over the column.
So the index was unusable by construction rather than merely unused.

Measured on production 2026-09-04 (`pg_stat_user_indexes`, stats never reset —
`catalog_ad` shows 737M index scans, so a zero is real):

    ad_search_text_trgm   40 MB   0 scans

and `EXPLAIN` on the two spellings of the same search, against the live table:

    UPPER(search_text) LIKE UPPER('%پژو%')  ->  Seq Scan on catalog_ad
    search_text        LIKE       '%پژو%'   ->  Bitmap Index Scan on
                                                ad_search_text_trgm

Every text search over the 79,741-row / 568 MB table was therefore a sequential
scan, with a 40 MB index sitting beside it being maintained on every write and
scanned by every autovacuum.

Indexing the expression rather than changing the filter to `__contains` is
deliberate: `normalization.normalize_text` normalises digits, Arabic letters and
punctuation but does **not** fold case, so the stored document keeps «X55 PRO»
and «MVM» as written. A case-sensitive filter would silently stop matching a
lowercase query for the Latin half of the corpus. Folding case inside
`search_document` instead would work, but only after a backfill of all 79,741
rows — until then new rows would be lowercased and old ones not.

`AddIndexConcurrently` / `RemoveIndexConcurrently`, hence `atomic = False`:
`catalog_ad` takes writes on every crawl tick, and a plain `CREATE INDEX` holds
a SHARE lock that blocks them for the whole build. Same reason as migration 0031.

Being non-atomic, the drop lands before the build. If the build then fails the
table is left with no search index at all — which is the one case where that is
harmless, because the index being replaced could not serve a search either. A
failed `CREATE INDEX CONCURRENTLY` also leaves an INVALID index behind; drop it
(`DROP INDEX CONCURRENTLY ad_search_text_trgm`) and re-run, rather than
reindexing, since the definition is what changed.
"""

from django.contrib.postgres.indexes import GinIndex, OpClass
from django.contrib.postgres.operations import AddIndexConcurrently, RemoveIndexConcurrently
from django.db import migrations
from django.db.models.functions import Upper


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("core", "0031_drop_dead_gin_indexes"),
    ]

    operations = [
        RemoveIndexConcurrently(model_name="ad", name="ad_search_text_trgm"),
        AddIndexConcurrently(
            model_name="ad",
            index=GinIndex(
                OpClass(Upper("search_text"), name="gin_trgm_ops"),
                name="ad_search_text_trgm",
            ),
        ),
    ]
