"""Write only the difference when a derived table is rebuilt.

The deal board, the market index and the ML predictions used to be rebuilt by
deleting every row in scope and inserting them all again. Most rows come out
identical from one tick to the next, so nearly all of that was wasted work:
70M inserts to keep 19k deal-score rows, 78M for 66k index points, 18M for 72k
predictions (``pg_stat_user_tables``, 2026-09-25 to 2026-11-27). Every one of
them writes the heap, every index, the TOAST table for the JSON columns and the
WAL, and leaves a dead row for autovacuum to clean up.

``sync_rows`` makes the rows in scope equal to the new ones, with the same end
state as delete-then-insert: new keys are inserted, changed rows are updated,
and rows whose key is gone are deleted. Unchanged rows are not touched, so an
``auto_now`` column on them keeps the time the values last changed.
"""

from __future__ import annotations

import json
import math

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone


def _comparable(field, value):
    """The value as it would come back from the database.

    A JSON column turns tuples into lists, so compare through the encoder.
    NaN is not equal to itself and would make a row look changed on every run.
    """
    if isinstance(field, models.JSONField):
        return json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True)
    if isinstance(value, float) and math.isnan(value):
        return "nan"
    return value


def sync_rows(model, rows, *, key, existing, batch_size: int = 500) -> dict[str, int]:
    """Make ``existing`` (the rows in scope) match ``rows``. Returns the counts.

    ``key`` holds the attribute names that identify a row (``("ad_id",)``,
    ``("scope", "scope_id", "date")``). The rows in ``rows`` must be unsaved
    instances with unique keys. ``existing`` must include every stored row that
    has one of those keys, or the insert will hit the unique constraint, which
    is the same contract the old delete-then-insert had.
    """
    meta = model._meta
    auto_now = [f for f in meta.concrete_fields if getattr(f, "auto_now", False)]
    compared = [
        f for f in meta.concrete_fields
        if not f.primary_key and f not in auto_now and not getattr(f, "auto_now_add", False)
    ]

    def key_of(obj):
        return tuple(getattr(obj, k) for k in key)

    current = {key_of(obj): obj for obj in existing}
    wanted = {key_of(obj): obj for obj in rows}

    gone = [obj.pk for k, obj in current.items() if k not in wanted]
    to_create, to_update = [], []
    for k, new in wanted.items():
        old = current.get(k)
        if old is None:
            to_create.append(new)
            continue
        if any(
            _comparable(f, getattr(old, f.attname)) != _comparable(f, getattr(new, f.attname))
            for f in compared
        ):
            new.pk = old.pk
            to_update.append(new)

    if gone:
        model.objects.filter(pk__in=gone).delete()
    if to_update:
        # `bulk_update` does not run `pre_save`, so `auto_now` is set by hand.
        if auto_now:
            now = timezone.now()
            for obj in to_update:
                for f in auto_now:
                    setattr(obj, f.attname, now)
        model.objects.bulk_update(
            to_update, [f.name for f in compared + auto_now], batch_size=batch_size,
        )
    if to_create:
        model.objects.bulk_create(to_create, batch_size=batch_size)

    return {
        "created": len(to_create),
        "updated": len(to_update),
        "deleted": len(gone),
        "unchanged": len(wanted) - len(to_create) - len(to_update),
    }
