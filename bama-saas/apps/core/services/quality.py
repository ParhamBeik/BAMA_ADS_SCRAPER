"""Single read-side chokepoint for "is this ad row trustworthy?".

Verification writes every fired rule id into ``Ad.quality_flags`` (empty list ==
completely clean). Analytics funnels through ``verified`` so no consumer can
forget the filter and silently average in a rial-denominated price or a
priceless lump-sum ad.

Only HARD flags exclude a row. Soft flags are advisory — they exist so a spike in
one of them is readable as "Bama changed their schema" — and must not remove
otherwise-good data. An ad whose publish phrase failed to parse still has a
perfectly valid price and belongs in the price statistics.
"""

from __future__ import annotations

from django.db.models import Q

from apps.jobs.services.verify import HARD_RULE_IDS

# Cohort-pass verdicts. The detector that wrote them (verify_cohort.py) is gone,
# so these are now frozen historical data: no new row will ever carry one. The
# filter stays because the flags already on 66k rows are still correct about
# those rows, and fair_price's baselines — which the deal score is now built on —
# must not be defined by a listing priced nothing like its peers.
FLAG_OUTLIER_HIGH = "price_outlier_high"
FLAG_OUTLIER_LOW = "price_outlier_low"
COHORT_FLAGS = frozenset({FLAG_OUTLIER_HIGH, FLAG_OUTLIER_LOW})


def verified(qs):
    """Restrict an Ad queryset to rows that failed no hard verification rule.

    Uses jsonb containment (``@>``), which the ``ad_quality_gin`` index serves.
    """
    disqualifying = Q()
    for rule_id in sorted(HARD_RULE_IDS):
        disqualifying |= Q(quality_flags__contains=[rule_id])
    return qs.exclude(disqualifying)


def without_cohort_outliers(qs):
    """Drop rows the cohort pass marked as priced far from their peers.

    Separate from ``verified`` on purpose, and used only where a *baseline* is
    being computed — a median, a mean, a regression, an index level. An outlier
    must not help define the number that judges it, but it is still a real
    listing: excluding it from the catalog would delete the underpriced car this
    product exists to find. Callers that show listings to a user should keep it
    and surface the flag instead.

    Uses jsonb containment (``@>``), served by the ``ad_cohort_gin`` index.
    """
    disqualifying = Q()
    for flag in sorted(COHORT_FLAGS):
        disqualifying |= Q(cohort_flags__contains=[flag])
    return qs.exclude(disqualifying)


def verified_by_ad(qs, field: str = "ad"):
    """Restrict a queryset that *references* Ad to rows whose ad is verified.

    ``verified()`` only knows how to filter Ad itself, so every price-side
    consumer (PriceObservation, PriceDropEvent) was left unfiltered — the
    Bollinger bands and the price-trend series were computed over observations
    belonging to ads the Ad-side analytics had already excluded. One helper here
    beats repeating the join predicate at each call site.
    """
    from apps.core.models import Ad  # local: avoids a models <-> services cycle

    return qs.filter(**{f"{field}__in": verified(Ad.objects)})
