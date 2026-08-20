"""The read API: catalog, markets, the deal board, the index, research.

Every research answer ships inside ``envelope`` — as_of, coverage, methodology
version. That is not decoration: these numbers come from a crawl that can be
incomplete, and a survival curve computed across a coverage hole reads crawler
downtime as cars leaving the market.

Medians, never means. Asking prices here are heavily right-skewed and a handful
of typo listings (a Peugeot 206 at 5.8 trillion toman) dragged the mean to
roughly twice the median, so the headline number described no car on the market.
They are computed in Python: there is no portable ORM median aggregate.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import cache_page
from rest_framework import status, viewsets
from rest_framework.decorators import api_view
from rest_framework.generics import ListAPIView
from rest_framework.response import Response

from apps.core import pricing, research
from apps.core.filters import AdFilter
from apps.core.models import (
    Ad, Brand, DealScoreCache, MarketIndex, Model, NotifierSettings,
    PageCoverage, PriceObservation, Variant,
)
from apps.core.quality import (
    condition_discounted, verified, verified_by_ad, without_high_outliers,
)
from apps.core.serializers import (
    AdSerializer, BrandSerializer, ModelSerializer, NotifierSettingsSerializer,
    VariantSerializer,
)
from apps.jobs.fetcher import COVERAGE_WINDOW_HOURS, consecutive_blocks, find_gaps, known_feed_depth

# Bumped whenever a formula changes, so a screenshotted answer can be traced to
# the logic that produced it.
METHODOLOGY_VERSION = 2

# A sweep older than this means the picture is stale enough to say so.
FRESH_WITHIN = timedelta(hours=13)

# Shorter than the worker tick, so a cached landing page is never more than one
# cycle behind the data it summarises.
MARKETS_CACHE_SECONDS = 120


# ---------------------------------------------------------------------------
# Provenance envelope
# ---------------------------------------------------------------------------


def _coverage() -> dict:
    """How much of the market the answers are actually based on.

    Judged on accumulated ``PageCoverage`` over the window rather than on one run
    having set ``reached_end``: under a rolling crawl no single run walks the
    feed end to end, so the old query reported "no completed sweep" indefinitely
    while the feed was in fact fully covered.
    """
    now = timezone.now()
    # Whether the source is refusing us, reported alongside coverage because it
    # is the *cause* of the staleness a reader is looking at. It also implies
    # removal detection is paused, so a listing shown as active may be sold.
    blocked = consecutive_blocks()

    depth = known_feed_depth()
    if not depth:
        return {"complete_sweep": False, "reason": "no pages fetched recently",
                "source_blocked": bool(blocked)}

    gaps = find_gaps(since=now - timedelta(hours=COVERAGE_WINDOW_HOURS), max_rank=depth)
    missing = sum(hi - lo + 1 for lo, hi in gaps)
    last_fetch = (
        PageCoverage.objects.order_by("-fetched_at")
        .values_list("fetched_at", flat=True).first()
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
        # A gap is exactly the condition mark_inactive refuses to run under, so
        # this is the same fact the worker acts on.
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


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class BrandViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Brand.objects.all()
    serializer_class = BrandSerializer
    lookup_field = "slug"
    pagination_class = None


class BrandModelsView(ListAPIView):
    serializer_class = ModelSerializer
    pagination_class = None

    def get_queryset(self):
        brand = get_object_or_404(Brand, slug=self.kwargs["brand_slug"])
        return Model.objects.filter(brand=brand).order_by("name_fa")


class ModelVariantsView(ListAPIView):
    serializer_class = VariantSerializer
    pagination_class = None

    def get_queryset(self):
        model = get_object_or_404(Model, pk=self.kwargs["model_pk"])
        return Variant.objects.filter(model=model).order_by("name_fa")


class AdViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/ads/ and /api/ads/<code>/.

    The list is restricted to verified, ACTIVE, publish-complete, priced ads with
    the *high* cohort outliers hidden (?include_outliers=true restores them).
    The catalog and the statistics have to describe the same population, or a
    user can find an ad the market summary says does not exist.

    Only the high side is hidden: a price far above its peers is noise, a price
    far below them is the underpriced car this product exists to find.

    The detail route is deliberately unrestricted — a saved ad that gets
    delisted must still open, and it renders its own inactive notice.
    """

    serializer_class = AdSerializer
    lookup_field = "code"
    filterset_class = AdFilter
    ordering_fields = ("current_price", "year", "year_jalali", "mileage",
                       "publish_at", "last_seen_at", "image_count")
    search_fields = ("title", "brand__name_fa", "model__name_fa")

    def get_queryset(self):
        qs = verified(Ad.objects).select_related("brand", "model", "variant", "city", "dealer")
        if self.action == "list":
            qs = qs.filter(status=Ad.Status.ACTIVE, publish_at__isnull=False,
                           current_price__gt=0)
            if self.request.query_params.get("include_outliers", "").lower() != "true":
                qs = without_high_outliers(qs)
        return qs


