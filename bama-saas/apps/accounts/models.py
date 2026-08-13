"""User and subscription models for the Bama SaaS platform."""

import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

class UserManager(BaseUserManager):
    """Email-based user manager (no usernames)."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Custom user identified by email (PBKDF2 hashed by Django)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    # Telegram chat id for push delivery (Phase 5). Empty = Telegram disabled.
    telegram_chat_id = models.CharField(max_length=64, blank=True, default="")
    email_verified_at = models.DateTimeField(null=True, blank=True)
    session_version = models.PositiveIntegerField(default=0)
    deletion_requested_at = models.DateTimeField(null=True, blank=True)
    preferred_brands = models.JSONField(default=list, blank=True)
    preferred_models = models.JSONField(default=list, blank=True)
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "accounts_user"
        ordering = ("email",)

    def __str__(self) -> str:
        return self.email


class Subscription(models.Model):
    """A user's subscription tier and usage counters (Phase 3 gating)."""

    class PlanType(models.TextChoices):
        FREE = "free", "Free"
        PRO = "pro", "Pro"
        ENTERPRISE = "enterprise", "Enterprise"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="subscriptions"
    )
    plan_type = models.CharField(
        max_length=16, choices=PlanType.choices, default=PlanType.FREE
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    monthly_api_limit = models.BigIntegerField(null=True, blank=True)
    api_usage_count = models.BigIntegerField(default=0)
    started_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_subscription"
        ordering = ("-started_at",)
        indexes = [
            models.Index(fields=("user", "status"), name="sub_user_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} {self.plan_type} ({self.status})"


# ---------------------------------------------------------------------------
# Engagement + alerting (Phase 3/5): user-owned premium entities.
# ---------------------------------------------------------------------------


class Favorite(models.Model):
    """A single bookmarked ad. One per (user, ad)."""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="favorites"
    )
    ad = models.ForeignKey(
        "core.Ad", on_delete=models.CASCADE, related_name="favorited_by"
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "accounts_favorite"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=("user", "ad"), name="uq_favorite_user_ad"),
        ]


class Alert(models.Model):
    """A user-configured watch rule. One of two shapes:

    - ``price_drop`` on a specific ``ad``.
    - ``undervalued`` for a ``model`` (fires when new deals appear).

    ``channels`` is a list of "email" / "telegram" / "inapp" (default in-app +
    email). Delivery identity comes from the user (email / telegram_chat_id).
    """

    class Type(models.TextChoices):
        PRICE_DROP = "price_drop", "Price drop"
        UNDERVALUED = "undervalued", "Undervalued listing"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="alerts")
    alert_type = models.CharField(max_length=16, choices=Type.choices)
    ad = models.ForeignKey(
        "core.Ad", on_delete=models.CASCADE, related_name="alerts",
        null=True, blank=True,
    )
    model = models.ForeignKey(
        "core.Model", on_delete=models.CASCADE, related_name="alerts",
        null=True, blank=True,
    )
    threshold = models.FloatField(
        null=True, blank=True,
        help_text="Min drop % (price_drop) or min discount % (undervalued).",
    )
    channels = models.JSONField(default=list, blank=True)
    enabled = models.BooleanField(default=True)

    class Delivery(models.TextChoices):
        DISABLED = "disabled", "Disabled"
        INAPP = "inapp", "In-app only"
        IMMEDIATE = "immediate", "Immediate email + in-app"
        DAILY = "daily", "Daily email + in-app"

    delivery = models.CharField(
        max_length=16, choices=Delivery.choices, default=Delivery.IMMEDIATE
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "accounts_alert"
        ordering = ("-created_at",)


class Notification(models.Model):
    """A queued / delivered message row (in-app, email, or Telegram).

    Created by the alert evaluator; status tracks delivery so failed sends can
    be retried and the UI can show an in-app inbox. ``dedupe_key`` prevents the
    same event notifying twice (e.g. a price drop re-firing each tick).
    """

    class Channel(models.TextChoices):
        INAPP = "inapp", "In-app"
        EMAIL = "email", "Email"
        TELEGRAM = "telegram", "Telegram"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="notifications"
    )
    alert = models.ForeignKey(
        Alert, on_delete=models.SET_NULL, related_name="notifications",
        null=True, blank=True,
    )
    channel = models.CharField(max_length=16, choices=Channel.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    subject = models.CharField(max_length=255, blank=True, default="")
    body = models.TextField(blank=True, default="")
    related_ad = models.ForeignKey(
        "core.Ad", on_delete=models.SET_NULL, related_name="notifications",
        null=True, blank=True,
    )
    dedupe_key = models.CharField(max_length=255, blank=True, default="", db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "accounts_notification"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("user", "status"), name="notif_user_status_idx"),
        ]


class FeatureUsageCounter(models.Model):
    """Daily atomic usage counters for capped features (e.g. valuations)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="feature_usage")
    feature = models.CharField(max_length=64)
    day = models.DateField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "accounts_feature_usage"
        constraints = [
            models.UniqueConstraint(
                fields=("user", "feature", "day"), name="uq_feature_usage_day"
            ),
        ]


class ProAccessRequest(models.Model):
    """In-app Pro upgrade request; staff approves with optional expiry."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="pro_requests")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    message = models.TextField(blank=True, default="")
    staff_note = models.TextField(blank=True, default="")
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_pro_requests"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    granted_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "accounts_pro_request"
        ordering = ("-created_at",)


class StaffAuditLog(models.Model):
    """Append-only record of staff mutations in the control center."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="staff_actions")
    action = models.CharField(max_length=120)
    target_type = models.CharField(max_length=64, blank=True, default="")
    target_id = models.CharField(max_length=64, blank=True, default="")
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "accounts_staff_audit"
        ordering = ("-created_at",)


class ProductEvent(models.Model):
    """Privacy-minimal first-party analytics; purge after 90 days."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="product_events"
    )
    event = models.CharField(max_length=64)
    properties = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "accounts_product_event"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("event", "created_at"), name="product_event_idx"),
        ]
