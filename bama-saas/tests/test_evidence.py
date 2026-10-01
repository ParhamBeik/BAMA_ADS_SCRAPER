"""Admission and archival evidence cannot silently become a catalog assertion."""

from datetime import timedelta
from hashlib import sha256

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.core.admission import sync_admission
from apps.core.models import Ad, AdVersion, AdVersionPhoto, ArchivedImage, Brand, Model
from apps.jobs import photo_archive


@pytest.fixture
def evidence():
    brand = Brand.objects.create(name_fa="پژو", slug="peugeot-evidence")
    model = Model.objects.create(brand=brand, name_fa="206")
    ad = Ad.objects.create(code="evidence1", brand=brand, model=model,
                           price_type="lumpsum", current_price=1_000_000_000)
    version = AdVersion.objects.create(ad=ad, semantic_hash="a" * 64, raw_hash="b" * 64,
                                       first_observed_at=timezone.now(),
                                       classification_state="verified", classified_model=model)
    ad.current_version = version
    ad.save(update_fields=["current_version"])
    photo = AdVersionPhoto.objects.create(
        version=version, position=0,
        source_url="https://cdn-sth1.bama.ir/uploads/BamaImages/VehicleCarImages/evidence.jpg",
    )
    return ad, photo


@pytest.mark.django_db
def test_admission_requires_current_verified_photo_and_cash(evidence, tmp_path):
    ad, photo = evidence
    assert sync_admission(ad) == Ad.Admission.PENDING
    body = b"\xff\xd8\xffsample\xff\xd9"
    digest = sha256(body).hexdigest()
    asset = ArchivedImage.objects.create(
        sha256=digest, relative_path=f"{digest[:2]}/{digest}",
        content_type="image/jpeg", byte_size=len(body),
    )
    photo.asset = asset
    photo.state = AdVersionPhoto.State.VERIFIED
    photo.save(update_fields=["asset", "state"])
    with override_settings(PHOTO_ARCHIVE_ROOT=str(tmp_path)):
        assert sync_admission(ad) == Ad.Admission.PENDING
        path = tmp_path / asset.relative_path
        path.parent.mkdir()
        path.write_bytes(body)
        assert sync_admission(ad) == Ad.Admission.READY
        path.write_bytes(b"corrupt bytes")
        assert sync_admission(ad) == Ad.Admission.PENDING
        path.write_bytes(body)
        ad.current_price = None
        ad.save(update_fields=["current_price"])
        assert sync_admission(ad) == Ad.Admission.HISTORY_ONLY
    assert AdVersion.objects.filter(pk=photo.version_id).exists()


@pytest.mark.django_db
def test_three_retrieval_failures_span_24_hours(evidence, monkeypatch):
    ad, photo = evidence
    monkeypatch.setattr(photo_archive, "consecutive_blocks", lambda: 0)
    monkeypatch.setattr(photo_archive, "_fetch", lambda url: 404)
    start = timezone.now()
    assert photo_archive.archive_one(photo, now=start) == "pending"
    assert photo.next_retry_at == start + timedelta(hours=8)
    assert photo_archive.archive_one(photo, now=start + timedelta(hours=8)) == "pending"
    assert photo.next_retry_at == start + timedelta(hours=24)
    assert photo_archive.archive_one(photo, now=start + timedelta(hours=24)) == "failed"
    ad.refresh_from_db()
    assert ad.admission_state == Ad.Admission.REJECTED


@pytest.mark.django_db
def test_source_outage_pauses_retry_clock(evidence, monkeypatch):
    _, photo = evidence
    monkeypatch.setattr(photo_archive, "consecutive_blocks", lambda: 0)
    monkeypatch.setattr(photo_archive, "_fetch", lambda url: 404)
    start = timezone.now()
    photo_archive.archive_one(photo, now=start)
    monkeypatch.setattr(photo_archive, "consecutive_blocks", lambda: 2)
    assert photo_archive.archive_one(photo, now=start + timedelta(hours=4)) == "source_blocked"
    monkeypatch.setattr(photo_archive, "consecutive_blocks", lambda: 0)
    assert photo_archive.archive_one(photo, now=start + timedelta(hours=20)) == "not_due"
    assert photo.next_retry_at == start + timedelta(hours=24)
    assert photo.attempts == 1


