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

import hashlib
import statistics
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Case, Count, F, IntegerField, Q, Value, When
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from apps.core import images, pricing, research
from apps.core.filters import AdFilter
from apps.core.models import (
    Ad,
    Brand,
    DealScoreCache,
    MarketIndex,
    Model,
    NotifierSettings,
    PageCoverage,
    PriceObservation,
    Variant,
)
from apps.core.quality import (
    condition_band,
    condition_discounted,
    verified,
    verified_by_ad,
    without_high_outliers,
)
from apps.core.serializers import (
    AdSerializer,
    BrandSerializer,
    ModelSerializer,
    NotifierSettingsSerializer,
    VariantSerializer,
)
from apps.jobs.fetcher import (
    COVERAGE_GAP_TOLERANCE_RANKS,
    COVERAGE_WINDOW_HOURS,
    consecutive_blocks,
    find_gaps,
    known_feed_depth,
    uncovered_ranks,
)
from apps.jobs.parsing import absolute_ad_url
from apps.jobs.verify import MAX_JALALI_YEAR, MIN_JALALI_YEAR

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
    missing = uncovered_ranks(gaps)
    # The same judgement `mark_inactive` acts on, not a stricter one. Reporting
    # "no complete sweep" off a six-rank hole the worker itself tolerates put a
    # permanent warning strip on every screen.
    swept = missing <= COVERAGE_GAP_TOLERANCE_RANKS
    last_fetch = (
        PageCoverage.objects.order_by("-fetched_at")
        .values_list("fetched_at", flat=True).first()
    )
    age = now - last_fetch if last_fetch else None
    return {
        "complete_sweep": swept,
        "swept_at": last_fetch,
        "ads_covered": depth - missing,
        "deepest_rank": depth,
        "uncovered_ranks": missing,
        "stale": bool(age and age > FRESH_WITHIN),
        "age_hours": round(age.total_seconds() / 3600, 1) if age else None,
        "source_blocked": bool(blocked),
        # Exactly the condition mark_inactive refuses to run under, so this is
        # the same fact the worker acts on.
        "removal_detection_paused": not swept,
    }


def envelope(payload: dict, *, coverage: dict | None = None, **extra) -> Response:
    """Wrap an answer with everything needed to judge it.

    `coverage` is passed in by the cached endpoints so it keeps the vintage of
    the answer it qualifies — see `cached_answer`. Uncached endpoints read it
    fresh, which for them is the same thing.
    """
    return Response({
        **payload,
        "as_of": timezone.now(),
        "coverage": coverage if coverage is not None else _coverage(),
        "methodology_version": METHODOLOGY_VERSION,
        **extra,
    })


def cache_key(prefix: str, parts: dict) -> str:
    """A cache key that cannot be shaped by what the caller typed.

    Brand slugs reach us as text, and `ingest` mints collision-path slugs that
    embed the raw Persian name, spaces and all — which Django's Redis backend
    warns about on every get and memcached rejects outright. Hashing the scope
    keeps the key opaque and fixed-width; the prefix keeps it greppable.
    """
    scope = "|".join(f"{k}={parts[k]!r}" for k in sorted(parts))
    return f"{prefix}:{hashlib.sha256(scope.encode()).hexdigest()[:32]}"


def cached(key: str, seconds: int, produce):
    """Cache the answer, never the response.

    These aggregations are shared — every reader gets the same market — so they
    are worth caching. But `cache_page` wraps a view from the outside, and a hit
    returns the stored response before DRF runs at all, permission check
    included: one signed-in reader warmed the cache and, for the rest of that
    window, anyone at all could fetch the same URL and be served the answer.
    Holding the payload instead keeps the gate in front of every single request
    and still pays for the query once. See tests/test_api.py for the case that
    fails the moment this goes back to a response-level cache.
    """
    hit = cache.get(key)
    if hit is None:
        hit = produce()
        cache.set(key, hit, seconds)
    return hit


