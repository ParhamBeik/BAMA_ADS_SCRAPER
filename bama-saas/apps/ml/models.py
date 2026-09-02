"""The registry, and the per-ad scores that come out of it.

Two tables, and the split between them is the deployment story.

``MLModel`` is one row per *trained artifact*: what it is, when it was fitted,
on how many rows, what it scored on a holdout, and — the load-bearing column —
whether it is ``ACTIVE``, ``SHADOW`` or ``RETIRED``. Promotion and rollback are
therefore a status update inside a transaction, not a file copy and a restart.
A model that loses to the incumbent stays SHADOW, keeps its metrics, and says so
on the Control page; nothing about that outcome is hidden, which is the whole
reason the status is a column rather than a filename convention.

``AdPrediction`` is one row per ad, holding whatever the active models had to
say about it. It exists for the same reason ``DealScoreCache.needs_review`` is a
real column and not a JSON key: the deal board filters and orders on these
values on every request, and a JSON path lookup has the wrong semantics for a
row written before the key existed (SQL's ``NOT NULL`` is not ``TRUE``).

`apps/core/pricing.py` records why this table is not allowed to quietly become
the product: a fitted model was tried here once before, scored r² 0.185, and
produced negative fair values and 148% "discounts". Nothing in here overwrites
the statistical answer — the peer median stays the number on the card, and these
sit beside it.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import Ad


class MLModel(models.Model):
    """One trained artifact and the evidence for whether it should be used.

    ``name`` is the *role* (``price``, ``sell_fast``, …) and many rows share it;
    ``version`` is monotonic within a name. At most one row per name may be
    ACTIVE, enforced by a partial unique constraint rather than by convention,
    because "which model is live" answered two different ways is how a rollback
    silently fails to roll anything back.
    """

    class Name(models.TextChoices):
        PRICE = "price", "Quantile price model (p10/p50/p90)"
        SELL_FAST = "sell_fast", "Probability of leaving the feed quickly"
        ANOMALY = "anomaly", "Isolation Forest over the feature space"
        MODEL_TEXT = "model_text", "Ad text to catalogue model"
        VALUE_TIER = "value_tier", "Per-variant value tiers"

    class Status(models.TextChoices):
        # Trained, scored, and running beside the incumbent without being read
        # by anything user-facing. The default, because a model has to earn the
        # other one.
        SHADOW = "shadow", "Shadow — scored but not served"
        ACTIVE = "active", "Active — served"
        RETIRED = "retired", "Retired — superseded or rolled back"

    name = models.CharField(max_length=32, choices=Name.choices, db_index=True)
    version = models.IntegerField()
    # Wide enough for a real pipeline description. These are read on a model
    # card, so "TfidfVectorizer(char_wb 2-4) + SGDClassifier(modified_huber)"
    # has to fit whole — truncating it would leave the card naming half a model.
    algorithm = models.CharField(max_length=160)
    status = models.CharField(max_length=16, choices=Status.choices,
                              default=Status.SHADOW, db_index=True)

    trained_at = models.DateTimeField(auto_now_add=True, db_index=True)
    training_rows = models.IntegerField(default=0)
    # The last publish_at in the training half of the split. Every metric below
    # was measured on rows *after* this instant, which is what makes them an
    # estimate of future performance rather than of memory.
    trained_through = models.DateTimeField(null=True, blank=True)

    # The column order, the categorical vocabularies, and any scaling — enough
    # to rebuild an identical design matrix at inference time. Stored in the row
    # rather than beside the artifact so a model can never be loaded against a
    # feature set it was not fitted on.
    feature_spec = models.JSONField(default=dict, blank=True)
    # Whatever the trainer measured. Free-form on purpose: interval coverage is
    # the headline for the quantile model and calibration error is the headline
    # for the classifier, and forcing both into one column set would flatten the
    # difference. `promotion` inside it records the gate's decision and why.
    metrics = models.JSONField(default=dict, blank=True)
    artifact_path = models.CharField(max_length=400, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "ml_model"
        ordering = ("name", "-version")
        constraints = [
            models.UniqueConstraint(fields=("name", "version"), name="uq_mlmodel_name_version"),
            # "Which one is live" must have exactly one answer per role.
            models.UniqueConstraint(
                fields=("name",), condition=models.Q(status="active"),
                name="uq_mlmodel_one_active_per_name",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} v{self.version} ({self.status})"


class AdPrediction(models.Model):
    """What the active models said about one ad, at the last scoring run.

    Every field is nullable and every one means "the model refused or was not
    active", never zero. That distinction is the same one ``liquidity`` makes on
    the deal card: "we cannot say" and "the answer is low" are different facts
    and a chart that renders them identically is lying about one of them.
    """

    class Anomaly(models.TextChoices):
        """Why a listing stands out — two causes the old single MAD threshold
        could not tell apart, which is the whole point of adding a second model.

        ``pricing.flag_high_outliers`` measures distance from a cohort median in
        price alone, so a genuinely cheap car and a car with a broken record
        both come back as "far from the middle". Here the price residual and the
        feature-space outlier score are separate readings, so a listing that is
        cheap *and* otherwise ordinary is a candidate, while one that is odd in
        its own attributes is a data problem to look at.
        """

        UNDERPRICED = "underpriced_candidate", "Below predicted p10, features unremarkable"
        DATA = "data_anomaly", "Outlier in feature space — suspect the record"

    ad = models.OneToOneField(Ad, on_delete=models.CASCADE, related_name="ml")

    # --- Quantile price model -------------------------------------------
    price_p10 = models.BigIntegerField(null=True, blank=True)
    price_p50 = models.BigIntegerField(null=True, blank=True, db_index=True)
    price_p90 = models.BigIntegerField(null=True, blank=True)
    # (p50 - ask) / p50. Positive means the model thinks the car is underpriced.
    # Stored as its own indexed column because `band=ml` orders by it.
    residual_pct = models.FloatField(null=True, blank=True, db_index=True)
    # Exact TreeSHAP contributions in log-price space, feature -> value, biggest
    # |contribution| first, truncated. The reason the learned number is allowed
    # on screen at all: it arrives with its own decomposition beside the
    # statistical one, so the reader can compare two accounts of the same car.
    contributions = models.JSONField(default=list, blank=True)

    # --- Isolation Forest ------------------------------------------------
    anomaly_score = models.FloatField(null=True, blank=True)
    anomaly_kind = models.CharField(max_length=32, choices=Anomaly.choices,
                                    blank=True, db_index=True)

    # --- Time-to-sell ----------------------------------------------------
    # Calibrated probability that this listing leaves the feed within the
    # trained horizon. "Leaves the feed", never "sells": Bama publishes no
    # reason for a delisting.
    sell_fast_prob = models.FloatField(null=True, blank=True)
    sell_fast_horizon_days = models.IntegerField(null=True, blank=True)

    # --- Value tiers -----------------------------------------------------
    value_tier = models.CharField(max_length=32, blank=True, db_index=True)
    value_tier_rank = models.IntegerField(null=True, blank=True)

    # --- Text classifier -------------------------------------------------
    # Set only when the text confidently predicts a *different* catalogue model
    # than the one the ad was filed under. See `apps/ml/train.py` for why that,
    # and not gap-filling, is the useful job here.
    suspected_model = models.ForeignKey("core.Model", on_delete=models.SET_NULL,
                                        null=True, blank=True,
                                        related_name="ml_suspected_ads")
    suspected_model_prob = models.FloatField(null=True, blank=True)

    # Which artifacts produced the values above, so a row scored by a model that
    # has since been rolled back is identifiable rather than merely old.
    model_versions = models.JSONField(default=dict, blank=True)
    scored_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        db_table = "ml_adprediction"
        indexes = [
            # The `band=ml` board: strongly-underpriced candidates, best first.
            models.Index(fields=("anomaly_kind", "-residual_pct"), name="mlpred_band_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.ad_id} p50={self.price_p50}"


class ReviewDecision(models.Model):
    """A human's verdict on something a model flagged.

    Two jobs, and they are deliberately the same row. It records the outcome so
    a queue does not re-present a case somebody already settled, and it is the
    only source of *labelled* data this project has — every other label here is
    either the catalogue's own value or a rule's output, so a reviewer saying
    "no, that one is fine" is genuinely new information.

    The flag is identified by ``kind`` rather than by a foreign key to a
    prediction: ``AdPrediction`` rows are deleted and rebuilt wholesale on every
    scoring tick, and a decision that vanished when the scorer next ran would be
    worse than not recording it. An ad code survives rescoring.
    """

    class Kind(models.TextChoices):
        SUSPECT_MODEL = "suspect_model", "Filed under the wrong model"
        DATA_ANOMALY = "data_anomaly", "The record itself looks broken"

    class Verdict(models.TextChoices):
        CONFIRMED = "confirmed", "The model was right"
        REJECTED = "rejected", "The model was wrong"

    ad = models.ForeignKey("core.Ad", on_delete=models.CASCADE,
                           related_name="review_decisions")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    verdict = models.CharField(max_length=16, choices=Verdict.choices)
    # What the model claimed at the time. Kept as a snapshot because the next
    # retrain may change its mind, and a decision has to stay interpretable
    # against what was actually on screen when somebody made it.
    claim = models.JSONField(default=dict, blank=True)
    note = models.TextField(blank=True)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                 blank=True, related_name="ml_review_decisions")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "ml_reviewdecision"
        constraints = [
            # One standing decision per (ad, kind). Re-reviewing overwrites.
            models.UniqueConstraint(fields=("ad", "kind"), name="one_decision_per_flag"),
        ]

    def __str__(self) -> str:
        return f"{self.ad_id} {self.kind}={self.verdict}"
