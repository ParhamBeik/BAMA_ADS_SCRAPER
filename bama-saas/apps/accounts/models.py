"""User and saved-ads models.

This is a local, single-operator tool: there is no subscription tier, no
alerting, no in-app inbox. ``User`` survives only because Django admin needs a
user table and swapping ``AUTH_USER_MODEL`` on a database holding 66k ads and
688k observations is a far bigger risk than the ~40 lines it costs to keep.

``Favorite`` lost its user FK with the rest of the SaaS layer — one operator
means the saved list is a flat table keyed on the ad.
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
    """A saved ad. One row per ad — there is only one operator."""

    ad = models.OneToOneField(
        "core.Ad", on_delete=models.CASCADE, related_name="favorite"
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "accounts_favorite"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return str(self.ad_id)
