"""FilterSet for cross-ad change events."""

import django_filters

from .models import AdChangeEvent


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
