"""PostgreSQL advisory locks shared by processes in different containers.

The worker, the ML container and the web container each run their own Python
process, so a file lock or a module-level flag coordinates nothing between
them. The database is the one thing they all share.

Two kinds:

- ``rebuild_lock`` serialises the delete-then-insert rebuilds of a derived
  table. Transaction-scoped, so it is released by the commit that publishes
  the new rows and cannot outlive a crash. Two rebuilds of the same table used
  to interleave: one deleted the rows the other had just written, then both
  bulk-inserted and the second hit the one-row-per-ad unique constraint.
- ``lease`` marks a long-running phase other processes may want to stay out
  of, such as the train cadence's full rescore. Session-scoped, like
  ``fetcher._fetch_lease``.
"""

from __future__ import annotations

from contextlib import contextmanager

from django.db import connection

# Distinct from fetcher.FETCH_LEASE_KEY (0x42414D41) and the per-label keys in
# jobs.views._lease_key.
DEAL_SCORES_REBUILD = 0x42414D42
PREDICTIONS_REBUILD = 0x42414D43
TRAIN_LEASE = 0x42414D44


def rebuild_lock(key: int) -> None:
    """Block until no other transaction is rebuilding the table behind ``key``.

    Must be called inside ``transaction.atomic()``; the lock is released when
    that transaction commits or rolls back.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("rebuild_lock must be taken inside transaction.atomic()")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


@contextmanager
def lease(key: int):
    """Try to hold ``key`` for the block; yields whether it was acquired."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        acquired = bool(cursor.fetchone()[0])
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


def lease_busy(key: int) -> bool:
    """True when another session holds ``key``."""
    with lease(key) as acquired:
        return not acquired