def cached_answer(key: str, seconds: int, produce) -> tuple[dict, dict]:
    """An answer and the coverage that qualifies it, kept as one vintage.

    Coverage is what tells a reader whether to trust the number printed beside
    it, so the two have to age together. Held on separate clocks they drift: a
    coverage reading taken after the crawl closed a gap ends up badging a figure
    that was computed while the gap was open, which reads as "complete sweep" on
    a number the hole is precisely the reason to doubt. The response cache this
    replaced got that right for free by storing both under one key; storing them
    as one entry is how to keep it.

    It also keeps `_coverage` off the per-request path for these endpoints —
    `find_gaps` pulls a 13-hour window of rows into Python, which is the
    heaviest query in the envelope.
    """
    hit = cache.get(key)
    if hit is None:
        hit = {"payload": produce(), "coverage": _coverage()}
        cache.set(key, hit, seconds)
    return hit["payload"], hit["coverage"]


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


# How many models a search returns. The catalog has a long tail of one-listing
# models; a picker that lists all of them is a scroll, not a choice.
MODEL_SEARCH_LIMIT = 60


@api_view(["GET"])
def model_search(request):
    """Models across every brand, searchable, with how many listings each has.

    The filter panel used to force brand-then-model: to find a 206 you first had
    to know it is a پژو and pick that. This is the flat list behind a single
    "which car" box, so typing the model name is enough.

    Counted over the same population the Explorer lists, or the number beside a
    model would promise listings the next screen does not show.

    ``?id=`` resolves one model by primary key, ignoring the search and brand
    filters. A screen restored from a shared URL knows the id and nothing else,
    and the ranked list only reaches 60 rows — so without this the picker on a
    link to an unpopular model could not name the car the page was about.
    """
    params = request.query_params
    qs = Model.objects.select_related("brand")
    by_id = (params.get("id") or "").strip()
    if by_id:
        qs = qs.filter(pk=by_id) if by_id.isdigit() else qs.none()
    else:
        if brand := params.get("brand"):
            qs = qs.filter(brand__slug=brand)
        if q := (params.get("q") or "").strip():
            qs = qs.filter(Q(name_fa__icontains=q) | Q(brand__name_fa__icontains=q))

    listable = without_high_outliers(pricing.scorable_rows())
    rows = qs.annotate(ad_count=Count("ads", filter=Q(ads__in=listable), distinct=True))
    if not by_id:
        # A model nobody is currently selling is noise in a picker — but it is
        # still the answer when the caller asked for that exact model by id, and
        # a screen that cannot name the car it is about is worse than one
        # reporting an empty count.
        rows = rows.filter(ad_count__gt=0)
    rows = rows.order_by("-ad_count", "name_fa")[:MODEL_SEARCH_LIMIT]
    return Response([
        {"id": m.id, "name_fa": m.name_fa, "brand_slug": m.brand_id,
         "brand_name": m.brand.name_fa, "ad_count": m.ad_count}
        for m in rows
    ])


# Everything `GET /api/ads/` actually reads: the filterset's own names, the two
# DRF backends' parameters, and the one local flag. Derived from `AdFilter`
# rather than restated, so a filter added there is accepted here without a
# second edit — the failure mode of a hand-maintained copy is a working filter
# rejected as unknown.
_AD_QUERY_PARAMS = frozenset(AdFilter.base_filters) | {
    "page", "ordering", "include_outliers",
}


class AdViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/ads/ and /api/ads/<code>/.

    The list is `pricing.scorable_rows()` with the *high* cohort outliers hidden
    (?include_outliers=true restores them) — the same population the deal board
    scores and every analytic counts. It used to be a private copy of that
    filter that had drifted: it took any price above zero and never excluded
    instalment listings, so sorting by cheapest-first returned eight حواله
    allocations priced at their deposit before it returned a car, and the app
    printed four different "active listings" totals on four screens.

    Only the high side of the outlier flag is hidden: a price far above its
    peers is noise, a price far below them is the underpriced car this product
    exists to find.

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
        related = ("brand", "model", "variant", "city", "dealer")
        if self.action != "list":
            return verified(Ad.objects).select_related(*related)
        qs = pricing.scorable_rows().select_related(*related)
        if self.request.query_params.get("include_outliers", "").lower() != "true":
            qs = without_high_outliers(qs)
        return qs

    def list(self, request, *args, **kwargs):
        """Refuse a query parameter nothing here reads.

        DRF ignores unknown parameters, so `?search=پژو` returned every one of
        the 22,997 listings with a 200 and no hint that the search had not
        happened — indistinguishable from a search that matched everything. A
        filter that silently does nothing is worse than one that errors.
        """
        unknown = sorted(set(request.query_params) - _AD_QUERY_PARAMS)
        if unknown:
            return Response(
                {"detail": f"unknown query parameter(s): {', '.join(unknown)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().list(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Listing photos
# ---------------------------------------------------------------------------


@api_view(["GET"])
@throttle_classes([])
def listing_image(request, code: str, index: int | None = None):
    """One listing photo, cached in Redis and served from our own origin.

    ``index`` addresses the gallery; the ``/thumb/`` route omits it and gets the
    narrower card-sized file instead.

    Throttle-exempt on purpose. A card page is 24 photos and the detail gallery
    is up to 12 more, so the global per-user rate would start rejecting images
    part-way down the first scroll — and a rate limiter that fires on a page's
    own assets is indistinguishable from the broken CDN this endpoint exists to
    work around. The cost is bounded elsewhere: a response is only ever produced
    for a URL already stored on an ad, and every byte after the first fetch
    comes from cache.

    A cold cache during a Bama block redirects to the CDN rather than 404ing —
    the block is on *our* egress, and the user's own browser can usually still
    reach the picture.
    """
    ad = get_object_or_404(Ad.objects.only("code", "image_urls", "primary_image_url"),
                           code=code)
    url = images.source_url(ad, index)
    if not url:
        return Response({"detail": "no such image"}, status=status.HTTP_404_NOT_FOUND)

    fetched = images.fetch(url)
    if fetched is None:
        return HttpResponseRedirect(url)

    content_type, body = fetched
    response = HttpResponse(body, content_type=content_type)
    # Bama's image URLs are content-addressed, so the bytes behind one of our
    # paths cannot change without the ad's stored URL changing too — which makes
    # this genuinely immutable rather than optimistically so.
    #
    # `private`, not `public`: this endpoint requires a login, and `public`
    # invites any proxy or CDN placed in front of it to keep a copy and hand
    # that copy to whoever asks — the same shape as the response cache that made
    # the analytics readable without an account. Browsers still cache it.
    response["Cache-Control"] = f"private, max-age={settings.IMAGE_CACHE_SECONDS}, immutable"
    response["ETag"] = f'"{hashlib.sha256(body).hexdigest()[:32]}"'
    response["Content-Length"] = str(len(body))
    return response


# ---------------------------------------------------------------------------
# Markets and price history
# ---------------------------------------------------------------------------


def _market_summary() -> list[dict]:
    """Every model's price summary, ranked by how many cars are listed.

    Same population as the Explorer and the deal board: it used to omit the
    ACTIVE filter and the instalment exclusion, so its per-model ad counts and
    medians described a set no screen could show.
    """
    rows = (
        pricing.scorable_rows()
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
    return out


@api_view(["GET"])
def markets(request):
    """Per-model market summary (publish-complete, priced), top-N by ad count."""
    try:
        limit = max(1, min(int(request.query_params.get("limit", 100)), 500))
    except ValueError:
        limit = 100

    # Cached whole and sliced per request, so readers asking for different
    # depths of the same board share one scan rather than one each.
    return Response(cached("markets:summary", MARKETS_CACHE_SECONDS, _market_summary)[:limit])


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
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{key} must be {'an integer' if cast is int else 'a number'}"
        ) from exc


def _model_year(params):
    """`?year=`, refused outside the range the catalogue can hold.

    Every other part of a scope names a row, so the catalogue's own size bounds
    how many distinct scopes exist and therefore how many cache entries. A year
    names nothing, so without this it is any integer at all — and each one is a
    fresh key that misses the cache and re-runs the scan behind it. `verify`
    already refuses to store a year outside these bounds, so nothing in range is
    excluded and this needs no query to decide.
    """
    year = _opt(params, "year", int)
    if year is not None and not (MIN_JALALI_YEAR <= year <= MAX_JALALI_YEAR):
        raise ValueError(f"year must be a Jalali year between "
                         f"{MIN_JALALI_YEAR} and {MAX_JALALI_YEAR}")
    return year


def _deal_score_row(obj, *, now=None):
    components = obj.components or {}
    published = obj.ad.publish_at
    now = now or timezone.now()
    return {
        "code": obj.ad_id,
        # No `score`. The column exists because SQL orders on it, but it is
        # `round(discount_pct, 1)` and nothing more, so shipping it beside
        # `discount_pct` under a name that implies a composite invited the
        # reader to look for weighting that is not there. Freshness band and
        # discount are the whole ordering, and both are already in this row.
        "discount_pct": obj.discount_pct,
        "peer_median": obj.peer_median,
        "peer_count": components.get("peer_count"),
        "confidence": components.get("confidence"),
        # Two different facts, deliberately not merged. `age_days` is how long
        # WE have known about the ad; `days_listed` is how long it has been on
        # Bama by Bama's own reckoning, which is what the ordering uses and what
        # a buyer means by "how long has this been sitting".
        "age_days": components.get("age_days"),
        "days_listed": (now - published).days if published else None,
        "publish_at": published,
        # The band this row was ORDERED by, not one the client re-derives.
        # `days_listed` is a floor to whole days, so recomputing the band from it
        # lands on the wrong side of every edge (an ad aged 3.5 days floors to 3
        # and reads as the 1-3 band while SQL sorted it into 4-7) — which drew a
        # second, out-of-order heading further down the same grid.
        "freshness": getattr(obj, "freshness", None),
        "price": obj.ad.current_price,
        # year_jalali, never Ad.year: the raw column mixes 1399 and 2025.
        "year": obj.ad.year_jalali,
        "mileage": obj.ad.mileage,
        "bama_url": absolute_ad_url(obj.ad.url or obj.ad.canonical_path),
        "title": obj.ad.title,
        "model_name": getattr(obj, "model_name", None),
        "brand_name": getattr(obj, "brand_name", None),
        "image_url": images.ad_image_paths(obj.ad)[0],
        "city_name": obj.ad.city.name_fa if obj.ad.city_id else "",
        # The listing's own explanation for being cheap. Not a reason to hide it,
        # but the cohort key has no condition dimension, so without this the gap
        # looks like free money instead of accident damage.
        "condition_flagged": condition_discounted(
            title=obj.ad.title, description=obj.ad.description,
            body_status=obj.ad.body_status,
        ),
        "body_status": obj.ad.body_status,
        "condition_band": condition_band(obj.ad.body_status),
        "district": getattr(obj.ad, "district", "") or "",
        "components": components,
    }


def _freshness_band(now):
    """A 0..4 rank for how recently the ad was published or bumped.

    Computed in SQL so the board can order on it under LIMIT/OFFSET. Nulls sort
    into the last band: an ad with no publish time is not evidence of freshness.
    """
    whens = [
        When(ad__publish_at__gte=now - timedelta(days=days), then=Value(rank))
        for rank, (days, _) in enumerate(pricing.FRESHNESS_BANDS)
    ]
    return Case(*whens, default=Value(len(pricing.FRESHNESS_BANDS)),
                output_field=IntegerField())


def _deal_score_qs(*, now=None, by_freshness: bool = True):
    # Gated on read as well as at build time: the cache is rebuilt periodically,
    # so between an ad going bad and the next rebuild its stale score is still
    # served — and a deal score is the most acted-upon number on the site.
    qs = (
        verified_by_ad(DealScoreCache.objects.select_related("ad", "ad__city"))
        .annotate(model_name=F("ad__model__name_fa"), brand_name=F("ad__brand__name_fa"))
    )
    # Tie-break on ad_id in both orders: hundreds of rows share a score to one
    # decimal, and an unstable sort under LIMIT/OFFSET drops and repeats
    # listings as the reader pages through.
    if not by_freshness:
        return qs.order_by("-score", "ad_id")
    return (
        qs.annotate(freshness=_freshness_band(now or timezone.now()))
        .order_by("freshness", "-score", "ad_id")
    )


BANDS = ("top", "all", "review")


@api_view(["GET"])
def deal_scores(request):
    """The board: three bands, freshest first, paginated.

    ``band`` decides which population is being asked for, and the two that are
    ranked put freshness ahead of size of discount. A three-week-old asking
    price is a worse guide to what a car costs today than a fresh one, however
    large the gap looks — the discount still decides order *within* a band.

    * ``top`` (default) — inside ``deal_window()``: recent enough, and in the
      better part of that window's own discount distribution. Both thresholds
      are measured from the current board, not hardcoded, so a quiet day is a
      short board rather than a month of stale listings.
    * ``all`` — everything at or below the trusted ceiling, same recency
      window, no discount floor.
    * ``review`` — above the ceiling, *or* carrying a declared body condition
      that already explains the gap. Same recency window. Not hidden, but not
      recommended either: past ~25% the gap is an attribute the cohort key
      cannot see far more often than it is a bargain, and a repainted or
      panel-replaced car is that same attribute stated outright by the seller.
      Ranked by discount, since that is the thing being reviewed.

    The two ranked bands are therefore disjoint from ``review``: a car whose
    cheapness the listing itself accounts for is not a find.

    Returns ``count`` alongside ``results``: the cache holds ~9,800 rows and the
    screen used to show a hard-coded top 50 with no way forward, which put every
    genuine 5-20% deal out of reach.
    """
    params = request.query_params
    try:
        model = _opt(params, "model", int)
        year = _opt(params, "year", int)
        year_min = _opt(params, "year_min", int)
        year_max = _opt(params, "year_max", int)
        price_min = _opt(params, "price_min", int)
        price_max = _opt(params, "price_max", int)
        mileage_min = _opt(params, "mileage_min", int)
        mileage_max = _opt(params, "mileage_max", int)
        limit = max(1, min(_opt(params, "limit", int) or 50, 200))
        offset = max(0, _opt(params, "offset", int) or 0)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    band = params.get("band") or "top"
    if band not in BANDS:
        return Response({"detail": f"band must be one of {', '.join(BANDS)}"},
                        status=status.HTTP_400_BAD_REQUEST)

    now = timezone.now()
    window = pricing.deal_window(now=now)
    qs = _deal_score_qs(now=now, by_freshness=band != "review")
    cutoff = now - timedelta(days=window["window_days"])

    # "The gap has a cause this score cannot see" — once as a threshold on the
    # discount, once as the seller's own declaration. Built as one predicate so
    # the three bands cannot drift into overlapping or leaving a row homeless.
    needs_review = Q(discount_pct__gt=window["ceiling_pct"]) | Q(needs_review=True)
    if band == "review":
        qs = qs.filter(needs_review)
    else:
        qs = qs.exclude(needs_review)
        if band == "top":
            qs = qs.filter(discount_pct__gte=window["min_discount_pct"])
    qs = qs.filter(ad__publish_at__gte=cutoff)

    filters = {
        "ad__model__brand__slug": params.get("brand") or None,
        "ad__model_id": model,
        "ad__year_jalali": year,
        "ad__year_jalali__gte": year_min,
        "ad__year_jalali__lte": year_max,
        "ad__current_price__gte": price_min,
        "ad__current_price__lte": price_max,
        "ad__mileage__gte": mileage_min,
        "ad__mileage__lte": mileage_max,
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
        "count": count, "limit": limit, "offset": offset, "band": band,
        # The thresholds the page is standing on, so the screen can state them
        # rather than describing a filter it does not know the value of.
        "window": window,
        "results": [_deal_score_row(o, now=now) for o in qs[offset:offset + limit]],
    })


@api_view(["GET"])
def deal_score_detail(request, code: str):
    # Same queryset as the board, so the detail card can never disagree with the
    # row the reader clicked.
    return Response(_deal_score_row(get_object_or_404(_deal_score_qs(), ad_id=code)))


def _series_sample(series: list[dict]) -> dict:
    """What the series is standing on, and whether that is enough to say so.

    Every index panel already printed "built from N cohorts and M ads on the
    last day" — but printed it identically whether N was 40 or 2, so a
    year-1386 scope drawn from 2 cohorts and 15 ads got the same presentation as
    the whole market. The judgement belongs with the number, not with whoever
    reads the caption.

    Measured on the most recent day that actually contributed a return: an
    under-covered day carries the level forward with nothing behind it, so its
    zeroes describe the crawler, not the scope.
    """
    contributing = [p for p in series if not p.get("low_coverage")]
    latest = contributing[-1] if contributing else None
    cohorts = (latest or {}).get("cohort_count") or 0
    return {
        "cohort_count": cohorts,
        "ad_count": (latest or {}).get("ad_count") or 0,
        # Same bar the movers leaderboard uses to decide a scope is rankable,
        # and the same one `jobs.market_index` uses to decide a scope gets a
        # series at all — restating it here would be a third copy to retune.
        "thin": cohorts < research.MOVER_MIN_COHORTS,
        "min_cohorts": research.MOVER_MIN_COHORTS,
        # Days inside the window the crawler under-covered, whose returns were
        # withheld. A window with several is a window to read carefully.
        "low_coverage_days": sum(1 for p in series if p.get("low_coverage")),
        # The largest chained step in the window. 1 on a healthy series; the
        # market's biggest single "daily" move sat on a four-day hole.
        "max_gap_days": max((p.get("gap_days") or 1 for p in series), default=1),
    }


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
        "sample": _series_sample(series),
        "series": series,
    })


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------


