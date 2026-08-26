"""Every table in the app, in four groups: catalog, provenance, price, analytics.

``db_table`` is pinned on all of them, so the physical schema is independent of
how the Python is organised.
"""

import uuid

from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Q
from django.utils import timezone

# ===========================================================================
# Catalog — the dimensions, and the current snapshot of one ad
# ===========================================================================


class Brand(models.Model):
    """A vehicle make (e.g. پژو). ``slug`` is a stable ASCII PK."""

    slug = models.SlugField(max_length=160, primary_key=True, allow_unicode=True)
    name_fa = models.CharField(max_length=120, unique=True)
    name_en = models.CharField(max_length=120, null=True, blank=True)
    aliases = models.JSONField(default=list, blank=True)
    # Brands are parsed out of free-text titles, so a Bama format change would
    # otherwise invent catalog rows in silence and every cohort keyed on them
    # would be wrong. Rows minted by ingestion land unconfirmed; the set of car
    # brands sold in Iran is nearly fixed, so a genuinely new one is rare enough
    # to be worth a human glance.
    is_confirmed = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "catalog_brand"
        ordering = ("name_fa",)

    def __str__(self) -> str:
        return self.name_fa


class Model(models.Model):
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="models")
    name_fa = models.CharField(max_length=160)
    is_confirmed = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "catalog_model"
        ordering = ("brand__name_fa", "name_fa")
        constraints = [
            models.UniqueConstraint(fields=("brand", "name_fa"), name="uq_model_brand_name"),
        ]

    def __str__(self) -> str:
        return self.name_fa


class Variant(models.Model):
    # No is_confirmed here on purpose: trim names vary legitimately and
    # constantly, so flagging them would be pure noise.
    model = models.ForeignKey(Model, on_delete=models.CASCADE, related_name="variants")
    name_fa = models.CharField(max_length=200, default="default")

    class Meta:
        db_table = "catalog_variant"
        ordering = ("model__name_fa", "name_fa")
        constraints = [
            models.UniqueConstraint(fields=("model", "name_fa"), name="uq_variant_model_name"),
        ]

    def __str__(self) -> str:
        return self.name_fa


class City(models.Model):
    name_fa = models.CharField(max_length=120, unique=True)
    province = models.CharField(max_length=120, null=True, blank=True)

    class Meta:
        db_table = "catalog_city"
        ordering = ("name_fa",)

    def __str__(self) -> str:
        return self.name_fa


class Dealer(models.Model):
    """PK is Bama's own dealer id."""

    id = models.BigIntegerField(primary_key=True)
    name = models.CharField(max_length=255, blank=True)
    type = models.CharField(max_length=64, blank=True)
    package_type = models.CharField(max_length=64, blank=True)
    score = models.FloatField(null=True, blank=True)
    ad_count = models.IntegerField(null=True, blank=True)
    address = models.TextField(blank=True)
    link = models.URLField(max_length=500, blank=True)
    logo = models.URLField(max_length=500, blank=True)

    class Meta:
        db_table = "catalog_dealer"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name or f"Dealer {self.id}"


