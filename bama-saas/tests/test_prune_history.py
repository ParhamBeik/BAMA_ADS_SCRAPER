"""Retention prune: old observations/coverage/job runs go; recent + last sweeps stay."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import Ad, AdObservation, AdVersion, Brand, FetchRun, JobRun, Model, PageCoverage
from apps.jobs.services.prune import prune_history


@pytest.mark.django_db
def test_prune_history_deletes_old_rows_and_keeps_recent_and_sweep_coverage():
    now = timezone.now()
    old = now - timedelta(days=120)
    brand = Brand.objects.create(slug="x", name_fa="x")
    model = Model.objects.create(brand=brand, name_fa="m")
    ad = Ad.objects.create(code="p1", brand=brand, model=model, year_jalali=1400)

    old_run = FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH,
        status=FetchRun.Status.SUCCEEDED,
        started_at=old,
        finished_at=old,
        reached_end=True,
        mode=FetchRun.Mode.FULL,
    )
    keep_run = FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH,
        status=FetchRun.Status.SUCCEEDED,
        started_at=now,
        finished_at=now,
        reached_end=True,
        mode=FetchRun.Mode.FULL,
    )
    version = AdVersion.objects.create(
        ad=ad, semantic_hash="a" * 8, raw_hash="b" * 8,
        payload={}, origin=AdVersion.Origin.LIVE_FETCH, first_observed_at=old,
    )
    AdObservation.objects.create(
        ad=ad, fetch_run=old_run, version=version, observed_at=old, raw_hash="b" * 8,
    )
    AdObservation.objects.create(
        ad=ad, fetch_run=keep_run, version=version, observed_at=now, raw_hash="b" * 8,
    )
    PageCoverage.objects.create(
        fetch_run=old_run, page_index=0, rank_lo=1, rank_hi=30,
        ad_count=30, fetched_at=old,
    )
    PageCoverage.objects.create(
        fetch_run=keep_run, page_index=0, rank_lo=1, rank_hi=30,
        ad_count=30, fetched_at=now,
    )
    JobRun.objects.create(name="fetch", status=JobRun.Status.OK, started_at=old, finished_at=old)
    JobRun.objects.create(name="fetch", status=JobRun.Status.OK, started_at=now, finished_at=now)

    result = prune_history(days=90)

    assert result["observations"] == 1
    assert AdObservation.objects.count() == 1
    assert AdObservation.objects.get().fetch_run_id == keep_run.id
    # old_run is one of the last two completed sweeps — its coverage is kept.
    assert PageCoverage.objects.count() == 2
    assert JobRun.objects.filter(started_at__lt=now - timedelta(days=1)).count() == 0
    assert Ad.objects.filter(code="p1").exists()
    assert AdVersion.objects.filter(ad=ad).exists()
