"""Matched-cohort chained price index — "did the market move?" as opposed to
"what does a car cost today?".

Those are different questions and the codebase previously only answered the
second. A median over all live listings moves whenever the *mix* of listings
moves, and the mix moves constantly: the live data shows inventory growing
21,688 → 33,668 in eight days while the overall median fell 2,500M → 2,390M.
Nothing in that median says how much of the 4.4% fall was cars getting cheaper
and how much was cheaper cars arriving.

The fix is to never compare different cars. The unit of measurement is the
**cohort** — one (model, variant, year_jalali) group — and the only thing ever
compared is the same cohort on two consecutive dates:

1. ``r_c = median_d / median_prev - 1``   (one cohort's own price move)
2. ``R_d = Σ(r_c · n_c) / Σ n_c``         (size-weighted average across cohorts)
3. ``index_d = index_prev · (1 + R_d)``   (chain it, base 100)

A cohort that only exists on one of the two dates contributes no ``r_c`` at all.
So new listings and delistings can shift the weights but cannot themselves move
the index — which is the entire point, and the property a raw median lacks.

Input is ``DailyInventorySnapshot``, already written daily by the worker and
already keyed on ``year_jalali`` and already filtered through ``verified()``.
This module therefore adds no new crawl load and no new quality path — it is
arithmetic over rows that exist. Its accuracy is inherited from that command,
which is why the snapshot's cohort key had to be fixed first.

Two guards keep the number honest:

* ``MIN_COHORT_ADS`` — a cohort whose median comes from one or two cars is noise,
  not a price signal; it is dropped from the return on both sides.
* ``MAX_COHORT_RETURN`` — a single cohort jumping >50% in a day is a data
  artefact (a cohort emptying to one odd car, a relisting wave), not a market
  move. Winsorising is deliberate: a mean of ratios has no upper bound and one
  bad cohort would otherwise set the headline number for the day.

Gaps in the daily series are bridged: consecutive *available* dates are chained,
so a day the worker did not run costs resolution, never a break in the series.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date as date_cls

from apps.core.models import DailyInventorySnapshot, MarketIndex

# A cohort must have at least this many ads on BOTH dates to contribute a return.
MIN_COHORT_ADS = 3

# Per-cohort daily moves beyond this are clipped, not trusted. 0.5 == ±50%.
MAX_COHORT_RETURN = 0.5

# Every index series starts here.
BASE_VALUE = 100.0


def _clip(value: float) -> float:
    return max(-MAX_COHORT_RETURN, min(MAX_COHORT_RETURN, value))


def cohort_series(scope: str, scope_id: str | None = None) -> dict:
    """``{date: {cohort_key: (median_price, ad_count)}}`` for one scope.

    One query. The scope filter is applied in SQL; the day/cohort grouping is
    done in Python, matching how every other service in this package works.
    """
    qs = DailyInventorySnapshot.objects.filter(
        median_price__isnull=False, model_id__isnull=False
    )
    if scope == MarketIndex.Scope.BRAND:
        qs = qs.filter(model__brand__slug=scope_id)
    elif scope == MarketIndex.Scope.MODEL:
        qs = qs.filter(model_id=scope_id)

    by_date: dict = defaultdict(dict)
    rows = qs.values_list(
        "date", "model_id", "variant_id", "year_jalali", "median_price", "ad_count"
    )
    for d, model_id, variant_id, year_jalali, median_price, ad_count in rows:
        by_date[d][(model_id, variant_id, year_jalali)] = (median_price, ad_count)
    return by_date


def compute_index(by_date: dict) -> list[dict]:
    """Chain per-date cohort medians into an index series.

    ``by_date`` is what :func:`cohort_series` returns. Output is one dict per
    date in ascending order; the first date seeds the base and has no return.
    """
    dates = sorted(by_date)
    if not dates:
        return []

    series: list[dict] = []
    index_value = BASE_VALUE
    previous: dict | None = None

    for d in dates:
        current = by_date[d]
        if previous is None:
            # Base date: an index needs somewhere to start, and the first
            # observation cannot have a return by definition.
            series.append({
                "date": d,
                "index_value": index_value,
                "return_pct": None,
                "cohort_count": len(current),
                "ad_count": sum(n for _, n in current.values()),
            })
            previous = current
            continue

        weighted_sum = 0.0
        weight_total = 0
        matched = 0
        for key, (median_now, n_now) in current.items():
            prior = previous.get(key)
            if prior is None:
                continue  # cohort is new today: weights change, returns do not
            median_prev, n_prev = prior
            if not median_prev or n_now < MIN_COHORT_ADS or n_prev < MIN_COHORT_ADS:
                continue
            # Weight by the smaller side so a cohort cannot buy influence by
            # ballooning overnight — the return is only as solid as its thinner end.
            weight = min(n_now, n_prev)
            weighted_sum += _clip(median_now / median_prev - 1.0) * weight
            weight_total += weight
            matched += 1

        if weight_total:
            day_return = weighted_sum / weight_total
            index_value *= 1.0 + day_return
        else:
            # No cohort survived both dates (a gap, or a very thin scope). Carry
            # the level forward rather than inventing a move.
            day_return = None

        series.append({
            "date": d,
            "index_value": round(index_value, 4),
            "return_pct": round(day_return * 100, 4) if day_return is not None else None,
            "cohort_count": matched,
            "ad_count": weight_total,
        })
        previous = current

    return series


def build_index(scope: str, scope_id: str | None = None) -> int:
    """Recompute and persist one scope's whole series. Returns rows written.

    Full rebuild rather than incremental append: the series is chained, so a
    late-arriving or corrected snapshot changes every value after it. Rebuilding
    a few thousand rows is far cheaper than reasoning about which suffix to
    invalidate — the same call the deal-score and analytics refreshes make.
    """
    series = compute_index(cohort_series(scope, scope_id))
    MarketIndex.objects.filter(scope=scope, scope_id=scope_id).delete()
    if not series:
        return 0
    MarketIndex.objects.bulk_create(
        [
            MarketIndex(
                scope=scope,
                scope_id=scope_id,
                date=row["date"],
                index_value=row["index_value"],
                return_pct=row["return_pct"],
                cohort_count=row["cohort_count"],
                ad_count=row["ad_count"],
            )
            for row in series
        ],
        batch_size=500,
    )
    return len(series)


def read_index(
    scope: str, scope_id: str | None = None, days: int | None = None
) -> list[dict]:
    """Persisted series for one scope, oldest first, optionally last N days."""
    qs = MarketIndex.objects.filter(scope=scope, scope_id=scope_id).order_by("-date")
    if days:
        qs = qs[:days]
    return [
        {
            "date": r.date.isoformat() if isinstance(r.date, date_cls) else r.date,
            "index_value": round(r.index_value, 2),
            "return_pct": r.return_pct,
            "cohort_count": r.cohort_count,
            "ad_count": r.ad_count,
        }
        for r in reversed(list(qs))
    ]