@pytest.mark.django_db
def test_archive_cap_and_corrupt_local_copy(evidence, tmp_path, monkeypatch):
    monkeypatch.setattr(photo_archive.shutil, "disk_usage", lambda root: type(
        "Usage", (), {"free": 10_000})())
    body = b"\xff\xd8\xffsample\xff\xd9"
    with override_settings(PHOTO_ARCHIVE_ROOT=str(tmp_path),
                           PHOTO_ARCHIVE_CAP_BYTES=len(body) - 1,
                           PHOTO_ARCHIVE_MIN_FREE_BYTES=0):
        assert photo_archive._archive_bytes("image/jpeg", body) is None
    with override_settings(PHOTO_ARCHIVE_ROOT=str(tmp_path),
                           PHOTO_ARCHIVE_CAP_BYTES=len(body),
                           PHOTO_ARCHIVE_MIN_FREE_BYTES=0):
        asset = photo_archive._archive_bytes("image/jpeg", body)
        assert asset.sha256 == sha256(body).hexdigest()
        (tmp_path / asset.relative_path).unlink()
        with pytest.raises(RuntimeError, match="missing or corrupt"):
            photo_archive._archive_bytes("image/jpeg", body)


@pytest.mark.django_db
def test_archive_prioritizes_current_versions(evidence, monkeypatch):
    ad, older_photo = evidence
    current_version = AdVersion.objects.create(
        ad=ad, semantic_hash="d" * 64, raw_hash="e" * 64,
        first_observed_at=timezone.now(),
    )
    current_photo = AdVersionPhoto.objects.create(
        version=current_version, position=0, source_url=older_photo.source_url,
    )
    ad.current_version = current_version
    ad.save(update_fields=["current_version"])
    selected = []
    monkeypatch.setattr(photo_archive, "archive_one", lambda photo, now: (
        selected.append(photo.pk) or "verified"
    ))
    assert photo_archive.archive_pending(limit=1) == {"verified": 1}
    assert selected == [current_photo.pk]


@pytest.mark.django_db
def test_rare_model_group_keeps_ads_without_a_price_range(evidence):
    from apps.core.explorer import model_exploration

    ad, _ = evidence
    ad.publish_at = timezone.now()
    ad.year_jalali = 1400
    ad.save(update_fields=["publish_at", "year_jalali"])
    result = model_exploration(ad.model_id)
    assert result["selected"]["count"] == 1
    assert result["selected"]["asking_range"] is None
    assert result["ads"][0]["code"] == ad.code
    assert result["purchase_price"] is None


def test_holdout_deduplicates_repost_and_uses_prior_baseline():
    from apps.ml.train import _cohort_quantile_baseline, _median_for, _median_table, time_split

    now = timezone.now()
    rows = [
        {"publish_at": now + timedelta(days=i), "code": str(i),
         "listing_fingerprint": "same" if i in (0, 9) else str(i),
         "model_id": 1, "variant_id": 1, "year_jalali": 1400,
         "current_price": 100 + i}
        for i in range(10)
    ]
    train, holdout = time_split(rows)
    assert "9" not in {r["code"] for r in holdout}
    assert max(r["publish_at"] for r in train) < min(r["publish_at"] for r in holdout)
    query = [{**rows[8], "current_price": 100_000}]
    assert _median_for(_median_table(train), query[0]) < 200
    assert _cohort_quantile_baseline(query, reference_rows=train)[0.5][0] < 200

    within = [
        {"publish_at": now + timedelta(days=i), "code": str(i),
         "listing_fingerprint": "repeat" if i in (8, 9) else str(i)}
        for i in range(10)
    ]
    _, deduped = time_split(within)
    assert len(deduped) == 1