# ---------------------------------------------------------------------------
# Markets and price history
# ---------------------------------------------------------------------------


@cache_page(MARKETS_CACHE_SECONDS)
@api_view(["GET"])
def markets(request):
    """Per-model market summary (publish-complete, priced), top-N by ad count."""
    try:
        limit = max(1, min(int(request.query_params.get("limit", 100)), 500))
    except ValueError:
        limit = 100

    rows = (
        verified(Ad.objects)
        .filter(current_price__gt=0, publish_at__isnull=False)
        .annotate(
            model_name=F("model__name_fa"),
            brand_slug=F("model__brand__slug"),
            brand_name=F("model__brand__name_fa"),
        )
        .values_list("model_id", "model_name", "brand_slug", "brand_name", "current_price")
    )

    prices: dict = defaultdict(list)
    meta: dict = {}
    for model_id, model_name, brand_slug, brand_name, price in rows.iterator():
        prices[model_id].append(price)
        meta.setdefault(model_id, (model_name, brand_slug, brand_name))

    out = []
    for model_id, values in prices.items():
        model_name, brand_slug, brand_name = meta[model_id]
        out.append({
            "model_id": model_id, "model_name": model_name,
            "brand_slug": brand_slug, "brand_name": brand_name,
            "ad_count": len(values), "min_price": min(values), "max_price": max(values),
            "median_price": int(statistics.median(values)),
        })
    out.sort(key=lambda r: r["ad_count"], reverse=True)
    return Response(out[:limit])


@api_view(["GET"])
def ad_price_history(request, code: str):
    """One ad's change-only price series."""
    ad = get_object_or_404(verified(Ad.objects.all()), code=code)
    rows = (
        verified_by_ad(PriceObservation.objects.filter(ad=ad))
        .order_by("observed_at")
        .values("observed_at", "price", "payment", "prepayment", "installments", "price_type")
    )
    return Response({"code": ad.code, "current_price": ad.current_price,
                     "series": list(rows)})


# ---------------------------------------------------------------------------
# The deal board
# ---------------------------------------------------------------------------

# Confidence label -> its peer-count range, derived from pricing's own tiers
# rather than restated, so retuning a tier cannot leave this filter selecting a
# band the badge no longer means.
_CONFIDENCE_PEERS = {
    label: (threshold, pricing.TIERS[i - 1][0] if i else None)
    for i, (threshold, label) in enumerate(pricing.TIERS)
}


