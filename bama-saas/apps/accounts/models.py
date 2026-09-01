"""Users, saved ads, and the per-user watch and alert layer.

Accounts are created by signing up, never seeded from the environment. The
first account on an empty database becomes staff (see accounts.views.
RegisterView); every one after it is an ordinary user.

This file used to open by stating that there was no alerting and no in-app
inbox, and that was true: watchlists and saved searches were dropped in
migration 0003 and the only notifier was a staff-owned singleton pointed at one
Telegram chat. It made two of the product's four questions unanswerable — you
could not follow a car you were thinking of buying, and "tell me when a good
deal appears" had nowhere to be stored. `Watchlist`, `AlertRule` and
`AlertDelivery` are that layer, per user.

The scope shape is shared by both new models and matches the rest of the app:
an optional brand slug, model, variant and `year_jalali`, narrowing left to
right. `scope_key` is derived from them in `save()` — the same trick
`Ad.price_basis_unclear` uses — because Postgres treats NULLs as distinct in a
unique constraint, so a natural key over four nullable columns would let one
user follow the same car any number of times.
"""

import uuid

from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
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
    """Custom user identified by email (password hashed by Django)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "accounts_user"
        ordering = ("email",)

    def __str__(self) -> str:
        return self.email


class Favorite(models.Model):
    """A saved ad owned by one authenticated user."""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="favorites", null=True, blank=True
    )
    ad = models.ForeignKey(
        "core.Ad", on_delete=models.CASCADE, related_name="favorites"
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "accounts_favorite"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("user", "ad"), name="uq_favorite_user_ad"
            ),
        ]

    def __str__(self) -> str:
        return str(self.ad_id)


class ScopedToACar(models.Model):
    """The brand / model / trim / model-year scope both watch models share.

    Abstract, so `Watchlist` and `AlertRule` cannot disagree about what "the
    same car" means — they are compared against each other constantly (a
    watchlist entry is offered an alert rule, an alert names the scope that
    matched it) and two definitions of the scope would make that comparison
    quietly wrong rather than loudly broken.

    Every field is optional and they narrow left to right: no fields at all is
    the whole market, which is a legitimate thing to watch.
    """

    # Brand is a slug rather than an FK for the same reason MarketIndex.scope_id
    # is text: `ingest` mints brand slugs, `BRAND_PARENT` remaps them, and a
    # hard FK would turn a catalogue merge into a cascade across user data.
    brand_slug = models.CharField(max_length=160, blank=True)
    model = models.ForeignKey("core.Model", on_delete=models.CASCADE,
                              null=True, blank=True, related_name="+")
    variant = models.ForeignKey("core.Variant", on_delete=models.CASCADE,
                                null=True, blank=True, related_name="+")
    year_jalali = models.IntegerField(null=True, blank=True)

    # Derived in save(), never set by a caller. See the module docstring for why
    # a natural key over the four nullable columns above cannot be unique.
    scope_key = models.CharField(max_length=200, db_index=True, editable=False)

    class Meta:
        abstract = True

    def build_scope_key(self) -> str:
        """A stable, comparable identity for this scope.

        Ordered narrowest-last so a key reads the way the scope does, and so a
        prefix match finds everything under a brand or a model.
        """
        parts = []
        if self.brand_slug:
            parts.append(f"brand:{self.brand_slug}")
        if self.model_id:
            parts.append(f"model:{self.model_id}")
        if self.variant_id:
            parts.append(f"variant:{self.variant_id}")
        if self.year_jalali:
            parts.append(f"year:{self.year_jalali}")
        return "/".join(parts) or "market"

    def save(self, *args, **kwargs):
        self.scope_key = self.build_scope_key()
        return super().save(*args, **kwargs)


class Watchlist(ScopedToACar):
    """A car a user is following, so the app can tell them when it moves.

    Deliberately a scope and not an ad. Following one listing is what
    `Favorite` already does; the question this answers is "what is happening to
    the kind of car I want to buy", which outlives any single listing on it.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="watchlists")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "accounts_watchlist"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=("user", "scope_key"),
                                    name="uq_watchlist_user_scope"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.scope_key}"


class AlertRule(ScopedToACar):
    """What one user considers worth being interrupted for.

    The bars mirror the operator singleton's (`core.NotifierSettings`) and are
    applied by the same function, so a per-user alert and the operator's cannot
    come to different conclusions about the same listing.

    `min_peers` is floored at the fair-price engine's own `MIN_PEERS` by the
    serializer: below that the median being compared against is not one this app
    is willing to quote, let alone wake somebody up for.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="alert_rules")
    name = models.CharField(max_length=120, blank=True)
    enabled = models.BooleanField(default=True)

    min_discount_pct = models.FloatField(default=10.0)
    min_peers = models.IntegerField(default=8)
    price_min = models.BigIntegerField(null=True, blank=True)
    price_max = models.BigIntegerField(null=True, blank=True)
    mileage_max = models.BigIntegerField(null=True, blank=True)

    # Painted and structural cars are routed to the review band because the
    # listing itself already explains the discount. Defaulting this on means a
    # new rule does not immediately deliver a repainted car as a find; a user
    # who wants them can say so.
    exclude_review = models.BooleanField(default=True)

    # Optional per-user Telegram. The in-app feed is always written; this is a
    # second channel, not the only one, which is what the singleton it replaces
    # got wrong.
    telegram_chat_id = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "accounts_alertrule"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("user", "enabled"), name="alertrule_user_on_idx"),
        ]

    def __str__(self) -> str:
        return self.name or f"{self.user_id}:{self.scope_key}"


class AlertDelivery(models.Model):
    """One listing delivered to one user's feed, once.

    Unique on (user, ad) rather than (rule, ad): two of a user's rules
    overlapping is normal and expected, and it must not mean the same car
    arrives twice. It is a *per-user* guard, unlike `core.NotifiedAd`, which is
    global and therefore silently swallowed a listing for everybody as soon as
    one recipient had seen it.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="alerts")
    ad = models.ForeignKey("core.Ad", on_delete=models.CASCADE, related_name="alerts")
    rule = models.ForeignKey(AlertRule, on_delete=models.SET_NULL, null=True,
                             blank=True, related_name="deliveries")

    # Copied, not looked up. The board is rebuilt on a schedule and a score is
    # dropped the moment its listing stops qualifying, so a feed that joined to
    # DealScoreCache would show "12% below peers" one day and an empty row the
    # next — for an alert whose whole point is what was true when it fired.
    discount_pct = models.FloatField(null=True, blank=True)
    peer_median = models.BigIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    # Whether the second channel actually went out. A failed Telegram send must
    # not stop the in-app row from existing.
    telegram_sent = models.BooleanField(default=False)

    class Meta:
        db_table = "accounts_alertdelivery"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=("user", "ad"),
                                    name="uq_alert_user_ad"),
        ]
        indexes = [
            models.Index(fields=("user", "read_at"), name="alert_user_unread_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.ad_id}"