# The home page is the same answer for everyone and is rebuilt on the warm tick,
# so it is cached for the same reason `markets` is: shorter than the worker's
# cycle, so a cached summary is never more than one tick behind its data.
PULSE_CACHE_SECONDS = 120


def _window_days(params, default: int = 30) -> int:
    """`?days=`, clamped. Two days is the shortest thing that can have a change."""
    try:
        return max(2, min(int(params.get("days", default)), 3650))
    except (TypeError, ValueError):
        return default


def _leaderboard_limit(params, default: int = 8) -> int:
    """`?limit=`, clamped, never raising.

    These are read-only leaderboards with no meaningful failure mode for a
    junk limit, and `_opt` raises — which turned `?limit=abc` into a 500 rather
    than a board with the default number of rows on it.
    """
    try:
        return max(1, min(int(params.get("limit", default)), 50))
    except (TypeError, ValueError):
        return default


@api_view(["GET"])
def movers_view(request):
    """Brands or models ranked by how far their price index moved.

    Ranks the per-scope series the warm tick already writes (see
    jobs.market_index) — the UI asked only ever for the market-wide one, so this
    is reach, not new computation.
    """
    scope = request.query_params.get("scope", MarketIndex.Scope.MODEL)
    if scope not in (MarketIndex.Scope.BRAND, MarketIndex.Scope.MODEL):
        return Response({"detail": "scope must be brand or model"},
                        status=status.HTTP_400_BAD_REQUEST)
    limit = _leaderboard_limit(request.query_params)
    days = _window_days(request.query_params)
    payload, coverage = cached_answer(
        cache_key("pulse:movers", {"scope": scope, "days": days, "limit": limit}),
        PULSE_CACHE_SECONDS,
        lambda: research.movers(scope, days=days, limit=limit),
    )
    return envelope(payload, coverage=coverage)