class Ad(models.Model):
    """Current snapshot of one Bama ad, keyed by its ``code``.

    Mutable: overwritten on every observation. The permanent records are
    ``AdVersion`` (content) and ``ListingEpisode`` (lifecycle). Hot columns are
    denormalised for filter/sort/cohort grouping; the long tail stays in
    ``raw_payload``.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REMOVED = "removed", "Delisted (absent from two complete sweeps)"
        # The state that used to be told as a lie. An ad we have not seen for
        # days, during a stretch where coverage could not be proven complete, is
        # not evidence of anything — but it stayed ACTIVE, so the app claimed
        # 546 cars were for sale that nobody had laid eyes on in 48 hours.
        UNVERIFIED = "unverified", "Unverified (not seen, coverage incomplete)"

    class Reason(models.TextChoices):
        """Why an ad probably left. Inferred, never observed.

        Bama's feed carries no delisting reason — an ad simply stops appearing.
        Keeping this out of ``status`` is the point: the status column states
        what we saw, this column states what we guess, and a reader can tell
        which is which.
        """

        SOLD = "likely_sold", "Likely sold"
        EXPIRED = "likely_expired", "Likely expired"
        REPOSTED = "reposted", "Relisted under a new code"
        UNKNOWN = "unknown", "Unknown"

    class Confidence(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    class YearCalendar(models.TextChoices):
        JALALI = "jalali", "Jalali"
        GREGORIAN = "gregorian", "Gregorian"
        UNKNOWN = "unknown", "Unknown"

    code = models.CharField(max_length=16, primary_key=True)

    # PROTECT stops a brand/model/variant still referenced by ads from being
    # deleted; dealer/city just clear.
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name="ads",
                              null=True, blank=True)
    model = models.ForeignKey(Model, on_delete=models.PROTECT, related_name="ads",
                              null=True, blank=True)
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name="ads",
                                null=True, blank=True)
    dealer = models.ForeignKey(Dealer, on_delete=models.SET_NULL, related_name="ads",
                               null=True, blank=True)
    city = models.ForeignKey(City, on_delete=models.SET_NULL, related_name="ads",
                             null=True, blank=True)

    title = models.CharField(max_length=400, blank=True)

    # `year` is what Bama sent, kept verbatim for provenance. Bama publishes
    # model years in EITHER calendar depending on brand, so `year` alone mixes
    # 1399 and 2025 in one column and is NOT safe to group or range-filter on.
    # `year_jalali` is the canonical cohort key.
    year = models.IntegerField(null=True, blank=True, db_index=True)
    year_jalali = models.IntegerField(null=True, blank=True, db_index=True)
    year_gregorian = models.IntegerField(null=True, blank=True)
    year_calendar = models.CharField(max_length=16, choices=YearCalendar.choices, blank=True)

    mileage = models.BigIntegerField(null=True, blank=True, db_index=True)
    category = models.CharField(max_length=120, blank=True, db_index=True)
    transmission = models.CharField(max_length=64, blank=True, db_index=True)

    current_price = models.BigIntegerField(null=True, blank=True, db_index=True)
    current_payment = models.BigIntegerField(null=True, blank=True)
    current_prepayment = models.BigIntegerField(null=True, blank=True)
    current_installments = models.IntegerField(null=True, blank=True)
    price_type = models.CharField(max_length=64, blank=True)

    publish_at = models.DateTimeField(null=True, blank=True, db_index=True)
    publish_phrase = models.CharField(max_length=120, blank=True)
    first_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # ACTIVE while observed; REMOVED once absent from two consecutive complete
    # coverage windows (a proof, not a wall-clock guess — see jobs.pipeline);
    # UNVERIFIED when it has not been seen for that long but coverage was too
    # patchy to prove anything. Re-seeing an ad in any state flips it back.
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    removed_at = models.DateTimeField(null=True, blank=True)

    # The inference about *why*, kept apart from the fact of absence above.
    likely_reason = models.CharField(max_length=24, choices=Reason.choices,
                                     blank=True, db_index=True)
    reason_confidence = models.CharField(max_length=8, choices=Confidence.choices,
                                         blank=True)
    # Set on the NEW ad, pointing at the one it replaced. SET_NULL rather than
    # CASCADE: losing the predecessor must not delete the live listing.
    reposted_from = models.ForeignKey("self", on_delete=models.SET_NULL,
                                      related_name="reposts", null=True, blank=True)
    # Content identity across ad codes — how a relist is recognised when Bama
    # issues a fresh code for the same car. See jobs.parsing.listing_fingerprint.
    listing_fingerprint = models.CharField(max_length=64, blank=True, db_index=True)

    trim = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=200, blank=True)
    body_type = models.CharField(max_length=120, blank=True)
    body_color = models.CharField(max_length=120, blank=True)
    body_status = models.CharField(max_length=120, blank=True)
    fuel = models.CharField(max_length=64, blank=True)

    # Promoted out of raw_payload because they are the evidence behind an
    # outlier explanation: "priced far under its cohort, one photo, a two-line
    # description, unverified seller" is an answer; the price alone is a number.
    image_count = models.IntegerField(null=True, blank=True)
    description = models.TextField(blank=True, default="")
    primary_image_url = models.URLField(max_length=500, blank=True, default="")
    image_urls = models.JSONField(default=list, blank=True)
    description_length = models.IntegerField(null=True, blank=True)
    seller_authenticated = models.BooleanField(null=True, blank=True)
    # The source's own last-modified stamp. Excluded from the semantic hash (it
    # moves without the ad changing) but the only signal of seller activity that
    # does not depend on us having observed the change.
    source_modified_at = models.DateTimeField(null=True, blank=True)
    url = models.URLField(max_length=500, blank=True)
    canonical_path = models.CharField(max_length=400, blank=True)

    raw_payload = models.JSONField(null=True, blank=True)

    # Verification rules this row failed (apps/jobs/verify.py); empty == clean.
    # Recomputed from the payload on every observation.
    quality_flags = models.JSONField(default=list, blank=True)
    # Verdicts from the cohort pass, kept apart from quality_flags precisely
    # because those are recomputed every tick and would erase these. Also a
    # different kind of statement: quality_flags judges the row, this judges the
    # row *against its peers*, and only the second changes when the peers do.
    cohort_flags = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "catalog_ad"
        ordering = ("-publish_at",)
        indexes = [
            models.Index(fields=("model", "variant", "year"), name="ad_market_idx"),
            # The real cohort key: calendar-normalised model year.
            models.Index(fields=("model", "variant", "year_jalali"), name="ad_market_jy_idx"),
            GinIndex(fields=["quality_flags"], name="ad_quality_gin"),
            GinIndex(fields=["cohort_flags"], name="ad_cohort_gin"),
            models.Index(fields=("model", "current_price"), name="ad_market_price_idx"),
            GinIndex(fields=["raw_payload"], opclasses=["jsonb_path_ops"], name="ad_raw_gin"),
            # The browse-list predicate. GIN accelerates positive `@>` only, not
            # the NOT-containment exclusions quality.py applies on top, so
            # pagination's .count() was sequential-scanning the whole table on
            # every request. This narrows that scan first.
            models.Index(
                fields=("status", "publish_at"),
                name="ad_list_active_idx",
                condition=Q(current_price__gt=0),
            ),
        ]

    def __str__(self) -> str:
        return self.code


# ===========================================================================
# Provenance — append-only; what we fetched, when, and what we refused
# ===========================================================================


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
        # A confirmed empty page that was too shallow to be believed as the end
        # of the feed. Distinct from MAX_PAGES because it is an *observation*,
        # not a budget: `end_is_corroborated` counts these rows to decide when
        # a persistent disagreement has earned the right to lower the ratchet.
        END_UNCONFIRMED = "end_unconfirmed", "Empty page too shallow to believe"
        ERROR = "error", "Aborted on an unrecoverable error"
        INTERRUPTED = "interrupted", "Interrupted by the operator"
        # Distinct from ERROR because it is a policy decision by the source, not
        # a fault: it must drive a cooldown rather than a retry.
        BLOCKED = "blocked", "Refused by the source's WAF/CDN (403)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(max_length=32, choices=Source.choices,
                              default=Source.BULK_IMPORT, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices,
                              default=Status.QUEUED, db_index=True)
    max_ads = models.IntegerField(null=True, blank=True)
    page_pause = models.FloatField(null=True, blank=True)

    # Coverage state. Without these, "did this run cover the feed?" is
    # unanswerable. `deepest_rank` is the highest rank observed; `reached_end`
    # means we saw the empty page past the last ad.
    mode = models.CharField(max_length=16, choices=Mode.choices,
                            default=Mode.DELTA, db_index=True)
    start_page = models.IntegerField(default=0)
    pages_fetched = models.IntegerField(default=0)
    deepest_rank = models.IntegerField(null=True, blank=True)
    reached_end = models.BooleanField(default=False)
    # Where the feed ended, when this run walked into the empty page past the
    # last ad: rank ``PAGE_SIZE * <empty page index>``. Deliberately NOT folded
    # into ``deepest_rank``, which means "the highest rank we actually saw" — a
    # backfill can prove the feed ends at 34,710 while observing no ads at all,
    # and the two numbers answer different questions. This is the only thing
    # allowed to lower the depth ratchet (see fetcher.known_feed_depth).
    feed_end_rank = models.IntegerField(null=True, blank=True)
    stop_reason = models.CharField(max_length=24, choices=StopReason.choices, blank=True)
    # Set when a run aborts mid-sweep, so the next one resumes instead of
    # restarting from page 0.
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
    """One fetched listing page: which rank range it held, and when.

    The crawl's ledger. Bama numbers ads by recency, so a page is an inclusive
    rank interval and "have we covered the feed?" becomes a query rather than a
    guess. Insertions push ads to higher ranks (harmless — we re-read them);
    deletions pull them to *lower* ranks, behind pages already read, which is
    the case that silently loses ads.
    """

    fetch_run = models.ForeignKey(FetchRun, on_delete=models.CASCADE, related_name="page_coverages")
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
            models.UniqueConstraint(fields=("fetch_run", "page_index"), name="uq_pagecov_run_page"),
        ]
        indexes = [
            models.Index(fields=("rank_lo", "fetched_at"), name="pagecov_rank_time_idx"),
            models.Index(fields=("fetched_at",), name="pagecov_time_idx"),
        ]

    def __str__(self) -> str:
        return f"page {self.page_index} ranks {self.rank_lo}-{self.rank_hi}"


class IngestReject(models.Model):
    """An ad that failed a hard rule — quarantined, never dropped.

    The payload is kept so a rule that turns out to be wrong stays replayable,
    and so a spike in one ``rule`` reads directly as "Bama changed their schema".
    """

    code = models.CharField(max_length=16, blank=True, db_index=True)
    rule = models.CharField(max_length=64, db_index=True)
    detail = models.TextField(blank=True)
    raw_payload = models.JSONField(null=True, blank=True)
    fetch_run = models.ForeignKey(FetchRun, on_delete=models.SET_NULL,
                                 related_name="rejects", null=True, blank=True)
    observed_at = models.DateTimeField()

    class Meta:
        db_table = "history_ingestreject"
        ordering = ("-observed_at",)
        indexes = [
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

    ad = models.ForeignKey(Ad, on_delete=models.CASCADE, related_name="versions")
    semantic_hash = models.CharField(max_length=64, db_index=True)
    raw_hash = models.CharField(max_length=64)
    # Which generation of the volatile-field rules produced semantic_hash. Two
    # hashes are only comparable within one version.
    semantic_hash_version = models.IntegerField(default=1, db_index=True)
    payload = models.JSONField(null=True, blank=True)
    origin = models.CharField(max_length=32, choices=Origin.choices, default=Origin.BULK_IMPORT)
    first_observed_at = models.DateTimeField()

    class Meta:
        db_table = "history_adversion"
        ordering = ("first_observed_at",)
        constraints = [
            models.UniqueConstraint(fields=("ad", "semantic_hash"),
                                    name="uq_adversion_ad_semantic"),
        ]

    def __str__(self) -> str:
        return f"{self.ad_id} {self.semantic_hash[:8]}"


class AdObservation(models.Model):
    """A single sighting of an ad within one run, pinned to a version."""

    ad = models.ForeignKey(Ad, on_delete=models.CASCADE, related_name="observations")
    fetch_run = models.ForeignKey(FetchRun, on_delete=models.CASCADE, related_name="observations")
    version = models.ForeignKey(AdVersion, on_delete=models.CASCADE, related_name="observations")
    observed_at = models.DateTimeField()
    raw_hash = models.CharField(max_length=64)
    publish_phrase = models.CharField(max_length=120, blank=True)
    rank = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = "history_adobservation"
        ordering = ("observed_at",)
        constraints = [
            models.UniqueConstraint(fields=("fetch_run", "ad"), name="uq_observation_run_ad"),
        ]
        indexes = [models.Index(fields=("ad", "observed_at"), name="obs_ad_time_idx")]

    def __str__(self) -> str:
        return f"{self.ad_id} in {self.fetch_run_id}"


class ListingEpisode(models.Model):
    """One continuous period during which a listing code was on the feed.

    The unit every lifecycle question is really about. Without episodes a repost
    reads as one new ad plus one removal, which restarts the tenure clock and
    double-counts a delisting — biasing every survival curve toward "sells fast".
    """

    ad = models.ForeignKey(Ad, on_delete=models.CASCADE, related_name="episodes")
    started_at = models.DateTimeField(db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    first_price = models.BigIntegerField(null=True, blank=True)
    last_price = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "history_listingepisode"
        ordering = ("ad", "started_at")
        constraints = [
            # Two open episodes would make "is this listing live?" ambiguous and
            # double-count the ad in every inventory query.
            models.UniqueConstraint(
                fields=("ad",), condition=models.Q(ended_at__isnull=True),
                name="uq_episode_one_open_per_ad",
            ),
        ]

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    def __str__(self) -> str:
        return f"{self.ad_id} {self.started_at:%Y-%m-%d}..{self.ended_at or 'open'}"


class JobRun(models.Model):
    """One row per scheduled step, whatever the outcome.

    ``FetchRun`` records fetches and nothing else, so every other step left no
    trace but stdout. ``SKIPPED`` is a first-class outcome and the reason this
    table is worth having: a step that never ran because its prerequisite failed
    is a different fact from one that ran and worked.
    """

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        OK = "ok", "Succeeded"
        FAILED = "failed", "Failed"
        # Also covers a gated crawl: CrawlBlocked is a back-off we chose,
        # not a fault, so it must not read as FAILED on the health page.
        SKIPPED = "skipped", "Skipped (prerequisite failed)"

    class Trigger(models.TextChoices):
        SCHEDULER = "scheduler", "Scheduled worker"
        ADMIN = "admin", "Admin endpoint"
        MANUAL = "manual", "Manual invocation"

    name = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, db_index=True)
    triggered_by = models.CharField(max_length=16, choices=Trigger.choices,
                                    default=Trigger.SCHEDULER)
    attempt = models.IntegerField(default=1)
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_s = models.FloatField(null=True, blank=True)
    # The step's own account of what it did. Free text because every step
    # reports differently and none of them promise a format.
    detail = models.TextField(blank=True)
    error = models.TextField(blank=True)

    class Meta:
        db_table = "history_jobrun"
        ordering = ("-started_at",)
        indexes = [models.Index(fields=("name", "-started_at"), name="jobrun_name_time_idx")]

    def __str__(self) -> str:
        return f"{self.name} {self.status} @ {self.started_at:%Y-%m-%d %H:%M}"


# ===========================================================================
# Price — the change-only price backbone
# ===========================================================================


class PriceObservation(models.Model):
    """One row per actual price *change*, not per sighting.

    Dedup is done by the importer rather than a DB constraint: a price can
    return to a prior value later, which a unique constraint would reject.
    """

    ad = models.ForeignKey(Ad, on_delete=models.CASCADE, related_name="price_observations")
    fetch_run = models.ForeignKey(FetchRun, on_delete=models.CASCADE,
                                  related_name="price_observations", null=True, blank=True)
    observed_at = models.DateTimeField(db_index=True)
    price = models.BigIntegerField(null=True, blank=True)
    payment = models.BigIntegerField(null=True, blank=True)
    prepayment = models.BigIntegerField(null=True, blank=True)
    installments = models.IntegerField(null=True, blank=True)
    price_type = models.CharField(max_length=64, blank=True)
    fingerprint = models.CharField(max_length=64, db_index=True)
    # Flags describing this *transition*, not the ad. The row is kept either
    # way: we know one of the two prices is wrong, not which.
    quality_flags = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "market_priceobservation"
        ordering = ("observed_at",)
        indexes = [models.Index(fields=("ad", "observed_at"), name="po_ad_time_idx")]

    def __str__(self) -> str:
        return f"{self.ad_id} @ {self.observed_at.date()} = {self.price}"


class PriceDropEvent(models.Model):
    """One row per detected price cut. Amount and percentage are denormalised so
    the "biggest drops" board sorts without re-deriving."""

    ad = models.ForeignKey(Ad, on_delete=models.CASCADE, related_name="price_drops")
    old_price = models.BigIntegerField(null=True, blank=True)
    new_price = models.BigIntegerField(null=True, blank=True)
    drop_amount = models.BigIntegerField(default=0, db_index=True)
    drop_pct = models.FloatField(default=0.0, db_index=True)
    observed_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "market_pricedropevent"
        ordering = ("-observed_at",)
        indexes = [
            models.Index(fields=("ad", "-observed_at"), name="pde_ad_time_idx"),
            models.Index(fields=("-drop_pct",), name="pde_drop_pct_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.ad_id} -{self.drop_pct}% @ {self.observed_at.date()}"


# ===========================================================================
# Analytics — derived, all rebuildable from the tables above
# ===========================================================================


class DailyInventorySnapshot(models.Model):
    """One row per (model, variant, year_jalali, date) — the cohort backbone.

    Rebuilt idempotently each day from publish-complete, priced, ACTIVE,
    verified ads, and the input to the matched-cohort market index.

    The cohort key is ``year_jalali``, never the raw ``Ad.year``: a year-keyed
    snapshot split each real cohort into two half-populated rows with two wrong
    medians, and no cohort could be matched across consecutive days at all.
    """

    model = models.ForeignKey("core.Model", on_delete=models.SET_NULL,
                              related_name="daily_snapshots", null=True, blank=True)
    variant = models.ForeignKey("core.Variant", on_delete=models.SET_NULL,
                                related_name="daily_snapshots", null=True, blank=True)
    year_jalali = models.IntegerField(null=True, blank=True)
    date = models.DateField(db_index=True)

    ad_count = models.IntegerField(default=0)
    new_count = models.IntegerField(default=0)  # ads first seen on `date`
    median_price = models.BigIntegerField(null=True, blank=True)
    mean_price = models.BigIntegerField(null=True, blank=True)
    min_price = models.BigIntegerField(null=True, blank=True)
    max_price = models.BigIntegerField(null=True, blank=True)

    calculated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "analytics_dailyinventorysnapshot"
        ordering = ("-date", "model")
        constraints = [
            models.UniqueConstraint(
                fields=("model", "variant", "year_jalali", "date"),
                name="uq_snapshot_market_date",
            ),
        ]
        indexes = [
            models.Index(fields=("model", "date"), name="snap_model_date_idx"),
            models.Index(fields=("model", "variant", "year_jalali", "date"),
                         name="snap_cohort_date_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.model_id}/{self.year_jalali}/{self.date} ({self.ad_count})"


class DealScoreCache(models.Model):
    """Per-ad deal score (0-100): how far below its cohort's fair value it sits.

    ``components`` keeps the breakdown so the UI can show *why* without
    recomputing. Dropped and rebuilt wholesale on every refresh.
    """

    ad = models.OneToOneField(Ad, on_delete=models.CASCADE, related_name="deal_score")
    score = models.FloatField(default=0.0, db_index=True)
    discount_pct = models.FloatField(null=True, blank=True)
    peer_median = models.BigIntegerField(null=True, blank=True)
    components = models.JSONField(default=dict, blank=True)
    calculated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "analytics_dealscorecache"
        ordering = ("-score",)

    def __str__(self) -> str:
        return f"{self.ad_id} score={self.score}"


class MarketIndex(models.Model):
    """Chained, composition-controlled price index — one row per (scope, date).

    ``index_value`` is 100 at the first date with data; ``return_pct`` is that
    day's aggregate move; ``cohort_count``/``ad_count`` are the sample behind it,
    so a reader can tell a genuine 2% move from one computed off three cars.
    See apps/core/research.py for the arithmetic and why it exists.
    """

    class Scope(models.TextChoices):
        MARKET = "market", "Whole market"
        BRAND = "brand", "Per brand"
        MODEL = "model", "Per model"

    scope = models.CharField(max_length=16, choices=Scope.choices)
    # Null for the market-wide series; Brand.slug or Model.pk as text otherwise,
    # so one table serves all three levels without three nullable FKs.
    scope_id = models.CharField(max_length=160, null=True, blank=True)
    date = models.DateField()

    index_value = models.FloatField()
    return_pct = models.FloatField(null=True, blank=True)
    cohort_count = models.IntegerField(default=0)
    ad_count = models.IntegerField(default=0)

    calculated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "analytics_marketindex"
        ordering = ("scope", "scope_id", "date")
        constraints = [
            models.UniqueConstraint(fields=("scope", "scope_id", "date"),
                                    name="uq_index_scope_date"),
        ]
        indexes = [models.Index(fields=("scope", "scope_id", "date"), name="idx_scope_date")]

    def __str__(self) -> str:
        return f"{self.scope}:{self.scope_id or '*'}@{self.date} = {self.index_value:.2f}"


class NotifierSettings(models.Model):
    """The one row that decides which deals are worth interrupting you for.

    A singleton, not a per-user rule table: this is a single-operator tool. The
    defaults are deliberately conservative — a notifier that fires on marginal
    deals gets muted, and a muted notifier is worth nothing.
    """

    SINGLETON_PK = 1

    id = models.PositiveSmallIntegerField(primary_key=True, default=SINGLETON_PK)
    enabled = models.BooleanField(default=False)

    # A deal must beat its cohort's fair value by this much...
    min_discount_pct = models.FloatField(default=20.0)
    # ...and the cohort must be big enough for that median to mean something.
    # Above pricing.MIN_PEERS (8), because a ping is an interruption.
    min_peers = models.PositiveIntegerField(default=15)

    # Optional scope. Empty = the whole market.
    price_min = models.BigIntegerField(null=True, blank=True)
    price_max = models.BigIntegerField(null=True, blank=True)
    model_ids = models.JSONField(default=list, blank=True)

    telegram_chat_id = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "analytics_notifiersettings"
        verbose_name_plural = "notifier settings"

    def __str__(self) -> str:
        state = "on" if self.enabled else "off"
        return f"notifier {state} (>={self.min_discount_pct}%, >={self.min_peers} peers)"

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_PK
        # Django force-inserts any unsaved instance whose pk has a default, so a
        # directly-constructed one would collide with row 1 rather than update
        # it. Clearing `adding` selects the update-then-insert path.
        self._state.adding = False
        return super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "NotifierSettings":
        return cls.objects.get_or_create(pk=cls.SINGLETON_PK)[0]


class NotifiedAd(models.Model):
    """One row per listing already sent, so nothing is announced twice.

    Keyed on the ad, not on (ad, run): a car that qualifies on twenty
    consecutive ticks is one piece of news. Never rebuilt with the deal-score
    cache, which is dropped wholesale and so can never be the memory of what was
    sent.
    """

    ad = models.OneToOneField(Ad, on_delete=models.CASCADE, related_name="notified")
    sent_at = models.DateTimeField(auto_now_add=True, db_index=True)
    discount_pct = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "analytics_notifiedad"
        ordering = ("-sent_at",)

    def __str__(self) -> str:
        return f"{self.ad_id} notified {self.sent_at:%Y-%m-%d %H:%M}"
