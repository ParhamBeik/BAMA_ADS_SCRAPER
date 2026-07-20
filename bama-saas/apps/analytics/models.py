"""Analytics models: precomputed price statistics + a generic result cache."""

import uuid

from django.db import models


class PriceStatistics(models.Model):
    """Aggregated price stats per market slice (refreshed by a command)."""

    brand = models.ForeignKey(
        "catalog.Brand", on_delete=models.SET_NULL, related_name="stats",
        null=True, blank=True,
    )
    model = models.ForeignKey(
        "catalog.Model", on_delete=models.SET_NULL, related_name="stats",
        null=True, blank=True,
    )
    variant = models.ForeignKey(
        "catalog.Variant", on_delete=models.SET_NULL, related_name="stats",
        null=True, blank=True,
    )
    year = models.IntegerField(null=True, blank=True)
    time_window = models.CharField(max_length=16, default="all")  # all / 30d / 90d / 365d

    mean = models.FloatField(null=True, blank=True)
    median = models.FloatField(null=True, blank=True)
    std_dev = models.FloatField(null=True, blank=True)
    min_price = models.BigIntegerField(null=True, blank=True)
    max_price = models.BigIntegerField(null=True, blank=True)
    count = models.IntegerField(default=0)

    calculated_at = models.DateTimeField(null=True, blank=True)
    refreshed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "analytics_pricestatistics"
        ordering = ("brand", "model", "variant", "year")
        constraints = [
            models.UniqueConstraint(
                fields=("brand", "model", "variant", "year", "time_window"),
                name="uq_stats_market_window",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.brand_id}/{self.model_id}/{self.year}/{self.time_window}"


class AnalyticsCache(models.Model):
    """Generic keyed cache for derived series (e.g. Bollinger bands)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    metric_key = models.CharField(max_length=255, unique=True)
    payload = models.JSONField()
    expires_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "analytics_cache"

    def __str__(self) -> str:
        return self.metric_key


class DailyInventorySnapshot(models.Model):
    """One row per (model, variant, year, date) — the inventory-trend backbone.

    Refreshed daily (idempotently) by the worker's ``daily_snapshot`` command
    from publish-complete, priced, ACTIVE ads. Powers inventory-count and
    median-price-over-time charts and growth/decline deltas vs the prior day.
    """

    model = models.ForeignKey(
        "catalog.Model", on_delete=models.SET_NULL, related_name="daily_snapshots",
        null=True, blank=True,
    )
    variant = models.ForeignKey(
        "catalog.Variant", on_delete=models.SET_NULL, related_name="daily_snapshots",
        null=True, blank=True,
    )
    year = models.IntegerField(null=True, blank=True)
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
                fields=("model", "variant", "year", "date"),
                name="uq_snapshot_market_date",
            ),
        ]
        indexes = [
            models.Index(fields=("model", "date"), name="snap_model_date_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.model_id}/{self.year}/{self.date} ({self.ad_count})"


class DealScoreCache(models.Model):
    """Per-ad deal score, refreshed by the ``compute_deal_scores`` command.

    Score (0–100) is how far below its peer median an ad sits, blunted by
    listing age so a stale "cheap" car does not top the board. ``components``
    keeps the breakdown (discount_pct, peer_median, age_days, liquidity) so the
    UI can show *why* an ad scored well without recomputing. One row per ad.
    """

    ad = models.OneToOneField(
        "catalog.Ad", on_delete=models.CASCADE, related_name="deal_score"
    )
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


class MarketSnapshot(models.Model):
    """One row per date — the whole-market daily rollup.

    Where ``DailyInventorySnapshot`` slices per (model, variant, year), this is
    the single global inventory + price-distribution series that powers the
    market-overview chart (total stock, new/removed today, overall median).
    Refreshed daily (idempotently) by the ``market_snapshot`` command.
    """

    date = models.DateField(unique=True)
    active_count = models.IntegerField(default=0)
    new_count = models.IntegerField(default=0)      # ads first seen on `date`
    removed_count = models.IntegerField(default=0)  # ads removed on `date`
    median_price = models.BigIntegerField(null=True, blank=True)
    mean_price = models.BigIntegerField(null=True, blank=True)
    min_price = models.BigIntegerField(null=True, blank=True)
    max_price = models.BigIntegerField(null=True, blank=True)
    brand_breakdown = models.JSONField(default=dict, blank=True)
    calculated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "analytics_marketsnapshot"
        ordering = ("-date",)

    def __str__(self) -> str:
        return f"{self.date} ({self.active_count} active)"