@api_view(["GET"])
def turnover_view(request):
    """Which models' listings leave the feed fastest, as a completed-window rate.

    Never "sold": the feed carries no reason, so this counts departures.
    """
    limit = _leaderboard_limit(request.query_params)
    days = _window_days(request.query_params)
    payload, coverage = cached_answer(
        cache_key("pulse:turnover", {"days": days, "limit": limit}), PULSE_CACHE_SECONDS,
        lambda: research.turnover(days=days, limit=limit),
    )
    return envelope(payload, coverage=coverage)


@api_view(["GET"])
def arrivals_view(request):
    """Which models are taking on the most new listings — the supply half."""
    limit = _leaderboard_limit(request.query_params)
    days = _window_days(request.query_params)
    payload, coverage = cached_answer(
        cache_key("pulse:arrivals", {"days": days, "limit": limit}), PULSE_CACHE_SECONDS,
        lambda: research.arrivals(days=days, limit=limit),
    )
    return envelope(payload, coverage=coverage)


@api_view(["GET"])
def distribution_view(request):
    """Asking-price shape for any scope, market-wide down to one model year.

    Cached because the population filter includes the installment-ad regex,
    which the query it lives on documents as an unindexed scan — fine once a
    cycle, not fine once per keystroke in a scope picker.
    """
    params = request.query_params
    try:
        scope = {
            "brand": params.get("brand") or None,
            "model_id": _opt(params, "model", int),
            "variant_id": _opt(params, "variant", int),
            "year_jalali": _model_year(params),
        }
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    payload, coverage = cached_answer(
        cache_key("distribution", scope), MARKETS_CACHE_SECONDS,
        lambda: research.price_distribution(**scope),
    )
    return envelope(payload, coverage=coverage)


