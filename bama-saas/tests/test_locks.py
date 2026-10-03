"""Cross-process coordination of the derived-table rebuilds.

A second, raw PostgreSQL connection stands in for the other container: it
holds the lock, and the code under test must either wait for it or stand down.
"""

from __future__ import annotations

import psycopg
import pytest
from django.db import OperationalError, connection

from apps.core.locks import DEAL_SCORES_REBUILD, TRAIN_LEASE, lease_busy
from apps.core.pricing import compute_deal_scores
from apps.jobs import jobs


@pytest.fixture
def other_process(db):
    params = connection.get_connection_params()
    params.pop("cursor_factory", None)
    params.pop("context", None)
    conn = psycopg.connect(**params, autocommit=False)
    yield conn
    conn.rollback()
    conn.close()


@pytest.mark.django_db
def test_deal_board_rebuild_waits_for_a_concurrent_one(other_process):
    with other_process.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [DEAL_SCORES_REBUILD])
    with connection.cursor() as cursor:
        cursor.execute("SET lock_timeout = '200ms'")
    try:
        # Waiting, not inserting on top of the other rebuild, is the fix; the
        # timeout turns "waits" into something a test can observe.
        with pytest.raises(OperationalError, match="lock timeout"):
            compute_deal_scores()
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET lock_timeout")
    other_process.commit()
    compute_deal_scores()  # free again once the other transaction ends


@pytest.mark.django_db
def test_hot_ml_score_stands_down_while_the_train_cadence_runs(other_process):
    assert not lease_busy(TRAIN_LEASE)
    with other_process.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s)", [TRAIN_LEASE])
    assert lease_busy(TRAIN_LEASE)
    result = jobs.ml_score(incremental=True)
    assert result["skipped"] is True
    assert "train" in result["detail"]
    with other_process.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s)", [TRAIN_LEASE])
    assert not lease_busy(TRAIN_LEASE)
