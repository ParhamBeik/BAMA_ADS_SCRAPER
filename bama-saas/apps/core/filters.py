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

    def filter_seller_type(self, queryset, name, value):
        return queryset.filter(dealer__isnull=value != "dealer")

    def filter_q(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            Q(title__icontains=value)
            | Q(brand__name_fa__icontains=value)
            | Q(model__name_fa__icontains=value)
            | Q(description__icontains=value)
        )

    class Meta:
        model = Ad
        fields = []
