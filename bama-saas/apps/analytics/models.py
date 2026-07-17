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
