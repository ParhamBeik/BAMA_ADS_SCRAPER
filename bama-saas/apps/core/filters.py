"""Query filters for GET /api/ads/.

No ``has_image`` filter: every stored ad has one. The feed is crawled with
``image=1&priced=1`` and ``verify._photo_missing`` is hard, so "photoless"
is not a state a row can be in — a filter for it would have exactly one
answer forever, and ``?has_image=false`` would silently return nothing.
"""

import django_filters
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from apps.common.parsing import normalize_model_year
from apps.core.models import Ad, Model

# Several cars at once (Cars tab). Bounded so one URL cannot become an
# unbounded IN list.
MAX_CARS = 10


def car_scope_q(brands: str | None, models: str | None, *, prefix: str = "") -> Q | None:
    """``?brand=a,b&model=1,2``: every listed model, plus every listed brand
    none of whose models was picked.

    So "Peugeot 206 + Peugeot 207 + all of Quick" is ``brand=peugeot,quick
    &model=<206>,<207>``, and the old single-car link ``brand=X&model=Y`` keeps
    meaning model Y, because a picked model narrows its own brand.
    """
    brand_list = [b for b in (brands or "").split(",") if b.strip()][:MAX_CARS]
    raw_models = [m.strip() for m in (models or "").split(",") if m.strip()][:MAX_CARS]
    if not brand_list and not raw_models:
        return None
    if not all(m.isdigit() for m in raw_models):
        raise ValidationError({"model": "model must be a comma-separated list of ids"})
    model_ids = [int(m) for m in raw_models]
    narrowed = set(
        Model.objects.filter(pk__in=model_ids).values_list("brand_id", flat=True)
    )
    whole_brands = [b.strip() for b in brand_list if b.strip() not in narrowed]
    q = Q()
    if model_ids:
        q |= Q(**{f"{prefix}model_id__in": model_ids})
    if whole_brands:
        q |= Q(**{f"{prefix}model__brand__slug__in": whole_brands})
    return q if q else Q(pk__in=[])


class AdFilter(django_filters.FilterSet):
    variant = django_filters.NumberFilter(field_name="variant_id")
    city = django_filters.NumberFilter(field_name="city_id")

    # `year_min`/`year_max` range-filter on `year_jalali`: raw `Ad.year` mixes
    # Jalali (1399) and Gregorian (2025) in one column, so a range over it is
    # meaningless. A value in either calendar is accepted and converted.
    year_min = django_filters.NumberFilter(method="filter_year")
    year_max = django_filters.NumberFilter(method="filter_year")

    price_min = django_filters.NumberFilter(field_name="current_price", lookup_expr="gte")
    price_max = django_filters.NumberFilter(field_name="current_price", lookup_expr="lte")
    mileage_min = django_filters.NumberFilter(field_name="mileage", lookup_expr="gte")
    mileage_max = django_filters.NumberFilter(field_name="mileage", lookup_expr="lte")

    transmission = django_filters.CharFilter(field_name="transmission")
    body_type = django_filters.CharFilter(field_name="body_type", lookup_expr="iexact")
    fuel = django_filters.CharFilter(field_name="fuel", lookup_expr="iexact")
    status = django_filters.CharFilter(field_name="status")
    seller_authenticated = django_filters.BooleanFilter(field_name="seller_authenticated")
    publish_from = django_filters.DateTimeFilter(field_name="publish_at", lookup_expr="gte")
    last_seen_from = django_filters.DateTimeFilter(field_name="last_seen_at", lookup_expr="gte")
    # When the ad first appeared. Bama's own date (`publish_at`) resets on every
    # bump, so "listed this week" has to read our first sighting instead.
    listed_from = django_filters.DateTimeFilter(field_name="first_seen_at", lookup_expr="gte")
    listed_to = django_filters.DateTimeFilter(field_name="first_seen_at", lookup_expr="lte")

    seller_type = django_filters.ChoiceFilter(
        choices=(("dealer", "dealer"), ("private", "private")),
        method="filter_seller_type",
    )
    q = django_filters.CharFilter(method="filter_q")
    condition = django_filters.ChoiceFilter(
        choices=(
            ("clean", "clean"),
            ("cosmetic", "cosmetic"),
            ("painted", "painted"),
            ("structural", "structural"),
            # The price page's peer groups also split out 0 km cars
            # (`quality.peer_band`), and its listings link carries that on.
            ("new", "new"),
        ),
        method="filter_condition",
    )

    def filter_seller_type(self, queryset, name, value):
        return queryset.filter(dealer__isnull=value != "dealer")

    def filter_q(self, queryset, name, value):
        if not value:
            return queryset
        from apps.core.normalization import search_tokens, token_variants

        tokens = search_tokens(value)
        if not tokens:
            return queryset

        combined_q = Q()
        for t in tokens:
            word_q = Q()
            for variant in token_variants(t):
                word_q |= Q(search_text__icontains=variant)
            combined_q &= word_q

        return queryset.filter(combined_q)

    def filter_year(self, queryset, name, value):
        jalali = normalize_model_year(int(value))[0]
        if jalali is None:
            return queryset.none()
        lookup = "gte" if name == "year_min" else "lte"
        return queryset.filter(**{f"year_jalali__{lookup}": jalali})

    def filter_condition(self, queryset, name, value):
        """Four-band body condition, matching ``quality.condition_band``.

        This used to be a hand-written include/exclude pair per band — a second
        copy of ``quality._BAND_RULES`` expressed backwards, which is a copy that
        drifts the first time a rule is added. ``condition_band_q`` derives the
        predicate from those rules directly, so there is one definition again.
        """
        from apps.core.quality import condition_band_q

        if not value:
            return queryset
        if value == "new":  # pre-2026-10-13 links; 0 km is now a mileage choice
            return queryset.filter(mileage=0)
        # Body condition describes used cars: a 0 km car has no paint by
        # definition and is chosen through mileage instead.
        return queryset.filter(condition_band_q(value)).exclude(mileage=0)

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        scope = car_scope_q(self.data.get("brand"), self.data.get("model"))
        return queryset if scope is None else queryset.filter(scope)

    class Meta:
        model = Ad
        fields = []
