"""Catalog dimension tables and the Ad current-snapshot fact row.

Design (see project plan, "Normalized model schema"):
- Brand → Model → Variant are normalized lookup tables with FKs.
- Ad is the current-snapshot row keyed by Bama `code`, carrying a few hot
  denormalized columns (year/mileage/current_price/publish_at) for fast
  range/sort plus the full `raw_payload` JSONB for the long tail.
- Indexes target the hot query paths: per-market grouping, price-range scans,
  and JSONB ad-hoc containment.
"""

from django.contrib.postgres.indexes import GinIndex
from django.db import models


class Brand(models.Model):
    """A vehicle make (e.g. پژو). `slug` is a stable ASCII PK."""

    slug = models.SlugField(max_length=160, primary_key=True, allow_unicode=True)
    name_fa = models.CharField(max_length=120, unique=True)
    name_en = models.CharField(max_length=120, null=True, blank=True)
    aliases = models.JSONField(default=list, blank=True)
    # Brands are parsed out of free-text ad titles, so a Bama format change would
    # otherwise invent catalog rows in silence and every cohort keyed on them would
    # be wrong. Rows minted by ingestion land unconfirmed and are reviewed; the set
    # of car brands sold in Iran is nearly fixed, so a genuinely new one is rare
    # enough to be worth a human glance.
    is_confirmed = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "catalog_brand"
        ordering = ("name_fa",)

    def __str__(self) -> str:
        return self.name_fa


class Model(models.Model):
    brand = models.ForeignKey(
        Brand, on_delete=models.CASCADE, related_name="models"
    )
    name_fa = models.CharField(max_length=160)
    # Same rationale as Brand.is_confirmed. Variant is deliberately left out: trim
    # names vary legitimately and constantly, so flagging them would be noise.
    is_confirmed = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "catalog_model"
        ordering = ("brand__name_fa", "name_fa")
        constraints = [
            models.UniqueConstraint(
                fields=("brand", "name_fa"), name="uq_model_brand_name"
            ),
        ]
        # brand_id index is created automatically by the FK.

    def __str__(self) -> str:
        return self.name_fa


