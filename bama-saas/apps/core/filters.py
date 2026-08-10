"""FilterSets for core list endpoints (merged from catalog + history)."""

import django_filters

from apps.core.models import Ad, AdChangeEvent


class AdFilter(django_filters.FilterSet):
    brand = django_filters.CharFilter(field_name="brand__slug", lookup_expr="exact")
    model = django_filters.NumberFilter(field_name="model_id", lookup_expr="exact")
    variant = django_filters.NumberFilter(field_name="variant_id", lookup_expr="exact")
    city = django_filters.NumberFilter(field_name="city_id", lookup_expr="exact")

    # `year_min`/`year_max` keep their public names but range-filter on
    # `year_jalali`: raw `Ad.year` mixes Jalali (1399) and Gregorian (2025) in
    # one column, so a range over it is meaningless. Incoming values are
    # therefore interpreted as JALALI.
    year_min = django_filters.NumberFilter(field_name="year_jalali", lookup_expr="gte")
    year_max = django_filters.NumberFilter(field_name="year_jalali", lookup_expr="lte")

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


class ChangeFilter(django_filters.FilterSet):
    ad = django_filters.CharFilter(field_name="ad_id", lookup_expr="exact")
    event_type = django_filters.CharFilter(
        field_name="event_type", lookup_expr="exact"
    )
    fetch_run = django_filters.UUIDFilter(
        field_name="observation__fetch_run_id", lookup_expr="exact"
    )

    class Meta:
        model = AdChangeEvent
        fields = []