def _movement(model_id: int, variant_id: int | None, year: int | None, days: int) -> dict:
    """The rebased index series for one scope. Pure arithmetic over snapshots."""
    series = research.compute_index(research.cohort_series(
        MarketIndex.Scope.MODEL, str(model_id),
        variant_id=variant_id, year_jalali=year,
    ))[-days:]
    latest = series[-1] if series else None
    if not latest:
        return {"available": False, "reason": "insufficient_clean_history",
                "scope": {"model_id": model_id, "variant_id": variant_id,
                          "year_jalali": year}}
    # Re-based on the first day still inside the window: the stored series is
    # chained from its own first observation, and slicing it without re-basing
    # would show a level that answers a question about a date off the chart.
    base = series[0]["index_value"] or research.BASE_VALUE
    rebased = [
        {**point,
         "date": point["date"].isoformat() if hasattr(point["date"], "isoformat")
                 else point["date"],
         "index_value": round(point["index_value"] / base * research.BASE_VALUE, 2)}
        for point in series
    ]
    return {
        "available": True,
        "scope": {"model_id": model_id, "variant_id": variant_id, "year_jalali": year},
        "base_value": research.BASE_VALUE,
        "latest_index": rebased[-1]["index_value"],
        "change_pct": round((latest["index_value"] / base - 1) * 100, 2),
        "window": {
            "requested_days": days,
            "days": len(rebased),
            "clamped": len(rebased) < days,
            "first_date": rebased[0]["date"],
            "last_date": rebased[-1]["date"],
        },
        "sample": _series_sample(rebased),
        "series": rebased,
    }


