"""Recompute Ad.first_seen_at / last_seen_at from the observation log.

``ingest_ad`` used to write ``last_seen_at = observed_at`` on every update and
set ``first_seen_at`` only at row creation. ``observed_at`` is not monotonic
across calls — ``import_history`` replays old observations and
``backfill``/``crawl_gaps`` refetch pages out of order — so both columns drifted
away from reality: 5,009 ads ended up with ``last_seen_at < first_seen_at``,
making every time-on-feed duration negative (``fast_movers`` was reporting
-7 days).

``AdObservation`` is append-only and is the actual record of when each ad was
seen, so the true bounds are just its min/max per ad. One UPDATE ... FROM does
the whole table server-side.

``removed_at`` is realigned in the same pass: ``mark_inactive_ads`` stamps it
with the ad's own ``last_seen_at``, so a corrected ``last_seen_at`` leaves the
old value inconsistent.

Idempotent, and the forward fix in ``ingest_ad`` keeps it from recurring.
"""

from django.db import migrations

REPAIR = """
WITH bounds AS (
    SELECT ad_id,
           MIN(observed_at) AS first_obs,
           MAX(observed_at) AS last_obs
    FROM history_adobservation
    GROUP BY ad_id
)
UPDATE catalog_ad a
SET first_seen_at = b.first_obs,
    last_seen_at  = b.last_obs,
    removed_at    = CASE WHEN a.status = 'removed' THEN b.last_obs ELSE NULL END
FROM bounds b
WHERE a.code = b.ad_id
  AND (a.first_seen_at IS DISTINCT FROM b.first_obs
       OR a.last_seen_at IS DISTINCT FROM b.last_obs);
"""


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_delete_analyticscache"),
    ]

    operations = [
        migrations.RunSQL(REPAIR, reverse_sql=migrations.RunSQL.noop),
    ]
