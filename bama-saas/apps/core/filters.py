"""Query filters for GET /api/ads/.

No ``has_image`` filter: every stored ad has one. The feed is crawled with
``image=1&priced=1`` and ``verify._photo_missing`` is hard, so "photoless"
is not a state a row can be in — a filter for it would have exactly one
answer forever, and ``?has_image=false`` would silently return nothing.
"""

import django_filters
from django.db.models import Q

from apps.core.models import Ad


class AdFilter(django_filters.FilterSet):
    brand = django_filters.CharFilter(field_name="brand__slug")
    model = django_filters.NumberFilter(field_name="model_id")
    variant = django_filters.NumberFilter(field_name="variant_id")
    city = django_filters.NumberFilter(field_name="city_id")

    # `year_min`/`year_max` keep their public names but range-filter on
    # `year_jalali`: raw `Ad.year` mixes Jalali (1399) and Gregorian (2025) in
    # one column, so a range over it is meaningless. Incoming values are
    # therefore interpreted as JALALI.
    year_min = django_filters.NumberFilter(field_name="year_jalali", lookup_expr="gte")
    year_max = django_filters.NumberFilter(field_name="year_jalali", lookup_expr="lte")

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
        ),
        method="filter_condition",
    )

    def filter_seller_type(self, queryset, name, value):
        return queryset.filter(dealer__isnull=value != "dealer")

    def filter_q(self, queryset, name, value):
        if not value:
            return queryset
        from apps.core.normalization import search_tokens, to_persian_digits

        tokens = search_tokens(value)
        if not tokens:
            return queryset

        combined_q = Q()
        for t in tokens:
            token_q = (
                Q(title__icontains=t)
                | Q(brand__name_fa__icontains=t)
                | Q(model__name_fa__icontains=t)
                | Q(description__icontains=t)
            )
            # If the token contains digits, also query Persian digit representation
            persian_t = to_persian_digits(t)
            if persian_t != t:
                token_q |= (
                    Q(title__icontains=persian_t)
                    | Q(model__name_fa__icontains=persian_t)
                    | Q(description__icontains=persian_t)
                )
            combined_q &= token_q

        return queryset.filter(combined_q)

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
        return queryset.filter(condition_band_q(value))

    class Meta:
        model = Ad
        fields = []
