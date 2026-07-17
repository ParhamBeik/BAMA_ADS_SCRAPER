"""FilterSet for the Ad list endpoint."""

import django_filters

from .models import Ad


class AdFilter(django_filters.FilterSet):
    brand = django_filters.CharFilter(field_name="brand__slug", lookup_expr="exact")
    model = django_filters.NumberFilter(field_name="model_id", lookup_expr="exact")
    variant = django_filters.NumberFilter(field_name="variant_id", lookup_expr="exact")
    city = django_filters.NumberFilter(field_name="city_id", lookup_expr="exact")

    year_min = django_filters.NumberFilter(field_name="year", lookup_expr="gte")
    year_max = django_filters.NumberFilter(field_name="year", lookup_expr="lte")

    price_min = django_filters.NumberFilter(
        field_name="current_price", lookup_expr="gte"
    )
    price_max = django_filters.NumberFilter(
        field_name="current_price", lookup_expr="lte"
    )

    mileage_max = django_filters.NumberFilter(field_name="mileage", lookup_expr="lte")
    transmission = django_filters.CharFilter(
        field_name="transmission", lookup_expr="exact"
    )

    publish_from = django_filters.DateTimeFilter(
        field_name="publish_at", lookup_expr="gte"
    )
    last_seen_from = django_filters.DateTimeFilter(
        field_name="last_seen_at", lookup_expr="gte"
    )

    class Meta:
        model = Ad
        fields = []
