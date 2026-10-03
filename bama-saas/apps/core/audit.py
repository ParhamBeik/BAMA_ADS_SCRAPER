"""Read-only data contract report for the CLI and staff Control screen."""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.common.verify import MAX_JALALI_YEAR, MAX_PLAUSIBLE_MILEAGE, MIN_JALALI_YEAR
from apps.core.models import (
    Ad,
    AdObservation,
    AdVersion,
    AdVersionPhoto,
    ArchivedImage,
    DetailPageCheck,
    FetchRun,
    IngestReject,
    Model,
    PriceObservation,
)


def report(*, now=None) -> dict:
    """Compact counts and rates, without raw payloads or seller data."""
    now = now or timezone.now()
    ads = Ad.objects
    total = ads.count()
    active = ads.filter(status=Ad.Status.ACTIVE)
    active_n = active.count()
    cash = Q(price_type="lumpsum", current_price__gt=0, price_basis_unclear=False)
    refs = ~Q(primary_image_url="") | ~Q(image_urls=[])
    photo_versions = AdVersionPhoto.objects
    statuses = dict(ads.values_list("admission_state").annotate(n=Count("code")))
    # Legacy source_family is blank until backfill; raw_payload remains the
    # source of truth for detecting pre-existing taxonomy collisions.
    mixed = (ads.exclude(raw_payload__detail__brand_fa__isnull=True)
             .exclude(model__isnull=True).values("model_id")
             .annotate(families=Count("raw_payload__detail__brand_fa", distinct=True))
             .filter(families__gt=1).order_by("-families", "model_id"))
    collision_ids = [row["model_id"] for row in mixed[:20]]
    collisions = list(
        Model.objects.filter(pk__in=collision_ids).values("id", "name_fa", "is_confirmed")
    )
    since = now - timedelta(days=7)
    trend = list(
        FetchRun.objects.filter(started_at__gte=since)
        .annotate(day=TruncDate("started_at"))
        .values("day", "source", "status")
        .annotate(runs=Count("id"), fetched=Sum("fetched_count"),
                  created=Sum("created_count"), updated=Sum("updated_count"),
                  price_changes=Sum("price_change_count"))
        .order_by("day", "source", "status")
    )
    latest_fetch = (FetchRun.objects.filter(source=FetchRun.Source.LIVE_FETCH)
                    .order_by("-finished_at").values("finished_at", "status",
                                                     "stop_reason").first())
    checks = {
        "active_cash_price": active.filter(cash).count(),
        "active_non_cash_or_unclear": active.exclude(cash).count(),
        "active_photo_reference": active.filter(refs).count(),
        "active_photo_reference_missing": active.exclude(refs).count(),
        "declared_gallery_exceeds_extracted": (
            ads.filter(image_count__gt=0)
            .extra(where=["image_count > jsonb_array_length(image_urls)"])
            .count()
        ),
        "active_catalog_ready": active.filter(admission_state=Ad.Admission.READY).count(),
        "pending_admission": statuses.get(Ad.Admission.PENDING, 0),
        "history_only": statuses.get(Ad.Admission.HISTORY_ONLY, 0),
        "legacy_unverified": statuses.get(Ad.Admission.LEGACY, 0),
        "verified_photos": photo_versions.filter(state=AdVersionPhoto.State.VERIFIED).count(),
        "photo_retries_due": photo_versions.filter(
            state__in=[AdVersionPhoto.State.PENDING, AdVersionPhoto.State.BLOCKED],
            next_retry_at__lte=now,
        ).count(),
        "photo_failed": photo_versions.filter(state=AdVersionPhoto.State.FAILED).count(),
        "archive_bytes": ArchivedImage.objects.aggregate(n=Sum("byte_size"))["n"] or 0,
        "missing_model": ads.filter(model__isnull=True).count(),
        "missing_year": ads.filter(year_jalali__isnull=True).count(),
        "invalid_year": ads.filter(year_jalali__isnull=False).exclude(
            year_jalali__range=(MIN_JALALI_YEAR, MAX_JALALI_YEAR)).count(),
        "missing_mileage": ads.filter(mileage__isnull=True).count(),
        "implausible_mileage": ads.filter(mileage__gt=MAX_PLAUSIBLE_MILEAGE).count(),
        "reversed_seen": ads.filter(last_seen_at__lt=F("first_seen_at")).count(),
        "current_version_ad_mismatch": ads.filter(current_version__isnull=False).exclude(
            current_version__ad_id=F("code")).count(),
        "current_version_missing": ads.filter(current_version__isnull=True).count(),
        "variant_model_mismatch": ads.exclude(variant__isnull=True).exclude(
            variant__model_id=F("model_id")).count(),
        "unreviewed_versions": AdVersion.objects.exclude(classification_state="verified").count(),
        "confirmed_mixed_models": Model.objects.filter(
            pk__in=mixed.values("model_id"), is_confirmed=True).count(),
        "detail_blocked_7d": DetailPageCheck.objects.filter(
            checked_at__gte=since, outcome=DetailPageCheck.Outcome.BLOCKED).count(),
        "detail_unavailable": ads.filter(detail_state=DetailPageCheck.Outcome.UNAVAILABLE).count(),
        "rejects_7d": IngestReject.objects.filter(observed_at__gte=since).count(),
    }
    return {
        "generated_at": now.isoformat(),
        "grain": "one current row per Bama ad code; versions and sightings are separate",
        "population": {"ads": total, "active": active_n,
                       "versions": AdVersion.objects.count(),
                       "observations": AdObservation.objects.count(),
                       "price_observations": PriceObservation.objects.count()},
        "checks": checks,
        "rates": {"active_cash_price": round(checks["active_cash_price"] / active_n, 4)
                  if active_n else None,
                  "active_non_cash_or_unclear": round(
                      checks["active_non_cash_or_unclear"] / active_n, 4) if active_n else None,
                  "active_photo_reference": round(checks["active_photo_reference"] / active_n, 4)
                  if active_n else None},
        "mixed_model_examples": collisions,
        "affected_examples": {
            "non_cash_or_unclear": list(active.exclude(cash).values_list("code", flat=True)[:10]),
            "no_photo_reference": list(active.exclude(refs).values_list("code", flat=True)[:10]),
            "implausible_mileage": list(ads.filter(mileage__gt=MAX_PLAUSIBLE_MILEAGE)
                                         .values_list("code", flat=True)[:10]),
            "needs_identity_review": list(AdVersion.objects.exclude(
                classification_state="verified").values_list("ad_id", flat=True)[:10]),
        },
        "latest_fetch": latest_fetch,
        "runs_by_day_source": trend,
    }
