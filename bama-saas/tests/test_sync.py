"""`sync_rows`: rebuilds write only the difference, with the same end state."""

from __future__ import annotations

from datetime import date

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core.models import Ad, DealScoreCache, MarketIndex
from apps.core.sync import sync_rows

D0, D1, D2 = date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3)


def _sync(rows):
    return sync_rows(
        MarketIndex, rows, key=("scope", "scope_id", "date"),
        existing=MarketIndex.objects.filter(scope="market", scope_id=None),
    )


@pytest.mark.django_db
def test_inserts_updates_and_deletes_only_the_difference():
    rows = [MarketIndex(scope="market", scope_id=None, date=D0, index_value=100.0),
            MarketIndex(scope="market", scope_id=None, date=D1, index_value=101.0)]
    assert _sync(rows) == {"created": 2, "updated": 0, "deleted": 0, "unchanged": 0}
    before = {m.date: (m.pk, m.calculated_at) for m in MarketIndex.objects.all()}

    # Same D0, changed D1, new D2.
    rows = [MarketIndex(scope="market", scope_id=None, date=D0, index_value=100.0),
            MarketIndex(scope="market", scope_id=None, date=D1, index_value=102.0),
            MarketIndex(scope="market", scope_id=None, date=D2, index_value=103.0)]
    assert _sync(rows) == {"created": 1, "updated": 1, "deleted": 0, "unchanged": 1}
    after = {m.date: m for m in MarketIndex.objects.all()}
    assert (after[D0].pk, after[D0].calculated_at) == before[D0]  # not rewritten
    assert after[D1].pk == before[D1][0]
    assert after[D1].index_value == 102.0
    assert after[D1].calculated_at > before[D1][1]  # auto_now set on update

    # D0 is gone from the new series.
    rows = [MarketIndex(scope="market", scope_id=None, date=D1, index_value=102.0),
            MarketIndex(scope="market", scope_id=None, date=D2, index_value=103.0)]
    assert _sync(rows) == {"created": 0, "updated": 0, "deleted": 1, "unchanged": 2}
    assert set(MarketIndex.objects.values_list("date", flat=True)) == {D1, D2}


@pytest.mark.django_db
def test_an_unchanged_rebuild_writes_nothing():
    rows = lambda: [MarketIndex(scope="market", scope_id=None, date=D0,  # noqa: E731
                                index_value=100.0, return_pct=float("nan"))]
    _sync(rows())
    with CaptureQueriesContext(connection) as ctx:
        assert _sync(rows())["unchanged"] == 1
    # One SELECT for the existing rows; NaN must not read as "changed".
    assert len(ctx.captured_queries) == 1


@pytest.mark.django_db
def test_json_tuples_compare_equal_to_the_lists_they_come_back_as():
    now = timezone.now()
    Ad.objects.create(code="sync1", current_price=1, publish_at=now, last_seen_at=now)

    def rows():
        return [DealScoreCache(ad_id="sync1", score=10.0, components={"band": (1, 2)})]

    sync_rows(DealScoreCache, rows(), key=("ad_id",), existing=DealScoreCache.objects.all())
    result = sync_rows(DealScoreCache, rows(), key=("ad_id",),
                       existing=DealScoreCache.objects.all())
    assert result["unchanged"] == 1
