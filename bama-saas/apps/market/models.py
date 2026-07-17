"""Market fact tables.

`PriceObservation` is the price-through-time backbone. It is change-only:
one row per actual price change (not per sighting) so the Bollinger / true-mean
series are clean. Dedup against the ad's immediately-preceding observation is
performed by the importer, not by a DB constraint (a price can return to a
prior value later, which would collide under a unique constraint).
"""

from django.db import models


class PriceObservation(models.Model):
    ad = models.ForeignKey(
        "catalog.Ad", on_delete=models.CASCADE, related_name="price_observations"
    )
    fetch_run = models.ForeignKey(
        "history.FetchRun",
        on_delete=models.CASCADE,
        related_name="price_observations",
        null=True,
        blank=True,
    )
    observed_at = models.DateTimeField(db_index=True)
    price = models.BigIntegerField(null=True, blank=True)
    payment = models.BigIntegerField(null=True, blank=True)
    prepayment = models.BigIntegerField(null=True, blank=True)
    installments = models.IntegerField(null=True, blank=True)
    price_type = models.CharField(max_length=64, blank=True)
    fingerprint = models.CharField(max_length=64, db_index=True)

    class Meta:
        db_table = "market_priceobservation"
        ordering = ("observed_at",)
        indexes = [
            # Per-ad price history (the single-ad price-history endpoint).
            models.Index(fields=("ad", "observed_at"), name="po_ad_time_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.ad_id} @ {self.observed_at.date()} = {self.price}"