@api_view(["GET"])
def movement_view(request):
    """A price index for a scope finer than the three that get persisted.

    `market-index` serves the market, a brand or a model from stored rows. A
    trim or a single model year is computed here on demand from the same daily
    snapshots, because persisting a series per trim would multiply the warm
    tick's writes for a question most sessions never ask.

    Cached for the same reason its siblings are: this is the one endpoint on the
    analysis page that aggregates at request time rather than reading a stored
    series, and the scope picker fires it on every trim and model-year change.
    """
    params = request.query_params
    try:
        model_id = _opt(params, "model", int)
        variant_id = _opt(params, "variant", int)
        year = _model_year(params)
        days = _window_days(params, default=90)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    if not model_id:
        return Response({"detail": "movement requires ?model="},
                        status=status.HTTP_400_BAD_REQUEST)

    payload, coverage = cached_answer(
        cache_key("movement", {"model": model_id, "variant": variant_id,
                               "year": year, "days": days}), MARKETS_CACHE_SECONDS,
        lambda: _movement(model_id, variant_id, year, days),
    )
    return envelope(payload, coverage=coverage)


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
    """Explainable fair-price estimate for one listing, with components.

    404 when there is no such ad, matching its sibling: `/api/ads/ZZZZNOPE/`
    answered 404 while `/api/ads/ZZZZNOPE/fair-price/` answered 200 with
    ``available: false``, so a client could not tell a typo'd code from a real
    car with too few peers — and the screen rendered "not enough data" for a
    listing that does not exist.

    ``insufficient_peers`` stays a 200: that one *is* an answer about a real ad.
    """
    result = pricing.fair_price(code)
    if result.get("reason") == "unknown_or_unverified_ad":
        return Response({"detail": "No Ad matches the given query."},
                        status=status.HTTP_404_NOT_FOUND)
    return envelope(result)


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

    Literally the Explorer's queryset, not a description of it. The previous
    version filtered only on ACTIVE, so the front page's "23,028 active
    listings" counted rows the Explorer's own footer put at 22,997 and the deal
    board at 22,170 — three numbers for one market, on three screens of the same
    app.

    ``priced_listings`` is gone with it. It was `active.filter(price > 0)` over a
    population that was already priced, so it could only ever equal
    `active_listings`, and it did: 23,028 printed twice, once as a caption on
    itself. ``instalment_listings`` answers the question that tile was reaching
    for — how many listings are excluded because their price is a down payment
    — and is a number that can differ from the one above it.
    """
    active = without_high_outliers(pricing.scorable_rows())
    excluded = without_high_outliers(
        verified(Ad.objects).filter(status=Ad.Status.ACTIVE, price_basis_unclear=True)
    )
    return envelope({
        "active_listings": active.count(),
        "instalment_listings": excluded.count(),
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
@permission_classes([IsAdminUser])
def notifier_settings(request):
    """Staff only, because there is exactly one of these.

    `NotifierSettings` is a singleton by design — a single-operator tool, not a
    per-user rule table. Left on the default `IsAuthenticated` it let any
    account that had signed up rewrite the operator's alerting for everyone, or
    simply switch it off, from a panel the deal board showed to all of them.
    """
    cfg = NotifierSettings.load()
    if request.method == "GET":
        return Response(NotifierSettingsSerializer(cfg).data)
    serializer = NotifierSettingsSerializer(cfg, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    serializer.save()
    return Response(serializer.data)
