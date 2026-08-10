"""Append-only provenance + versioning models.

This mirrors the proven append-only SQLite history schema the data was collected with
(fetch_runs / ad_versions / ad_observations / change_events) but in PostgreSQL
with queryable JSONB instead of zlib blobs.
"""

import uuid

from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.utils import timezone


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

    class Mode(models.TextChoices):
        DELTA = "delta", "Delta (shallow, early-stopping)"
        FULL = "full", "Full sweep (page 0 to end of feed)"
        BACKFILL = "backfill", "Backfill (explicit page range)"

    class StopReason(models.TextChoices):
        END_OF_FEED = "end_of_feed", "Reached the empty page past the last ad"
        STALE_PAGES = "stale_pages", "Consecutive pages with nothing new"
        MAX_ADS = "max_ads", "Hit the max_ads cap"
        MAX_PAGES = "max_pages", "Hit the page-range end (backfill)"
        ERROR = "error", "Aborted on an unrecoverable error"
        INTERRUPTED = "interrupted", "Interrupted by the operator"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(
        max_length=32, choices=Source.choices, default=Source.BULK_IMPORT, db_index=True
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    max_ads = models.IntegerField(null=True, blank=True)
    page_pause = models.FloatField(null=True, blank=True)

    # Coverage state. Without these, "did this run actually cover the feed?" is
    # unanswerable and delta stopping can only guess from wall-clock elapsed
    # time. `deepest_rank` is the highest ad rank observed (the feed numbers ads
    # 1..N by recency); `reached_end` means we saw the empty page past the last
    # ad, which is the only proof of full coverage.
    mode = models.CharField(
        max_length=16, choices=Mode.choices, default=Mode.DELTA, db_index=True
    )
    start_page = models.IntegerField(default=0)
    pages_fetched = models.IntegerField(default=0)
    deepest_rank = models.IntegerField(null=True, blank=True)
    reached_end = models.BooleanField(default=False)
    stop_reason = models.CharField(
        max_length=24, choices=StopReason.choices, blank=True
    )
    # Set when a run aborts mid-sweep so the next run can resume instead of
    # restarting from page 0 and re-reading everything.
    resume_from_page = models.IntegerField(null=True, blank=True)

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


class PageCoverage(models.Model):
    """One fetched listing page: which rank range it held and what changed.

    This is the crawl's ledger. The Bama feed numbers ads by recency, so
    ``rank = PAGE_SIZE * page_index + 1 .. + PAGE_SIZE``. Recording the range we
    actually saw (and when) turns "have we covered the feed?" from a guess into
    a query — see ``apps.jobs.services.coverage.find_gaps``.

    Insertions push ads to higher ranks, which only makes us re-read them
    (idempotent). Deletions pull ads to lower ranks, past pages already read —
    that is the case that silently loses ads, and the reason coverage is tracked
    by rank range and re-verified on a cadence rather than assumed.
    """

    fetch_run = models.ForeignKey(
        FetchRun, on_delete=models.CASCADE, related_name="page_coverages"
    )
    page_index = models.IntegerField()
    rank_lo = models.IntegerField()
    rank_hi = models.IntegerField()
    ad_count = models.IntegerField(default=0)
    new_count = models.IntegerField(default=0)
    changed_count = models.IntegerField(default=0)
    fetched_at = models.DateTimeField()

    class Meta:
        db_table = "history_pagecoverage"
        ordering = ("fetch_run", "page_index")
        constraints = [
            models.UniqueConstraint(
                fields=("fetch_run", "page_index"), name="uq_pagecov_run_page"
            ),
        ]
        indexes = [
            # Gap scan: "which rank ranges were last seen before cutoff?"
            models.Index(fields=("rank_lo", "fetched_at"), name="pagecov_rank_time_idx"),
            models.Index(fields=("fetched_at",), name="pagecov_time_idx"),
        ]

    def __str__(self) -> str:
        return f"page {self.page_index} ranks {self.rank_lo}-{self.rank_hi}"


class IngestReject(models.Model):
    """An ad that failed a verification rule — quarantined, never dropped.

    One row per (ad, failed rule) per run. The raw payload is kept so a rule
    that turns out to be wrong can be replayed, and so a spike in a single
    ``rule`` is directly readable as "Bama changed their schema".
    """

    code = models.CharField(max_length=16, blank=True, db_index=True)
    rule = models.CharField(max_length=64, db_index=True)
    detail = models.TextField(blank=True)
    raw_payload = models.JSONField(null=True, blank=True)
    fetch_run = models.ForeignKey(
        FetchRun, on_delete=models.SET_NULL, related_name="rejects",
        null=True, blank=True,
    )
    observed_at = models.DateTimeField()

    class Meta:
        db_table = "history_ingestreject"
        ordering = ("-observed_at",)
        indexes = [
            # Quarantine-rate monitoring per rule over time.
            models.Index(fields=("rule", "observed_at"), name="reject_rule_time_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code or '?'} failed {self.rule}"


class AdVersion(models.Model):
    """An immutable content snapshot of an ad, deduped by semantic hash."""

    class Origin(models.TextChoices):
        LIVE_FETCH = "live_fetch", "Live fetch"
        BULK_IMPORT = "bulk_import", "Bulk import"
        HISTORY_REPLAY = "history_replay", "History replay"
        LEGACY_BASELINE = "legacy_baseline", "Legacy baseline"

    ad = models.ForeignKey(
        "core.Ad", on_delete=models.CASCADE, related_name="versions"
    )
    semantic_hash = models.CharField(max_length=64, db_index=True)
    raw_hash = models.CharField(max_length=64)
    # Which generation of the volatile-field rules produced semantic_hash. Two
    # hashes are only comparable within one version, so without this a rule change
    # silently splits an ad's history into two incomparable halves and every
    # version created before the change looks like a content change after it.
    semantic_hash_version = models.IntegerField(default=1, db_index=True)
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
        "core.Ad", on_delete=models.CASCADE, related_name="observations"
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
        "core.Ad", on_delete=models.CASCADE, related_name="change_events"
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


class VehicleIdentity(models.Model):
    """One physical car, which may appear under several listing codes.

    Bama stores an ad's photos under a per-vehicle path
    ``/VehicleCarImages/<uuid>/CarImage_....jpg``, and that uuid is reused when
    the same car is listed again. It is therefore near-conclusive evidence of
    identity — far stronger than matching on model, year, mileage and price,
    which a thousand identical Prides also share.

    Measured on the live database: 65 uuids covered 139 listing codes, and in
    every inspected case the model, year, mileage and price agreed exactly.
    """

    class Method(models.TextChoices):
        IMAGE_ASSET = "image_asset", "Shared image asset path"

    key = models.CharField(max_length=64, unique=True)
    method = models.CharField(
        max_length=24, choices=Method.choices, default=Method.IMAGE_ASSET
    )
    # Bumped when the matching rule changes, so a link can always be traced to
    # the logic that made it and re-derived if that logic turns out to be wrong.
    algorithm_version = models.IntegerField(default=1)
    evidence = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "history_vehicleidentity"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"vehicle {self.key[:12]} ({self.method})"


class ListingEpisode(models.Model):
    """One continuous period during which one listing code was on the feed.

    ``Ad`` is a mutable current snapshot — it is overwritten on every observation
    and remembers only the latest state. An episode is the permanent record, and
    it is the unit every lifecycle question is really asked about: how long did
    this take to sell, how often has this car been relisted, how much inventory
    is genuinely distinct.

    Without episodes a repost reads as one new ad plus one removal, which
    restarts the tenure clock and double-counts a delisting — so every survival
    curve is biased toward "sells fast".
    """

    ad = models.ForeignKey("core.Ad", on_delete=models.CASCADE, related_name="episodes")
    identity = models.ForeignKey(
        VehicleIdentity, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="episodes",
    )
    started_at = models.DateTimeField(db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    first_price = models.BigIntegerField(null=True, blank=True)
    last_price = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "history_listingepisode"
        ordering = ("ad", "started_at")
        constraints = [
            # At most one open episode per ad: two would make "is this listing
            # live?" ambiguous and double-count the ad in every inventory query.
            models.UniqueConstraint(
                fields=("ad",), condition=models.Q(ended_at__isnull=True),
                name="uq_episode_one_open_per_ad",
            ),
        ]
        indexes = [
            models.Index(fields=("identity", "started_at"), name="episode_ident_idx"),
        ]

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    def __str__(self) -> str:
        return f"{self.ad_id} {self.started_at:%Y-%m-%d}..{self.ended_at or 'open'}"


class JobRun(models.Model):
    """One row per scheduled step, whatever the outcome.

    ``FetchRun`` records fetches and nothing else, so every other step — removal
    marking, snapshots, the market index, deal scores, gap repair, alerts, the
    digest — left no trace but stdout. Whether last night's snapshot ran at all
    was a question answerable only by reading container logs, and the admin
    trigger endpoints spawned threads that recorded nothing whatsoever.

    ``SKIPPED`` is a first-class outcome and the reason this table is worth
    having: a step that never ran because its prerequisite failed is a different
    fact from one that ran and worked, and the two are indistinguishable in a
    world where only failures get logged.
    """

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        OK = "ok", "Succeeded"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped (prerequisite failed)"

    class Trigger(models.TextChoices):
        SCHEDULER = "scheduler", "Scheduled worker"
        ADMIN = "admin", "Admin endpoint"
        MANUAL = "manual", "Manual invocation"

    name = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, db_index=True)
    triggered_by = models.CharField(
        max_length=16, choices=Trigger.choices, default=Trigger.SCHEDULER
    )
    attempt = models.IntegerField(default=1)
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_s = models.FloatField(null=True, blank=True)
    # Captured stdout — the step's own account of what it did, which is where the
    # row counts live. Kept as text rather than parsed into columns because every
    # command reports differently and none of them promise a format.
    detail = models.TextField(blank=True)
    error = models.TextField(blank=True)

    class Meta:
        db_table = "history_jobrun"
        ordering = ("-started_at",)
        indexes = [
            # "how did each step do lately", the operations timeline query.
            models.Index(fields=("name", "-started_at"), name="jobrun_name_time_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} {self.status} @ {self.started_at:%Y-%m-%d %H:%M}"


class DataQualitySnapshot(models.Model):
    """One row per day describing the *shape* of the ingested data.

    The verification rules judge individual ads against fixed expectations, which
    means they can only catch what was anticipated when the rule was written. When
    Bama renames a field or changes a title format, no rule necessarily fires —
    the data simply becomes quietly emptier or stranger, and every statistic built
    on it drifts. What gives that away is not any single row but the distribution:
    a null rate that jumps, a field that loses half its distinct values, a spike
    in newly-minted catalog rows.

    Stored rather than computed on demand because drift is only visible against
    history, and the comparison is to this table's own trailing window.
    """

    date = models.DateField(primary_key=True)
    computed_at = models.DateTimeField(auto_now=True)

    total_ads = models.IntegerField(default=0)
    active_ads = models.IntegerField(default=0)
    priced_ads = models.IntegerField(default=0)

    # {rule_id: count} over the active population, for quality_flags and
    # cohort_flags respectively.
    flag_counts = models.JSONField(default=dict, blank=True)
    cohort_flag_counts = models.JSONField(default=dict, blank=True)
    # {field: fraction 0..1} — the most direct signal that a payload key moved.
    null_rates = models.JSONField(default=dict, blank=True)
    # {field: n} — a collapse here means a field stopped being populated
    # meaningfully even though it is not null.
    distinct_counts = models.JSONField(default=dict, blank=True)

    rejects_today = models.IntegerField(default=0)
    unconfirmed_brands = models.IntegerField(default=0)
    unconfirmed_models = models.IntegerField(default=0)

    # Robust price statistics: percentiles rather than mean/stddev so one absurd
    # listing cannot move the number that is supposed to reveal absurd listings.
    price_p10 = models.BigIntegerField(null=True, blank=True)
    price_median = models.BigIntegerField(null=True, blank=True)
    price_p90 = models.BigIntegerField(null=True, blank=True)

    # [{metric, value, baseline, deviation}] as judged against the trailing
    # window at the time this row was written.
    alarms = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "history_dataqualitysnapshot"
        ordering = ("-date",)

    def __str__(self) -> str:
        return f"{self.date} ({len(self.alarms)} alarm(s))"


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