class Variant(models.Model):
    model = models.ForeignKey(
        Model, on_delete=models.CASCADE, related_name="variants"
    )
    name_fa = models.CharField(max_length=200, default="default")

    class Meta:
        db_table = "catalog_variant"
        ordering = ("model__name_fa", "name_fa")
        constraints = [
            models.UniqueConstraint(
                fields=("model", "name_fa"), name="uq_variant_model_name"
            ),
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
    """Bama dealer. PK is the Bama dealer id (BigInteger)."""

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
    """Current snapshot of a single Bama ad, keyed by its `code`."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REMOVED = "removed", "Removed (absent from the last completed sweeps)"

    class YearCalendar(models.TextChoices):
        JALALI = "jalali", "Jalali"
        GREGORIAN = "gregorian", "Gregorian"
        UNKNOWN = "unknown", "Unknown"

    code = models.CharField(max_length=16, primary_key=True)

    # Dimension links. PROTECT prevents deleting a brand/model/variant that
    # ads still reference; dealer/city just clear on deletion.
    brand = models.ForeignKey(
        Brand, on_delete=models.PROTECT, related_name="ads", null=True, blank=True
    )
    model = models.ForeignKey(
        Model, on_delete=models.PROTECT, related_name="ads", null=True, blank=True
    )
    variant = models.ForeignKey(
        Variant, on_delete=models.PROTECT, related_name="ads", null=True, blank=True
    )
    dealer = models.ForeignKey(
        Dealer, on_delete=models.SET_NULL, related_name="ads", null=True, blank=True
    )
    city = models.ForeignKey(
        City, on_delete=models.SET_NULL, related_name="ads", null=True, blank=True
    )

    # Hot denormalized columns for fast filter/sort/peer-grouping.
    title = models.CharField(max_length=400, blank=True)

    # `year` is the raw value Bama sent, kept verbatim for provenance. Bama
    # publishes model years in EITHER calendar depending on the brand (and some
    # brands use both), so `year` alone mixes 1399 and 2025 in one column and is
    # NOT safe to group or range-filter on. `year_jalali` is the canonical
    # cohort key; `year_gregorian` is the same value in the other calendar.
    year = models.IntegerField(null=True, blank=True, db_index=True)
    year_jalali = models.IntegerField(null=True, blank=True, db_index=True)
    year_gregorian = models.IntegerField(null=True, blank=True)
    year_calendar = models.CharField(
        max_length=16, choices=YearCalendar.choices, blank=True
    )

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

    # Lifecycle: ACTIVE while observed, REMOVED once absent from the last two
    # *completed* sweeps (set by the worker's mark_inactive_ads command — a
    # coverage proof, not a wall-clock guess). Re-seeing a removed ad flips it
    # back to ACTIVE (ingest_ad clears these on update).
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    removed_at = models.DateTimeField(null=True, blank=True)

    # Long-tail attributes (kept on the snapshot for convenience; source of
    # truth is raw_payload).
    trim = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=200, blank=True)
    body_type = models.CharField(max_length=120, blank=True)
    body_color = models.CharField(max_length=120, blank=True)
    body_status = models.CharField(max_length=120, blank=True)
    fuel = models.CharField(max_length=64, blank=True)

    # Listing-presentation fields, promoted out of raw_payload because they are
    # the evidence behind an outlier explanation: "priced far under its cohort,
    # one photo, a two-line description, unverified seller" is an answer, whereas
    # the price alone is only a number. They also feed the confidence tier on a
    # fair-price estimate.
    #
    # Only these four are promoted. The payload also carries engine displacement,
    # battery capacity, range and promotion state, which belong to insight
    # families that are not being built — leaving them in the JSONB costs nothing
    # and they can be promoted when something actually reads them.
    image_count = models.IntegerField(null=True, blank=True)
    description_length = models.IntegerField(null=True, blank=True)
    seller_authenticated = models.BooleanField(null=True, blank=True)
    # The source's own last-modified timestamp. Excluded from the semantic hash
    # (it moves without the ad changing) but worth keeping: it is the only signal
    # of seller activity that does not depend on us having observed the change.
    source_modified_at = models.DateTimeField(null=True, blank=True)
    url = models.URLField(max_length=500, blank=True)
    canonical_path = models.CharField(max_length=400, blank=True)

    raw_payload = models.JSONField(null=True, blank=True)

    # Failed verification rules (see apps/jobs/services/verify.py). Empty list
    # means the row passed every rule. Analytics must exclude non-empty via
    # apps.core.services.quality.verified() so bad rows never reach a stat.
    quality_flags = models.JSONField(default=list, blank=True)
    # Verdicts from the cohort pass (apps/jobs/services/verify_cohort.py), kept
    # apart from quality_flags on purpose: quality_flags is recomputed from the
    # payload on every single observation, so a flag written by an out-of-band
    # pass would be erased within one fetch tick. These are also a different kind
    # of statement — quality_flags judges the row, this judges the row *against
    # its peers*, and only the second one changes when the peers change.
    cohort_flags = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "catalog_ad"
        ordering = ("-publish_at",)
        indexes = [
            # Peer grouping for true-mean / Bollinger per model+variant+year.
            # Kept for the raw column; cohort queries use ad_market_jy_idx.
            models.Index(
                fields=("model", "variant", "year"), name="ad_market_idx"
            ),
            # The real cohort key: calendar-normalized model year.
            models.Index(
                fields=("model", "variant", "year_jalali"), name="ad_market_jy_idx"
            ),
            # Cheap "is this row clean?" scans and quarantine-rate monitoring.
            GinIndex(fields=["quality_flags"], name="ad_quality_gin"),
            # Every statistical baseline excludes cohort outliers, so this
            # containment check sits on the hot path of all of them.
            GinIndex(fields=["cohort_flags"], name="ad_cohort_gin"),
            # Price-range scan within a model: WHERE model_id=? AND price BETWEEN.
            models.Index(
                fields=("model", "current_price"), name="ad_market_price_idx"
            ),
            # Ad-hoc JSONB containment queries over the long tail.
            GinIndex(
                fields=["raw_payload"],
                opclasses=["jsonb_path_ops"],
                name="ad_raw_gin",
            ),
        ]

    def __str__(self) -> str:
        return self.code
