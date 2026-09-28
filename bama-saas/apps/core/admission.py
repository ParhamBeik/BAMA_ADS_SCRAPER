"""Catalog admission from the current immutable version and verified photo evidence."""

from apps.core.models import Ad, AdVersionPhoto


def sync_admission(ad: Ad) -> str:
    if not ad.current_version_id:
        state = Ad.Admission.LEGACY
    elif (ad.price_type != "lumpsum" or (ad.current_price or 0) <= 0
          or ad.price_basis_unclear):
        state = Ad.Admission.HISTORY_ONLY
    else:
        photos = AdVersionPhoto.objects.filter(version_id=ad.current_version_id)
        if photos.filter(state=AdVersionPhoto.State.VERIFIED,
                         asset__backed_up_at__isnull=False).exists():
            state = (Ad.Admission.READY if
                     ad.current_version.classification_state == "verified" else
                     Ad.Admission.PENDING)
        elif not photos.exists() or not photos.filter(
                state__in=[AdVersionPhoto.State.PENDING, AdVersionPhoto.State.BLOCKED,
                           AdVersionPhoto.State.VERIFIED]
        ).exists():
            state = Ad.Admission.REJECTED
        else:
            state = Ad.Admission.PENDING
    if ad.admission_state != state:
        Ad.objects.filter(pk=ad.pk).update(admission_state=state)
        ad.admission_state = state
    return state
