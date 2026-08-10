"""Reconstruct historical ``DailyInventorySnapshot`` rows from provenance.

``daily_snapshot`` can only ever describe *today* — it reads ``Ad``, which is a
current-snapshot table. That leaves the market index with no history: it chains
day-over-day cohort medians, so on a fresh install it has nothing to chain and
the flagship chart is empty until enough days accumulate.

The append-only provenance tables can answer the historical question, though,
because they were built for exactly this:

* ``AdObservation`` records that a given ad was *in the feed* at a given time.
  That, not a wall-clock guess, is what makes an ad live on a past date — and it
  matters here, because ``Ad.removed_at`` is useless for backfill: the old
  14-day removal rule never fired, so every ad in the table currently looks like
  it is still listed.
* ``PriceObservation`` is change-only, so an ad's price on date D is simply its
  most recent observation at or before D. Walking the observations in time order
  and snapshotting at each date boundary reconstructs every day in one pass.

Liveness uses a window (``--liveness-days``, default 2) rather than "observed
exactly on D": the full sweep runs every 6 h and the 5-minute delta only reads
the top of the feed, so a deep-ranked ad is legitimately not seen every single
day. Days where crawl coverage was genuinely poor simply yield thinner cohorts,
which the index's ``MIN_COHORT_ADS`` guard then drops — the series degrades into
silence rather than into fiction.

Idempotent: rebuilds only the dates in range, replacing whatever was there.
Deliberately refuses to touch today's row, which ``daily_snapshot`` owns.

Usage:
    python manage.py backfill_snapshots [--days 60] [--liveness-days 2]
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.core.models import Ad, AdObservation, DailyInventorySnapshot, PriceObservation
from apps.core.services.quality import verified


class Command(BaseCommand):
    help = "Rebuild past DailyInventorySnapshot rows from observation history."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=60,
            help="How many days back to reconstruct (default: 60).",
        )
        parser.add_argument(
            "--liveness-days", type=int, default=2,
            help="An ad counts as listed on D if observed within this many days "
                 "up to and including D (default: 2).",
        )
        parser.add_argument(
            "--min-count", type=int, default=1,
            help="Skip cohorts with fewer than this many ads on a date.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        today = timezone.now().date()
        start = today - timedelta(days=options["days"])
        window = options["liveness_days"]
        min_count = options["min_count"]

        # Cohort key per ad. Only verified, publish-complete, cohort-keyable ads
        # — the same population daily_snapshot uses, minus the ACTIVE filter,
        # since a currently-removed ad was still live on a past date.
        cohort_of = {
            r["code"]: (r["model_id"], r["variant_id"], r["year_jalali"])
            for r in verified(Ad.objects)
            .filter(
                publish_at__isnull=False,
                model_id__isnull=False,
                year_jalali__isnull=False,
            )
            .values("code", "model_id", "variant_id", "year_jalali")
        }
        if not cohort_of:
            self.stdout.write(self.style.WARNING("No eligible ads; nothing to do."))
            return

        # date -> {ad codes seen that day}. One pass over provenance.
        seen_on: dict = defaultdict(set)
        for ad_id, observed_at in AdObservation.objects.filter(
            observed_at__date__gte=start - timedelta(days=window)
        ).values_list("ad_id", "observed_at"):
            if ad_id in cohort_of:
                seen_on[observed_at.date()].add(ad_id)

        # Chronological price walk: replay change-only observations so that
        # `price_at[code]` always holds the price as of the cursor date.
        price_events: dict = defaultdict(list)
        for ad_id, observed_at, price in PriceObservation.objects.filter(
            price__gt=0
        ).values_list("ad_id", "observed_at", "price"):
            if ad_id in cohort_of:
                price_events[observed_at.date()].append((ad_id, price))

        price_at: dict = {}
        # Prime with everything before the window opens, so an ad whose last
        # price change predates `start` still has a price on day one.
        for d in sorted(dd for dd in price_events if dd < start):
            for ad_id, price in price_events[d]:
                price_at[ad_id] = price

        DailyInventorySnapshot.objects.filter(
            date__gte=start, date__lt=today
        ).delete()

        written = 0
        dates_written = 0
        day = start
        while day < today:
            for ad_id, price in price_events.get(day, []):
                price_at[ad_id] = price

            live = set()
            for offset in range(window + 1):
                live |= seen_on.get(day - timedelta(days=offset), set())

            groups: dict = defaultdict(list)
            for ad_id in live:
                price = price_at.get(ad_id)
                if price:
                    groups[cohort_of[ad_id]].append(price)

            objs = [
                DailyInventorySnapshot(
                    model_id=model_id, variant_id=variant_id,
                    year_jalali=year_jalali, date=day,
                    ad_count=len(prices),
                    # new_count is not reconstructable per past day without
                    # re-deriving first-sighting per ad; the index does not read
                    # it, and inventing a number would be worse than a zero.
                    new_count=0,
                    median_price=int(statistics.median(prices)),
                    mean_price=int(statistics.mean(prices)),
                    min_price=min(prices), max_price=max(prices),
                )
                for (model_id, variant_id, year_jalali), prices in groups.items()
                if len(prices) >= min_count
            ]
            if objs:
                DailyInventorySnapshot.objects.bulk_create(objs, batch_size=1000)
                written += len(objs)
                dates_written += 1
            day += timedelta(days=1)

        self.stdout.write(self.style.SUCCESS(
            f"Reconstructed {written} cohort row(s) across {dates_written} date(s) "
            f"from {start} to {today - timedelta(days=1)} "
            f"(liveness window {window}d)."
        ))
