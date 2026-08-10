"""Analytics models: daily cohort snapshots, the market index, deal scores, and
the whole-market rollup.

``PriceStatistics`` used to live here and has been removed. It was rebuilt on a
schedule and read by nothing — no view, no serializer, no service — so it was
pure write cost plus a standing invitation to build on numbers nobody validated.
"""

from django.db import models


class DailyInventorySnapshot(models.Model):
    """One row per (model, variant, year_jalali, date) — the cohort backbone.

    Refreshed daily (idempotently) by the worker's ``daily_snapshot`` command
    from publish-complete, priced, ACTIVE, ``verified()`` ads. Powers
    inventory-count and median-price-over-time charts, growth/decline deltas vs
    the prior day, and — the reason the cohort key matters — the matched-cohort
    market index in ``apps/core/services/index.py``.

    The cohort key is ``year_jalali``, never the raw ``Ad.year``. Bama publishes
    model years in either calendar depending on brand, so a ``year``-keyed
    snapshot split each real cohort into two half-populated rows with two wrong
    medians, and the index could not match a cohort across consecutive days at
    all. The column was renamed rather than merely repurposed so the two
    meanings can never be silently mixed in one series.
    """

    model = models.ForeignKey(
        "core.Model", on_delete=models.SET_NULL, related_name="daily_snapshots",
        null=True, blank=True,
    )
    variant = models.ForeignKey(
        "core.Variant", on_delete=models.SET_NULL, related_name="daily_snapshots",
        null=True, blank=True,
    )
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
            # The index walks day-by-day over one cohort: (cohort..., date).
            models.Index(
                fields=("model", "variant", "year_jalali", "date"),
                name="snap_cohort_date_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.model_id}/{self.year_jalali}/{self.date} ({self.ad_count})"


class DealScoreCache(models.Model):
    """Per-ad deal score, refreshed by the ``compute_deal_scores`` command.

    Score (0–100) is how far below its peer median an ad sits, blunted by
    listing age so a stale "cheap" car does not top the board. ``components``
    keeps the breakdown (discount_pct, peer_median, age_days, liquidity) so the
    UI can show *why* an ad scored well without recomputing. One row per ad.
    """

    ad = models.OneToOneField(
        "core.Ad", on_delete=models.CASCADE, related_name="deal_score"
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


class MarketIndex(models.Model):
    """Chained, composition-controlled price index — one row per (scope, date).

    The raw market median answers "what does a car cost today", which is not the
    same question as "did prices move". If cheap models flood the feed the median
    falls while no individual car changed price; that is a *mix* change, and the
    live data shows exactly this — inventory grew 21.7k → 33.7k over eight days
    while the median fell 4.4%, with no way to tell how much of the fall was real.

    This index removes the mix effect by never comparing different cars. Each day
    it measures the price change *within* each (model, variant, year_jalali)
    cohort, then averages those changes weighted by cohort size and chains the
    result onto the previous day. A cohort that appears or disappears changes the
    weights but contributes no return, so composition cannot move the index on
    its own.

    ``index_value`` is 100 at the first date with data. ``return_pct`` is that
    day's aggregate move; ``cohort_count`` / ``ad_count`` are the sample behind
    it, so a reader can tell a genuine 2% move from one computed off three cars.
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
            models.UniqueConstraint(
                fields=("scope", "scope_id", "date"), name="uq_index_scope_date"
            ),
        ]
        indexes = [
            models.Index(fields=("scope", "scope_id", "date"), name="idx_scope_date"),
        ]

    def __str__(self) -> str:
        return f"{self.scope}:{self.scope_id or '*'}@{self.date} = {self.index_value:.2f}"


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
