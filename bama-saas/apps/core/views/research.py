"""Research endpoints: liquidity, fair price, negotiation, retention.

Every response here carries a provenance envelope — ``as_of``, coverage, sample
size, confidence and methodology version. That is not decoration. These numbers
are produced from a crawl that can be incomplete, and a survival curve computed
across a coverage hole reads crawler downtime as cars leaving the market. A number
without its provenance cannot be checked by the person acting on it, and the
whole point of this layer is that the answers are auditable.

Access follows the tier: the market overview is public, per-listing tools need an
account, and the cohort research that takes real compute is premium.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import IsActiveSubscription
from apps.core.models import FetchRun, ListingEpisode, VehicleIdentity
from apps.core.services import fair_price as FP
from apps.core.services import liquidity as L
from apps.core.services import retention as R
from apps.jobs.services.identity import identity_summary

# Bumped whenever a formula changes, so a stored or screenshotted answer can be
# traced to the logic that produced it.
METHODOLOGY_VERSION = 1

# A sweep older than this means the picture is stale enough to say so.
FRESH_WITHIN = timedelta(hours=13)


def _coverage() -> dict:
    """How much of the market the answers below are actually based on."""
    latest = (
        FetchRun.objects.filter(
            mode=FetchRun.Mode.FULL, reached_end=True,
            status=FetchRun.Status.SUCCEEDED,
        )
        .order_by("-started_at")
        .values("started_at", "deepest_rank", "fetched_count")
        .first()
    )
    if not latest:
        return {"complete_sweep": False, "reason": "no completed sweep on record"}
    age = timezone.now() - latest["started_at"]
    return {
        "complete_sweep": True,
        "swept_at": latest["started_at"],
        "ads_covered": latest["fetched_count"],
        "deepest_rank": latest["deepest_rank"],
        "stale": age > FRESH_WITHIN,
        "age_hours": round(age.total_seconds() / 3600, 1),
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
@permission_classes([IsAuthenticated, IsActiveSubscription])
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
    description=(
        "Time-to-delist split by where a listing's price sits within its own "
        "cohort. Association, not causation — an overpriced car and a slow car "
        "may share a cause rather than one producing the other."
    ),
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, IsActiveSubscription])
def price_position_view(request, model_id: int):
    year = request.query_params.get("year")
    return envelope(L.hazard_by_price_position(
        model_id=model_id, year_jalali=int(year) if year else None,
    ))


@extend_schema(
    tags=["Research"], responses={200: OpenApiTypes.OBJECT},
    description="Explainable fair-price estimate for one listing, with components.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def fair_price_view(request, code: str):
    return envelope(FP.fair_price(code))


@extend_schema(
    tags=["Research"], responses={200: OpenApiTypes.OBJECT},
    description="How far sellers in this cohort actually moved on price.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, IsActiveSubscription])
def negotiation_view(request, model_id: int):
    year = request.query_params.get("year")
    return envelope(FP.negotiation_room(
        model_id=model_id, year_jalali=int(year) if year else None,
    ))


@extend_schema(
    tags=["Research"], responses={200: OpenApiTypes.OBJECT},
    description="Cohorts ranked by price spread — where bargaining pays.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, IsActiveSubscription])
def dispersion_view(request):
    limit = min(int(request.query_params.get("limit", 20)), 100)
    return envelope({"cohorts": FP.dispersion_leaderboard(limit=limit)})


@extend_schema(
    tags=["Research"], responses={200: OpenApiTypes.OBJECT},
    description="Median asking price by model year, and retention against the newest.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def depreciation_view(request, model_id: int):
    variant = request.query_params.get("variant")
    return envelope(R.depreciation_curve(
        model_id, variant_id=int(variant) if variant else None,
    ))


@extend_schema(
    tags=["Research"], responses={200: OpenApiTypes.OBJECT},
    description="Models ranked by value retention across the observed age span.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, IsActiveSubscription])
def retention_view(request):
    limit = min(int(request.query_params.get("limit", 20)), 100)
    return envelope({"models": R.retention_leaderboard(limit=limit)})


@extend_schema(
    tags=["Research"], responses={200: OpenApiTypes.OBJECT},
    description="City price differences with the model mix held constant.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, IsActiveSubscription])
def regional_view(request):
    model = request.query_params.get("model")
    return envelope(R.regional_spread(model_id=int(model) if model else None))


@extend_schema(
    tags=["Research"], responses={200: OpenApiTypes.OBJECT},
    description=(
        "Listing history for the physical car behind an ad: every code it has "
        "appeared under, and whether those overlap (duplicate listings) or "
        "follow one another (a relist)."
    ),
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ad_identity_view(request, code: str):
    episode = (
        ListingEpisode.objects.filter(ad_id=code)
        .exclude(identity__isnull=True)
        .order_by("-started_at")
        .first()
    )
    if episode is None:
        return envelope({
            "code": code, "identified": False,
            "reason": "no shared-photo evidence for this listing",
        })
    summary = identity_summary(episode.identity_id)
    return envelope({"code": code, "identified": True, **summary})


@extend_schema(
    tags=["Research"], responses={200: OpenApiTypes.OBJECT},
    description="Public market summary: size, freshness and how much is verifiable.",
)
@api_view(["GET"])
@permission_classes([AllowAny])
def overview_view(request):
    from django.db.models import Count

    from apps.core.models import Ad
    from apps.core.services.quality import verified

    active = verified(Ad.objects).filter(status=Ad.Status.ACTIVE)
    return envelope({
        "active_listings": active.count(),
        "priced_listings": active.filter(current_price__gt=0).count(),
        "distinct_vehicles_identified": VehicleIdentity.objects.count(),
        "brands": active.values("brand_id").distinct().count(),
        "models": active.values("model_id").distinct().count(),
        "top_brands": list(
            active.values("brand__name_fa")
            .annotate(n=Count("code")).order_by("-n")[:10]
        ),
    })
