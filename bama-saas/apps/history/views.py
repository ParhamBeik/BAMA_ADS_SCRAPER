"""Read-only DRF views for the history app.

Exposes per-ad provenance (versions/changes/timeline) plus cross-ad lookup
views (changes, observations, fetch-runs) for the dashboard.
"""

from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import api_view
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Ad

from .filters import ChangeFilter
from .models import AdChangeEvent, AdObservation, AdVersion, FetchRun
from .serializers import (
    AdChangeEventSerializer,
    AdObservationSerializer,
    AdVersionSerializer,
    FetchRunSerializer,
    TimelineEntrySerializer,
)


def _get_ad(code: str) -> Ad:
    """Fetch an ad by code or raise 404 (shared by the nested ad routes)."""
    return get_object_or_404(Ad, code=code)


class AdVersionsView(APIView):
    """GET /api/ads/<code>/versions/ — newest first."""

    pagination_class = None

    @extend_schema(responses=AdVersionSerializer(many=True), tags=["History"])
    def get(self, request, code):
        ad = _get_ad(code)
        qs = AdVersion.objects.filter(ad=ad).order_by("-first_observed_at")
        serializer = AdVersionSerializer(qs, many=True)
        return Response(serializer.data)


class AdChangesView(APIView):
    """GET /api/ads/<code>/changes/ — newest first."""

    pagination_class = None

    @extend_schema(responses=AdChangeEventSerializer(many=True), tags=["History"])
    def get(self, request, code):
        ad = _get_ad(code)
        qs = AdChangeEvent.objects.filter(ad=ad).order_by("-created_at")
        serializer = AdChangeEventSerializer(qs, many=True)
        return Response(serializer.data)


class AdTimelineView(APIView):
    """GET /api/ads/<code>/timeline/

    Merges an ad's observations and change events into a single time-ordered
    list of `{kind, at, detail}` dicts (`kind` is "observation" or "change").
    """

    pagination_class = None

    @extend_schema(responses=TimelineEntrySerializer(many=True), tags=["History"])
    def get(self, request, code):
        ad = _get_ad(code)
        observations = AdObservation.objects.filter(ad=ad)
        changes = AdChangeEvent.objects.filter(ad=ad)

        entries = []
        for obs in observations:
            entries.append(
                {
                    "kind": "observation",
                    "at": obs.observed_at,
                    "detail": {
                        "id": obs.id,
                        "fetch_run_id": obs.fetch_run_id,
                        "version_id": obs.version_id,
                        "raw_hash": obs.raw_hash,
                        "publish_phrase": obs.publish_phrase,
                        "rank": obs.rank,
                    },
                }
            )
        for chg in changes:
            entries.append(
                {
                    "kind": "change",
                    "at": chg.created_at,
                    "detail": {
                        "id": chg.id,
                        "event_type": chg.event_type,
                        "origin": chg.origin,
                        "categories": chg.categories,
                        "changed_paths": chg.changed_paths,
                        "changes": chg.changes,
                    },
                }
            )

        entries.sort(key=lambda e: e["at"], reverse=True)
        serializer = TimelineEntrySerializer(entries, many=True)
        return Response(serializer.data)


class ChangeViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/changes/ and GET /api/changes/<id>/ (cross-ad)."""

    queryset = (
        AdChangeEvent.objects.all()
        .select_related("ad", "previous_version", "new_version")
        .order_by("-created_at")
    )
    serializer_class = AdChangeEventSerializer
    filterset_class = ChangeFilter


class ObservationViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/observations/ and GET /api/observations/<id>/."""

    queryset = AdObservation.objects.all().select_related("ad", "fetch_run", "version")
    serializer_class = AdObservationSerializer
    filterset_fields = {
        "ad": ["exact"],
        "fetch_run": ["exact"],
    }


class FetchRunViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/fetch-runs/ and GET /api/fetch-runs/<id>/."""

    queryset = FetchRun.objects.all()
    serializer_class = FetchRunSerializer
