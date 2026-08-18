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
    # 120-day-old coverage is past the retention window and proves nothing: the
    # depth ratchet only looks back FEED_DEPTH_WINDOW_DAYS. Coverage used to be
    # kept because its run had reached_end, a rule that no longer exists.
    assert PageCoverage.objects.count() == 1
    assert PageCoverage.objects.get().fetch_run_id == keep_run.id
    assert JobRun.objects.filter(started_at__lt=now - timedelta(days=1)).count() == 0
    assert Ad.objects.filter(code="p1").exists()
    assert AdVersion.objects.filter(ad=ad).exists()


@pytest.mark.django_db
def test_prune_never_deletes_coverage_inside_the_depth_window():
    """Coverage is the proof the ceiling and removal rule stand on.

    Pruning inside the ratchet window would lower the known feed depth, hiding
    the tail below the ceiling and silently stalling removal detection — so a
    short ``--days`` must not reach it.
    """
    now = timezone.now()
    recent = now - timedelta(days=10)
    run = FetchRun.objects.create(
        source=FetchRun.Source.LIVE_FETCH,
        status=FetchRun.Status.SUCCEEDED,
        started_at=recent, finished_at=recent, mode=FetchRun.Mode.FULL,
    )
    PageCoverage.objects.create(
        fetch_run=run, page_index=0, rank_lo=1, rank_hi=30,
        ad_count=30, fetched_at=recent,
    )

    prune_history(days=1)

    assert PageCoverage.objects.count() == 1
