"""History serializers (read-only): versions, observations, change events, runs."""

from rest_framework import serializers

from apps.core.serializers.catalog import AdSerializer  # noqa: F401  (kept for reuse)

from apps.core.models import AdChangeEvent, AdObservation, AdVersion, FetchRun


class FetchRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = FetchRun
        fields = (
            "id",
            "source",
            "status",
            "max_ads",
            "page_pause",
            "fetched_count",
            "created_count",
            "updated_count",
            "skipped_count",
            "price_change_count",
            "error",
            "created_at",
            "started_at",
            "finished_at",
        )


class AdVersionSerializer(serializers.ModelSerializer):
    ad_code = serializers.CharField(source="ad_id", read_only=True)

    class Meta:
        model = AdVersion
        fields = (
            "id",
            "ad_code",
            "semantic_hash",
            "raw_hash",
            "payload",
            "origin",
            "first_observed_at",
        )


class AdObservationSerializer(serializers.ModelSerializer):
    ad_code = serializers.CharField(source="ad_id", read_only=True)
    fetch_run_id = serializers.UUIDField(read_only=True)
    version_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = AdObservation
        fields = (
            "id",
            "ad_code",
            "fetch_run_id",
            "version_id",
            "observed_at",
            "raw_hash",
            "publish_phrase",
            "rank",
        )


class AdChangeEventSerializer(serializers.ModelSerializer):
    """Expose the rich change payload plus minimal ad/version references.

    `categories`, `changed_paths`, and `changes` are JSONField lists/dicts and
    serialize natively; we also nest the ad code and the previous/new version
    hashes so a client can follow the version chain without extra round-trips.
    """

    ad_code = serializers.CharField(source="ad_id", read_only=True)
    observation_id = serializers.IntegerField(read_only=True)
    previous_version_hash = serializers.CharField(
        source="previous_version.semantic_hash", read_only=True, default=None
    )
    new_version_hash = serializers.CharField(
        source="new_version.semantic_hash", read_only=True
    )
    categories = serializers.JSONField(read_only=True)
    changed_paths = serializers.JSONField(read_only=True)
    changes = serializers.JSONField(read_only=True)

    class Meta:
        model = AdChangeEvent
        fields = (
            "id",
            "ad_code",
            "observation_id",
            "previous_version_hash",
            "new_version_hash",
            "event_type",
            "categories",
            "changed_paths",
            "changes",
            "origin",
            "created_at",
        )


class TimelineEntrySerializer(serializers.Serializer):
    """One item in a merged ad timeline (observation or change)."""

    kind = serializers.CharField(read_only=True)
    at = serializers.DateTimeField(read_only=True)
    detail = serializers.JSONField(read_only=True)
