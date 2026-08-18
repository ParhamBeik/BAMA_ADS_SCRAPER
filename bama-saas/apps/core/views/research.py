"""Research endpoints: time-to-delist, price position, fair price, retention.

Every response here carries a provenance envelope — ``as_of``, coverage, sample
size, confidence and methodology version. That is not decoration. These numbers
are produced from a crawl that can be incomplete, and a survival curve computed
across a coverage hole reads crawler downtime as cars leaving the market. A
number without its provenance cannot be checked by the person acting on it, and
the whole point of this layer is that the answers are auditable.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.core.models import PageCoverage
from apps.core.services import fair_price as FP
from apps.core.services import liquidity as L
from apps.core.services import retention as R
from apps.jobs.services.coverage import (
    COVERAGE_WINDOW_HOURS,
    find_gaps,
    known_feed_depth,
)

# Bumped whenever a formula changes, so a stored or screenshotted answer can be
# traced to the logic that produced it.
METHODOLOGY_VERSION = 2

# A sweep older than this means the picture is stale enough to say so.
FRESH_WITHIN = timedelta(hours=13)


def _coverage() -> dict:
    """How much of the market the answers below are actually based on.

    Judged on accumulated ``PageCoverage`` over the coverage window rather than
    on one run having set ``reached_end``. Under a rolling crawl no single run
    walks the feed end to end, so the old query reported "no completed sweep"
    indefinitely — telling the reader the answer was unverified while the feed
    was in fact fully covered.
    """
    from apps.jobs.services import crawl_gate

    now = timezone.now()
    # Whether the source is currently refusing us. Reported alongside coverage
    # because it is the *cause* of the staleness a reader is looking at, and a
    # frozen catalog with no explanation is the kind of silently-wrong number
    # this envelope exists to prevent. It also implies removal detection is
    # paused, so a listing shown as active may already be sold.
    blocked = crawl_gate.consecutive_blocks()

    depth = known_feed_depth()
    if not depth:
        return {
            "complete_sweep": False,
            "reason": "no pages fetched recently",
            "source_blocked": bool(blocked),
        }

    gaps = find_gaps(since=now - timedelta(hours=COVERAGE_WINDOW_HOURS), max_rank=depth)
    missing = sum(hi - lo + 1 for lo, hi in gaps)
    last_fetch = (
        PageCoverage.objects.order_by("-fetched_at")
        .values_list("fetched_at", flat=True)
        .first()
    )
    age = now - last_fetch if last_fetch else None
    return {
        "complete_sweep": not gaps,
        "swept_at": last_fetch,
        "ads_covered": depth - missing,
        "deepest_rank": depth,
        "uncovered_ranks": missing,
        "stale": bool(age and age > FRESH_WITHIN),
        "age_hours": round(age.total_seconds() / 3600, 1) if age else None,
        "source_blocked": bool(blocked),
        # A gap in the covered ranks is exactly the condition mark_inactive_ads
        # refuses to run under, so this is the same fact the worker acts on.
        "removal_detection_paused": bool(gaps),
    }


def envelope(payload: dict, **extra) -> Response:
    """Wrap an answer with everything needed to judge it."""
    return Response({
        **payload,
        "as_of": timezone.now(),
        "coverage": _coverage(),
        "methodology_version": METHODOLOGY_VERSION,
        **extra,
    })


@extend_schema(
    tags=["Research"], responses={200: OpenApiTypes.OBJECT},
    description=(
        "Time-to-delist for a cohort, using Kaplan-Meier so listings that are "
        "still live are censored rather than dropped. Never described as 'sold': "
        "the feed cannot distinguish a sale from an expiry or a withdrawal."
    ),
)
@api_view(["GET"])
def liquidity_view(request, model_id: int):
    year = request.query_params.get("year")
    variant = request.query_params.get("variant")
    return envelope(L.survival(
        model_id=model_id,
        variant_id=int(variant) if variant else None,
        year_jalali=int(year) if year else None,
    ))


@extend_schema(
    tags=["Research"], responses={200: OpenApiTypes.OBJECT},
    description="Explainable fair-price estimate for one listing, with components.",
)
@api_view(["GET"])
def fair_price_view(request, code: str):
    return envelope(FP.fair_price(code))


@extend_schema(
    tags=["Research"], responses={200: OpenApiTypes.OBJECT},
    description="Median asking price by model year, and retention against the newest.",
)
@api_view(["GET"])
def depreciation_view(request, model_id: int):
    variant = request.query_params.get("variant")
    return envelope(R.depreciation_curve(
        model_id, variant_id=int(variant) if variant else None,
    ))


@extend_schema(
    tags=["Research"], responses={200: OpenApiTypes.OBJECT},
    description="Market summary: size, freshness and how much is verifiable.",
)
@api_view(["GET"])
def overview_view(request):
    from django.db.models import Count

    from apps.core.models import Ad
    from apps.core.services.quality import verified, without_high_outliers

    # The overview is a buyer-facing summary, so it must use the same population
    # as the Explorer: verified active listings with absurdly high historical
    # outliers removed. Otherwise its "priced listings" subtitle disagrees with
    # the Explorer footer by exactly the rows the Explorer does not show.
    active = without_high_outliers(
        verified(Ad.objects).filter(status=Ad.Status.ACTIVE)
    )
    return envelope({
        "active_listings": active.count(),
        "priced_listings": active.filter(current_price__gt=0).count(),
        "brands": active.values("brand_id").distinct().count(),
        "models": active.values("model_id").distinct().count(),
        "top_brands": list(
            active.values("brand__name_fa")
            .annotate(n=Count("code")).order_by("-n")[:10]
        ),
    })
