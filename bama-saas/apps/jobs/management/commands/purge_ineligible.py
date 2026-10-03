"""Delete ads that never had a car's cash price. Dry run unless ``--apply``.

An ad exists in this catalog only with a cash price and a photo. New ads are
refused at ingest (``cash_price_required``, ``photo_missing``); this removes the
ones stored before that rule, with their versions, sightings and prices.

Deleted:

* the stored price is not one car's cash price — ``price_basis_unclear``, a
  non-``lumpsum`` price type, or nothing above the unit-switch sentinel;
* an ACTIVE ad asking under ``PURGE_FLOOR`` of its (model, year) median, with at
  least ``MIN_PEERS`` clean peers. Measured 2026-09-29 on a labelled sample:
  below 0.2× nearly every row was a deposit, a placeholder or a missing zero;
  between 0.3× and 0.5× most were real damaged, old or free-zone cars.

Kept: a removed ad whose photo has since disappeared. Bama deletes photos on
takedown; the ad had one while it was listed, and its price history is true.

Irreversible. Read the dry-run report first.
"""

from __future__ import annotations

import json
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import Count, Q

from apps.common.verify import MIN_PLAUSIBLE_PRICE
from apps.core.models import Ad, AdObservation, AdVersion, PriceObservation
from apps.core.pricing import MIN_PEERS

PURGE_FLOOR = 0.2
BATCH = 1000

_FLOOR_SQL = """
WITH clean AS (
    SELECT model_id, year_jalali,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY current_price) AS median,
           count(*) AS peers
    FROM catalog_ad
    WHERE status = 'active' AND NOT price_basis_unclear AND price_type = 'lumpsum'
      AND current_price > %(sentinel)s AND model_id IS NOT NULL AND year_jalali IS NOT NULL
    GROUP BY 1, 2
)
SELECT a.code FROM catalog_ad a JOIN clean c USING (model_id, year_jalali)
WHERE a.status = 'active' AND c.peers >= %(min_peers)s
  AND a.current_price < %(floor)s * c.median
"""


class Command(BaseCommand):
    help = "Delete ads without a cash price (dry run unless --apply)"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        not_cash = (Q(price_basis_unclear=True) | ~Q(price_type="lumpsum") |
                    Q(current_price__isnull=True) |
                    Q(current_price__lte=MIN_PLAUSIBLE_PRICE))
        with connection.cursor() as cursor:
            cursor.execute(_FLOOR_SQL, {"sentinel": MIN_PLAUSIBLE_PRICE,
                                        "min_peers": MIN_PEERS, "floor": PURGE_FLOOR})
            under_floor = {code for (code,) in cursor.fetchall()}
        doomed = Ad.objects.filter(not_cash | Q(code__in=under_floor))
        codes = list(doomed.values_list("code", flat=True))
        reasons = Counter()
        for ad in doomed.values("code", "price_basis_unclear", "price_type"):
            reasons["under_cohort_floor" if ad["code"] in under_floor and not (
                ad["price_basis_unclear"] or ad["price_type"] != "lumpsum")
                    else "not_cash"] += 1
        report = {
            "ads": len(codes),
            "by_status": dict(doomed.values_list("status").annotate(n=Count("code"))),
            "by_reason": dict(reasons),
            "top_brands": list(doomed.values("brand__name_fa").annotate(n=Count("code"))
                               .order_by("-n")[:15]),
            "cascade": {
                "versions": AdVersion.objects.filter(ad__in=doomed).count(),
                "observations": AdObservation.objects.filter(ad__in=doomed).count(),
                "price_observations": PriceObservation.objects.filter(ad__in=doomed).count(),
            },
            "examples_under_floor": sorted(under_floor)[:20],
            "mode": "apply" if options["apply"] else "dry_run",
        }
        if options["apply"]:
            deleted = 0
            for start in range(0, len(codes), BATCH):
                with transaction.atomic():
                    deleted += Ad.objects.filter(code__in=codes[start:start + BATCH]).delete()[0]
            report["rows_deleted_including_cascade"] = deleted
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=1, default=str))
