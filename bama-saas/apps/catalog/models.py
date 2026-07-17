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
    year = models.IntegerField(null=True, blank=True, db_index=True)
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

    # Long-tail attributes (kept on the snapshot for convenience; source of
    # truth is raw_payload).
    trim = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=200, blank=True)
    body_type = models.CharField(max_length=120, blank=True)
    body_color = models.CharField(max_length=120, blank=True)
    body_status = models.CharField(max_length=120, blank=True)
    fuel = models.CharField(max_length=64, blank=True)
    url = models.URLField(max_length=500, blank=True)
    canonical_path = models.CharField(max_length=400, blank=True)

    raw_payload = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "catalog_ad"
        ordering = ("-publish_at",)
        indexes = [
            # Peer grouping for true-mean / Bollinger per model+variant+year.
            models.Index(
                fields=("model", "variant", "year"), name="ad_market_idx"
            ),
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