def _opt(params, key, cast):
    raw = params.get(key)
    if raw in (None, "", "null"):
        return None
    try:
        return cast(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be {'an integer' if cast is int else 'a number'}")


def _deal_score_row(obj):
    components = obj.components or {}
    return {
        "code": obj.ad_id,
        "score": obj.score,
        "discount_pct": obj.discount_pct,
        "peer_median": obj.peer_median,
        "peer_count": components.get("peer_count"),
        "confidence": components.get("confidence"),
        "age_days": components.get("age_days"),
        "price": obj.ad.current_price,
        # year_jalali, never Ad.year: the raw column mixes 1399 and 2025.
        "year": obj.ad.year_jalali,
        "mileage": obj.ad.mileage,
        "url": obj.ad.url,
        "title": obj.ad.title,
        "model_name": getattr(obj, "model_name", None),
        "brand_name": getattr(obj, "brand_name", None),
        "primary_image_url": obj.ad.primary_image_url,
        "city_name": obj.ad.city.name_fa if obj.ad.city_id else "",
        # The listing's own explanation for being cheap. Not a reason to hide it,
        # but the cohort key has no condition dimension, so without this the gap
        # looks like free money instead of accident damage.
        "condition_flagged": condition_discounted(
            title=obj.ad.title, description=obj.ad.description
        ),
        "components": components,
    }


def _deal_score_qs():
    # Gated on read as well as at build time: the cache is rebuilt periodically,
    # so between an ad going bad and the next rebuild its stale score is still
    # served — and a deal score is the most acted-upon number on the site.
    return (
        verified_by_ad(DealScoreCache.objects.select_related("ad", "ad__city"))
        .annotate(model_name=F("ad__model__name_fa"), brand_name=F("ad__brand__name_fa"))
        # Tie-break on ad_id: hundreds of rows share a score to one decimal, and
        # an unstable sort under LIMIT/OFFSET drops and repeats listings as the
        # reader pages through.
        .order_by("-score", "ad_id")
    )


@api_view(["GET"])
def deal_scores(request):
    """Deal scores joined to their ad; filtered, ordered by discount, paginated.

    Returns ``count`` alongside ``results``: the cache holds ~9,800 rows and the
    screen used to show a hard-coded top 50 with no way forward, which put every
    genuine 5-20% deal out of reach.
    """
    params = request.query_params
    try:
        model = _opt(params, "model", int)
        year = _opt(params, "year", int)
        min_score = _opt(params, "min_score", float)
        max_score = _opt(params, "max_score", float)
        price_min = _opt(params, "price_min", int)
        price_max = _opt(params, "price_max", int)
        limit = max(1, min(_opt(params, "limit", int) or 50, 200))
        offset = max(0, _opt(params, "offset", int) or 0)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    qs = _deal_score_qs()
    filters = {
        "ad__model__brand__slug": params.get("brand") or None,
        "ad__model_id": model,
        "ad__year_jalali": year,
        "score__gte": min_score,
        "score__lte": max_score,
        "ad__current_price__gte": price_min,
        "ad__current_price__lte": price_max,
    }
    qs = qs.filter(**{k: v for k, v in filters.items() if v is not None})

    # peer_count and confidence live in the components JSON. Filtering
    # confidence by its peer-count threshold instead keeps it in SQL.
    confidence = params.get("confidence")
    if confidence in _CONFIDENCE_PEERS:
        lo, hi = _CONFIDENCE_PEERS[confidence]
        qs = qs.filter(components__peer_count__gte=lo)
        if hi is not None:
            qs = qs.filter(components__peer_count__lt=hi)

    count = qs.count()
    return envelope({
        "count": count, "limit": limit, "offset": offset,
        "results": [_deal_score_row(o) for o in qs[offset:offset + limit]],
    })


@api_view(["GET"])
def deal_score_detail(request, code: str):
    obj = get_object_or_404(
        verified_by_ad(DealScoreCache.objects.select_related("ad")).annotate(
            model_name=F("ad__model__name_fa"), brand_name=F("ad__brand__name_fa")
        ),
        ad_id=code,
    )
    return Response(_deal_score_row(obj))


@api_view(["GET"])
def market_index(request):
    """Composition-controlled price index (base 100).

    Unlike the raw median this cannot move because the mix of listings changed —
    only because prices within a cohort changed.
    """
    params = request.query_params
    scope = params.get("scope", MarketIndex.Scope.MARKET)
    if scope not in MarketIndex.Scope.values:
        return Response({"detail": f"scope must be one of {sorted(MarketIndex.Scope.values)}"},
                        status=status.HTTP_400_BAD_REQUEST)
    scope_id = params.get("id") or None
    if scope != MarketIndex.Scope.MARKET and not scope_id:
        return Response({"detail": f"scope={scope} requires ?id="},
                        status=status.HTTP_400_BAD_REQUEST)
    if scope == MarketIndex.Scope.MARKET:
        scope_id = None  # the market series is keyed on NULL, never a stray ?id
    try:
        requested_days = max(2, min(int(params.get("days", 90)), 3650))
    except (TypeError, ValueError):
        return Response({"detail": "days must be an integer"},
                        status=status.HTTP_400_BAD_REQUEST)

    series = research.read_index(scope, scope_id, days=requested_days)
    latest = series[-1] if series else None
    return envelope({
        "scope": scope,
        "scope_id": scope_id,
        "base_value": research.BASE_VALUE,
        "latest_index": latest["index_value"] if latest else None,
        # Total move across the window, which is what a reader wants ("prices
        # are up 3% this month"), not the raw index level.
        "change_pct": (
            round((latest["index_value"] / series[0]["index_value"] - 1) * 100, 2)
            if latest and series[0]["index_value"] else None
        ),
        # The real window, clamped to what exists. A caller asking for 90 days
        # against 29 days of history used to get a short series with nothing
        # saying it was short, and the screen went on calling it "90 days".
        "window": {
            "requested_days": requested_days,
            "days": len(series),
            "clamped": len(series) < requested_days,
            "first_date": series[0]["date"] if series else None,
            "last_date": latest["date"] if latest else None,
        },
        "series": series,
    })


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------


@api_view(["GET"])
def liquidity_view(request, model_id: int):
    """Time-to-delist for a cohort (Kaplan-Meier; still-live listings censored).

    Never described as "sold": the feed cannot distinguish a sale from an expiry
    or a withdrawal.
    """
    year = request.query_params.get("year")
    variant = request.query_params.get("variant")
    return envelope(research.survival(
        model_id=model_id,
        variant_id=int(variant) if variant else None,
        year_jalali=int(year) if year else None,
    ))


@api_view(["GET"])
def fair_price_view(request, code: str):
    """Explainable fair-price estimate for one listing, with components."""
    return envelope(pricing.fair_price(code))


@api_view(["GET"])
def depreciation_view(request, model_id: int):
    """Median asking price by model year, and retention against the newest."""
    variant = request.query_params.get("variant")
    return envelope(research.depreciation_curve(
        model_id, variant_id=int(variant) if variant else None,
    ))


@api_view(["GET"])
def overview_view(request):
    """Market summary: size, freshness and how much is verifiable.

    Uses the same population as the Explorer, or its "priced listings" subtitle
    would disagree with the Explorer footer by exactly the rows it does not show.
    """
    active = without_high_outliers(verified(Ad.objects).filter(status=Ad.Status.ACTIVE))
    return envelope({
        "active_listings": active.count(),
        "priced_listings": active.filter(current_price__gt=0).count(),
        "brands": active.values("brand_id").distinct().count(),
        "models": active.values("model_id").distinct().count(),
        "top_brands": list(
            active.values("brand__name_fa").annotate(n=Count("code")).order_by("-n")[:10]
        ),
    })


# ---------------------------------------------------------------------------
# Notifier rules (edited from the deal board)
# ---------------------------------------------------------------------------


@api_view(["GET", "PATCH"])
def notifier_settings(request):
    cfg = NotifierSettings.load()
    if request.method == "GET":
        return Response(NotifierSettingsSerializer(cfg).data)
    serializer = NotifierSettingsSerializer(cfg, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    serializer.save()
    return Response(serializer.data)
