"""Distribution drift: noticing that the data changed shape.

The other three validation layers all encode an expectation — a plausible band, an
impossible transition, a peer group. That makes them blind in one specific way:
they can only catch what someone anticipated. When the source renames a field or
changes a format, often no rule fires at all. The data just gets quietly emptier,
every derived number drifts with it, and the first symptom is a market statistic
that looks slightly wrong months later.

What gives it away is the distribution, not any row: a null rate that jumps from
2% to 40%, a field that loses most of its distinct values, a spike in freshly
minted catalog rows. This module records that shape daily and compares today
against the trailing window.

Comparison uses median and MAD for the same reason layer 3 does. A mean baseline
would absorb the drift it is meant to detect — the bad days become part of
"normal" and the alarm silently stops firing exactly when the problem persists.
"""

from __future__ import annotations

import statistics
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from apps.core.models import Ad, Brand, DataQualitySnapshot, IngestReject, Model

# Fields worth watching: each is either a cohort key, a filter users rely on, or
# a direct product of the title/detail parsing that is most likely to break.
WATCHED_FIELDS = (
    "year_jalali", "mileage", "current_price", "publish_at", "model_id",
    "variant_id", "city_id", "dealer_id", "transmission", "fuel",
    "body_status", "body_color", "price_type",
)

# Fields where a collapse in variety is the signal (a parser that starts writing
# one constant is not null, it is worse — it looks populated).
CARDINALITY_FIELDS = ("transmission", "fuel", "body_status", "body_color", "price_type")

# Days of history compared against. Two weeks covers the weekly rhythm of the
# market without letting a slow month become the new normal.
LOOKBACK_DAYS = 14

# Below this there is not enough history to have an opinion, and alarming on two
# data points would just be noise.
MIN_HISTORY_DAYS = 7

# How far outside the trailing spread counts as drift. 4 is deliberately loose:
# this alarm should mean "something changed at the source", and an alarm that
# cries wolf gets muted, which is worse than no alarm at all.
DRIFT_SIGMA = 4.0

_MAD_TO_SIGMA = 1.4826

# Floors on the estimated spread, so a metric that has been perfectly flat does
# not alarm on a rounding difference. The two families need different scales:
#
# RATE metrics live in 0..1, so an absolute floor is meaningful — two percentage
# points of movement is noise regardless of where the rate sits.
#
# LEVEL metrics (counts, prices, cardinalities) span orders of magnitude in one
# table: distinct transmission values is 3, active ads is 60,000. Any absolute
# floor is simultaneously deafening for one and blind for the other — a floor of
# 5 would hide a transmission field collapsing from 3 distinct values to 1. So
# their floor is a fraction of their own baseline, which with DRIFT_SIGMA below
# means "alarm on a move of roughly 20% or more".
RATE, LEVEL = "rate", "level"
_RATE_FLOOR = 0.02
_LEVEL_FLOOR_FRACTION = 0.05


def _percentile(values: list[int], q: float):
    if not values:
        return None
    values = sorted(values)
    idx = min(int(q * (len(values) - 1)), len(values) - 1)
    return values[idx]


