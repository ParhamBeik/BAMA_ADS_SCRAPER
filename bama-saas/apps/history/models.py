"""Append-only provenance + versioning models.

This mirrors the proven SQLite history schema in `bama-scraper/src/history.py`
(fetch_runs / ad_versions / ad_observations / change_events) but in PostgreSQL
with queryable JSONB instead of zlib blobs.
"""

import uuid

from django.contrib.postgres.indexes import GinIndex
from django.db import models


class FetchRun(models.Model):
    """One ingestion pass: a live fetch, a bulk import, or a history replay."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    class Source(models.TextChoices):
        LIVE_FETCH = "live_fetch", "Live fetch"
        BULK_IMPORT = "bulk_import", "Bulk import"
        HISTORY_REPLAY = "history_replay", "History replay"
        LEGACY_BASELINE = "legacy_baseline", "Legacy baseline"
        MANUAL = "manual", "Manual"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(
        max_length=32, choices=Source.choices, default=Source.BULK_IMPORT, db_index=True
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    max_ads = models.IntegerField(null=True, blank=True)
    page_pause = models.FloatField(null=True, blank=True)
    fetched_count = models.IntegerField(default=0)
    created_count = models.IntegerField(default=0)
    updated_count = models.IntegerField(default=0)
    skipped_count = models.IntegerField(default=0)
    price_change_count = models.IntegerField(default=0)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "history_fetchrun"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.source} {self.id.hex[:8]} ({self.status})"


class AdVersion(models.Model):
    """An immutable content snapshot of an ad, deduped by semantic hash."""

    class Origin(models.TextChoices):
        LIVE_FETCH = "live_fetch", "Live fetch"
        BULK_IMPORT = "bulk_import", "Bulk import"
        HISTORY_REPLAY = "history_replay", "History replay"
        LEGACY_BASELINE = "legacy_baseline", "Legacy baseline"

    ad = models.ForeignKey(
        "catalog.Ad", on_delete=models.CASCADE, related_name="versions"
    )
    semantic_hash = models.CharField(max_length=64, db_index=True)
    raw_hash = models.CharField(max_length=64)
    payload = models.JSONField(null=True, blank=True)
    origin = models.CharField(
        max_length=32, choices=Origin.choices, default=Origin.BULK_IMPORT
    )
    first_observed_at = models.DateTimeField()

    class Meta:
        db_table = "history_adversion"
        ordering = ("first_observed_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("ad", "semantic_hash"), name="uq_adversion_ad_semantic"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.ad_id} {self.semantic_hash[:8]}"


class AdObservation(models.Model):
    """A single sighting of an ad within one run, pinned to a version."""

    ad = models.ForeignKey(
        "catalog.Ad", on_delete=models.CASCADE, related_name="observations"
    )
    fetch_run = models.ForeignKey(
        FetchRun, on_delete=models.CASCADE, related_name="observations"
    )
    version = models.ForeignKey(
        AdVersion, on_delete=models.CASCADE, related_name="observations"
    )
    observed_at = models.DateTimeField()
    raw_hash = models.CharField(max_length=64)
    publish_phrase = models.CharField(max_length=120, blank=True)
    rank = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = "history_adobservation"
        ordering = ("observed_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("fetch_run", "ad"), name="uq_observation_run_ad"
            ),
        ]
        indexes = [
            models.Index(fields=("ad", "observed_at"), name="obs_ad_time_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.ad_id} in {self.fetch_run_id}"


class AdChangeEvent(models.Model):
    """A recorded change between two observations of an ad."""

    class EventType(models.TextChoices):
        CONTENT_CHANGED = "content_changed", "Content changed"
        ROUTE_CHANGED = "route_changed", "Route changed"
        REAPPEARED = "reappeared", "Reappeared"

    ad = models.ForeignKey(
        "catalog.Ad", on_delete=models.CASCADE, related_name="change_events"
    )
    observation = models.ForeignKey(
        AdObservation, on_delete=models.CASCADE, related_name="change_events"
    )
    previous_version = models.ForeignKey(
        AdVersion, on_delete=models.SET_NULL, related_name="changes_as_prev",
        null=True, blank=True,
    )
    new_version = models.ForeignKey(
        AdVersion, on_delete=models.CASCADE, related_name="changes_as_new"
    )
    event_type = models.CharField(max_length=24, choices=EventType.choices)
    categories = models.JSONField(default=list, blank=True)
    changed_paths = models.JSONField(default=list, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    origin = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "history_adchangeevent"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("observation", "event_type"), name="uq_change_obs_type"
            ),
        ]
        indexes = [
            GinIndex(fields=["categories"], name="change_cat_gin"),
            GinIndex(fields=["changed_paths"], name="change_paths_gin"),
        ]

    def __str__(self) -> str:
        return f"{self.ad_id} {self.event_type}"


class AuditRun(models.Model):
    """Outcome of a data-quality audit pass."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    summary = models.JSONField(null=True, blank=True)
    report = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "history_auditrun"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"Audit {self.id.hex[:8]} ({self.status})"


class UnknownTimePhrase(models.Model):
    """Persian publish phrases we could not parse (for later pattern mining)."""

    phrase = models.CharField(max_length=120, primary_key=True)
    seen_count = models.IntegerField(default=1)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    first_fetch_run = models.ForeignKey(
        FetchRun, on_delete=models.SET_NULL, related_name="first_unknown_phrases",
        null=True, blank=True,
    )
    last_fetch_run = models.ForeignKey(
        FetchRun, on_delete=models.SET_NULL, related_name="last_unknown_phrases",
        null=True, blank=True,
    )

    class Meta:
        db_table = "history_unknownphrase"

    def __str__(self) -> str:
        return self.phrase