def build_snapshot(date=None) -> DataQualitySnapshot:
    """Record the shape of today's active population."""
    date = date or timezone.localdate()
    active = Ad.objects.filter(status=Ad.Status.ACTIVE)
    total = Ad.objects.count()
    active_count = active.count()

    null_rates = {}
    if active_count:
        # One query for every watched field rather than one per field.
        nulls = active.aggregate(
            **{f: Count("code", filter=Q(**{f"{f}__isnull": True})) for f in WATCHED_FIELDS}
        )
        null_rates = {f: round(n / active_count, 4) for f, n in nulls.items()}

    distinct_counts = {
        f: active.values(f).distinct().count() for f in CARDINALITY_FIELDS
    }

    flag_counts: dict[str, int] = {}
    for flags in active.values_list("quality_flags", flat=True).iterator(chunk_size=2000):
        for flag in flags or []:
            flag_counts[flag] = flag_counts.get(flag, 0) + 1

    cohort_flag_counts: dict[str, int] = {}
    for flags in active.values_list("cohort_flags", flat=True).iterator(chunk_size=2000):
        for flag in flags or []:
            cohort_flag_counts[flag] = cohort_flag_counts.get(flag, 0) + 1

    prices = list(
        active.filter(current_price__gt=0).values_list("current_price", flat=True)
    )

    snapshot, _ = DataQualitySnapshot.objects.update_or_create(
        date=date,
        defaults={
            "total_ads": total,
            "active_ads": active_count,
            "priced_ads": len(prices),
            "flag_counts": flag_counts,
            "cohort_flag_counts": cohort_flag_counts,
            "null_rates": null_rates,
            "distinct_counts": distinct_counts,
            "rejects_today": IngestReject.objects.filter(observed_at__date=date).count(),
            "unconfirmed_brands": Brand.objects.filter(is_confirmed=False).count(),
            "unconfirmed_models": Model.objects.filter(is_confirmed=False).count(),
            "price_p10": _percentile(prices, 0.10),
            "price_median": _percentile(prices, 0.50),
            "price_p90": _percentile(prices, 0.90),
            "alarms": [],
        },
    )
    return snapshot


def _metrics(snapshot: DataQualitySnapshot) -> dict[str, tuple[float, str]]:
    """Flatten a snapshot into {metric name: (value, family)}."""
    out: dict[str, tuple[float, str]] = {}
    for field, rate in (snapshot.null_rates or {}).items():
        out[f"null_rate.{field}"] = (float(rate), RATE)
    for field, n in (snapshot.distinct_counts or {}).items():
        out[f"distinct.{field}"] = (float(n), LEVEL)
    active = snapshot.active_ads or 0
    for flag, n in (snapshot.flag_counts or {}).items():
        # A rate, not a count: inventory size moves for legitimate reasons and
        # would otherwise trigger every flag metric at once.
        out[f"flag_rate.{flag}"] = (n / active if active else 0.0, RATE)
    out["active_ads"] = (float(active), LEVEL)
    out["unconfirmed_models"] = (float(snapshot.unconfirmed_models), LEVEL)
    if snapshot.price_median:
        out["price_median"] = (float(snapshot.price_median), LEVEL)
    return out


def _spread(baseline: float, mad: float, family: str) -> float:
    floor = (
        _RATE_FLOOR if family == RATE
        else max(abs(baseline) * _LEVEL_FLOOR_FRACTION, 1e-9)
    )
    return max(mad * _MAD_TO_SIGMA, floor)


def detect_drift(snapshot: DataQualitySnapshot, lookback: int = LOOKBACK_DAYS) -> list[dict]:
    """Compare a snapshot to its trailing window. Returns the alarms raised."""
    history = list(
        DataQualitySnapshot.objects.filter(
            date__lt=snapshot.date, date__gte=snapshot.date - timedelta(days=lookback)
        ).order_by("date")
    )
    if len(history) < MIN_HISTORY_DAYS:
        return []

    past = [_metrics(h) for h in history]
    alarms = []
    for name, (value, family) in _metrics(snapshot).items():
        series = [m[name][0] for m in past if name in m]
        if len(series) < MIN_HISTORY_DAYS:
            # A metric that only just started existing has no baseline. That is
            # itself worth noticing, but it is not drift.
            continue
        baseline = statistics.median(series)
        mad = statistics.median([abs(v - baseline) for v in series])
        deviation = abs(value - baseline) / _spread(baseline, mad, family)
        if deviation > DRIFT_SIGMA:
            alarms.append({
                "metric": name,
                "value": round(value, 4),
                "baseline": round(baseline, 4),
                "deviation": round(deviation, 2),
            })
    return sorted(alarms, key=lambda a: -a["deviation"])


def run_drift_check(date=None) -> tuple[DataQualitySnapshot, list[dict]]:
    """Build today's snapshot, judge it, and persist the verdict on the row."""
    snapshot = build_snapshot(date)
    alarms = detect_drift(snapshot)
    snapshot.alarms = alarms
    snapshot.save(update_fields=["alarms"])
    return snapshot, alarms
