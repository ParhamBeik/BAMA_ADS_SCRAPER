"""Three questions a raw median cannot answer.

* **Did the market move?** A median over all live listings moves whenever the
  *mix* of listings moves — inventory grew 21,688 -> 33,668 in eight days while
  the median fell 4.4%, with no way to tell how much was real. ``build_index``
  never compares different cars.
* **How long does a car take to leave the feed?** Averaging finished listings
  understates it, one-directionally: a car gone in three days is counted, a car
  sitting for ninety is still open and excluded. ``survival`` censors instead.
* **Which cars hold value?** ``depreciation_curve`` is a table of medians per
  model year, not a fitted line.

"Sold" appears nowhere: the feed cannot distinguish a sale from an expiry or a
withdrawal. Everything here is about *delisting*, the only event observed.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from apps.core import pricing
from apps.core.models import (
    Ad,
    Brand,
    DailyInventorySnapshot,
    ListingEpisode,
    MarketIndex,
    Model,
)
from apps.core.quality import (
    condition_band_q,
    exclude_unclear_price,
    verified,
    verified_by_ad,
    without_cohort_outliers,
)

# ---------------------------------------------------------------------------
# Matched-cohort chained price index
# ---------------------------------------------------------------------------
#
# The unit of measurement is the cohort — one (model, variant, year_jalali)
# group — and the only thing ever compared is the same cohort on two
# consecutive dates:
#
#   1. r_c     = median_d / median_prev - 1     one cohort's own move
#   2. R_d     = Σ(r_c · n_c) / Σ n_c           size-weighted average
#   3. index_d = index_prev · (1 + R_d)         chained, base 100
#
# A cohort existing on only one of the two dates contributes no r_c, so new
# listings and delistings shift the weights but cannot move the index — the
# property a raw median lacks.
#
# Input is DailyInventorySnapshot, already written daily, already keyed on
# year_jalali and already filtered through verified(). This adds no crawl load
# and no new quality path; it is arithmetic over rows that exist.

# A cohort must have this many ads on BOTH dates to contribute a return: a
# median from one or two cars is noise, not a price signal.
MIN_COHORT_ADS = 3

# Per-cohort daily moves beyond this are clipped, not trusted — a cohort
# emptying to one odd car is an artefact. Winsorising is deliberate: a mean of
# ratios has no upper bound and one bad cohort would set the headline number.
MAX_COHORT_RETURN = 0.5

BASE_VALUE = 100.0

# A day the crawler barely covered still produces cohort medians, and they are
# medians of whichever slice it happened to reach. Chaining through one reports
# the outage as a price move.
#
# Measured on production 2026-08-28: 2026-07-16, -17 and -18 were each built
# from ~1,950 ads against 21,733 on a normal day — 9% coverage — and were
# published at full confidence. The market series' single largest step
# (+1.52% on 07-26) sits on the far side of a four-day hole and is most of the
# front page's headline "+1.8%"; the Tondar 90 series drops 4.15% on the day
# its sample goes 69 -> 278, on a panel captioned "listings coming and going
# cannot move this index".
#
# A day below this share of the recent norm therefore contributes no return at
# all: the level is carried forward and the next well-covered day chains against
# the last well-covered one, exactly as a missing day already did. Half is
# deliberately loose — this is meant to catch an outage, not to police a quiet
# Friday, and real day-over-day feed drift is under 1%.
MIN_DAY_COVERAGE_RATIO = 0.5

# What "the recent norm" is measured over. Trailing rather than whole-series:
# the feed grew from ~6k to ~21k ads over August, so a series-wide median would
# call every early day an outage and every late one fine.
COVERAGE_REFERENCE_DAYS = 7

# Below this many accepted days there is nothing to compare against, so no day
# can be judged thin. Two points make a median that any third value beats.
MIN_COVERAGE_REFERENCE_DAYS = 3


# ---------------------------------------------------------------------------
# Segment axes
# ---------------------------------------------------------------------------
#
# Brand and model answer "which nameplate moved". These answer "which part of
# the market moved" — the question someone deciding whether to buy now or wait
# a month is actually asking, and the one the index could not be asked before.
#
# Every axis is derived from rows the warm tick already writes; none of them add
# a crawl, a column or a job. What they do add is one rule that has to hold:

# **A cohort's segment is fixed, not re-derived per day.** A price band assigned
# from each day's own median would move a cohort out of the band as its price
# rose, and the band's index would then be measuring reclassification rather
# than prices — the exact failure the matched-cohort design exists to avoid.
# Membership is therefore taken once, from the most recent snapshot, and applied
# to the whole history.

# Upper edges in toman. Chosen to split the market into bands that hold
# comparable numbers of cohorts rather than on round numbers alone: below 500M
# is the old-domestic floor, 500M-1B and 1-2B are where most of the volume sits,
# and everything above 5B is one band because the tail is thin.
PRICE_BAND_EDGES = (500_000_000, 1_000_000_000, 2_000_000_000, 5_000_000_000)

# Upper edges in years of age, measured against the newest model year present.
# Under 3 is near-new, 3-7 is the bulk of the used market, 8-15 is older stock
# and past that age stops being the thing that prices the car.
YEAR_BAND_EDGES = (3, 7, 15)

SEGMENT_SCOPES = (
    MarketIndex.Scope.PRICE_BAND,
    MarketIndex.Scope.YEAR_BAND,
    MarketIndex.Scope.BODY_TYPE,
)


def _banded(value, edges, prefix: str) -> str:
    """``value`` placed in the band ``edges`` describes, as a stable key."""
    for i, edge in enumerate(edges):
        if value < edge:
            return f"{prefix}{i}"
    return f"{prefix}{len(edges)}"


def price_band_label(key: str) -> str:
    """The toman range a ``price_band`` key stands for, for the UI to phrase."""
    i = int(key[1:])
    low = PRICE_BAND_EDGES[i - 1] if i else None
    high = PRICE_BAND_EDGES[i] if i < len(PRICE_BAND_EDGES) else None
    return f"{low or 0}-{high or ''}"


def year_band_label(key: str) -> str:
    i = int(key[1:])
    low = YEAR_BAND_EDGES[i - 1] if i else 0
    high = YEAR_BAND_EDGES[i] if i < len(YEAR_BAND_EDGES) else None
    return f"{low}-{high or ''}"


def _model_body_types() -> dict[int, str]:
    """Each model's modal body type, over the population every screen counts.

    Modal rather than "the body type of any ad": a handful of miscategorised
    listings must not decide which segment a whole model belongs to.
    """
    tally: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for model_id, body_type in (
        pricing.scorable_rows()
        .exclude(body_type="")
        .exclude(model_id__isnull=True)
        .values_list("model_id", "body_type")
        .iterator()
    ):
        tally[model_id][body_type] += 1
    return {
        model_id: max(counts.items(), key=lambda kv: kv[1])[0]
        for model_id, counts in tally.items()
    }


def cohort_segments() -> dict[tuple, dict[str, str]]:
    """``{cohort_key: {axis: segment_key}}``, fixed by the most recent snapshot.

    One pass over the latest day's rows plus one pass over the ad table for body
    types. Cohorts absent from the latest snapshot get no membership at all and
    therefore contribute to no segment — they are cohorts that no longer exist,
    and letting history alone put them in a band would have a segment's index
    driven by cars nobody is selling.
    """
    latest = (
        DailyInventorySnapshot.objects.order_by("-date")
        .values_list("date", flat=True).first()
    )
    if latest is None:
        return {}

    rows = list(
        DailyInventorySnapshot.objects.filter(
            date=latest, median_price__isnull=False, model_id__isnull=False
        ).values_list("model_id", "variant_id", "year_jalali", "median_price")
    )
    if not rows:
        return {}

    # Age is measured against the newest model year actually present, not
    # against the calendar: the Jalali year rolls over in March and a fixed
    # "current year" would silently age every car by one overnight.
    newest_year = max((r[2] for r in rows if r[2] is not None), default=None)
    body_types = _model_body_types()

    out: dict[tuple, dict[str, str]] = {}
    for model_id, variant_id, year, median_price in rows:
        segments: dict[str, str] = {
            MarketIndex.Scope.PRICE_BAND: _banded(median_price, PRICE_BAND_EDGES, "p"),
        }
        if year is not None and newest_year is not None:
            segments[MarketIndex.Scope.YEAR_BAND] = _banded(
                newest_year - year, YEAR_BAND_EDGES, "y"
            )
        if model_id in body_types:
            segments[MarketIndex.Scope.BODY_TYPE] = body_types[model_id]
        out[(model_id, variant_id, year)] = segments
    return out


def cohort_series(
    scope: str,
    scope_id: str | None = None,
    *,
    variant_id: int | None = None,
    year_jalali: int | None = None,
    segments: dict | None = None,
) -> dict:
    """``{date: {cohort_key: (median_price, ad_count)}}`` for one scope.

    ``variant_id`` / ``year_jalali`` narrow below the three persisted scopes.
    Nothing builds an index at that granularity on a schedule — a trim-level
    series for every trim in the catalogue would multiply what the warm tick
    writes every thirty minutes, for a question most sessions never ask — so
    those two are answered on demand instead. The snapshot rows are already
    keyed on both, so this is a filter, not a new aggregation.

    A segment scope filters on ``cohort_segments()`` in Python rather than in
    SQL, because membership is a property of the cohort *tuple* and matching a
    set of tuples in SQL costs more than the scan it would save. ``segments`` is
    accepted so a caller building every segment's series in one pass — which is
    what ``jobs.market_index`` does — resolves membership once rather than per
    scope.
    """
    qs = DailyInventorySnapshot.objects.filter(
        median_price__isnull=False, model_id__isnull=False
    )
    if scope == MarketIndex.Scope.BRAND:
        qs = qs.filter(model__brand__slug=scope_id)
    elif scope == MarketIndex.Scope.MODEL:
        qs = qs.filter(model_id=scope_id)
    if variant_id is not None:
        qs = qs.filter(variant_id=variant_id)
    if year_jalali is not None:
        qs = qs.filter(year_jalali=year_jalali)

    membership = None
    if scope in SEGMENT_SCOPES:
        membership = cohort_segments() if segments is None else segments

    by_date: dict = defaultdict(dict)
    for d, model_id, variant_id, year, median_price, ad_count in qs.values_list(
        "date", "model_id", "variant_id", "year_jalali", "median_price", "ad_count"
    ):
        key = (model_id, variant_id, year)
        if membership is not None and membership.get(key, {}).get(scope) != scope_id:
            continue
        by_date[d][key] = (median_price, ad_count)
    return by_date


def compute_index(by_date: dict) -> list[dict]:
    """Chain per-date cohort medians into a series, oldest first.

    Gaps are bridged: consecutive *well-covered* dates are chained, so a day the
    worker did not run — or only half ran — costs resolution, never a break in
    the series.

    Each point carries the two facts a reader needs to know whether to believe
    its move: ``gap_days`` is how many calendar days it is chained across (1 on
    a normal day), and ``low_coverage`` marks a day the crawler under-covered,
    whose return is withheld rather than published. See
    ``MIN_DAY_COVERAGE_RATIO``.
    """
    dates = sorted(by_date)
    if not dates:
        return []

    series: list[dict] = []
    index_value = BASE_VALUE
    previous: dict | None = None
    previous_date = None
    # Totals of the days actually chained, newest last. The yardstick a day's
    # own coverage is judged against.
    reference: list[int] = []

    for d in dates:
        current = by_date[d]
        day_ads = sum(n for _, n in current.values())
        gap_days = (d - previous_date).days if previous_date else None

        # Judged before this day joins the reference, or an outage would move
        # the very norm that is supposed to catch it.
        thin = (
            len(reference) >= MIN_COVERAGE_REFERENCE_DAYS
            and day_ads < MIN_DAY_COVERAGE_RATIO * statistics.median(reference)
        )

        if previous is None:
            # The base date: an index needs somewhere to start, and the first
            # observation cannot have a return by definition.
            series.append({
                "date": d, "index_value": index_value, "return_pct": None,
                "cohort_count": len(current), "ad_count": day_ads,
                "gap_days": None, "low_coverage": False,
            })
            previous, previous_date = current, d
            reference.append(day_ads)
            continue

        if thin:
            # Carry the level and, crucially, do NOT advance `previous`: the
            # next well-covered day then chains against the last well-covered
            # one instead of against a slice of the market.
            # ad_count/cohort_count mean "what stood behind this day's return",
            # and a withheld return has nothing behind it. The day's raw sample
            # is not lost — it is the reason `low_coverage` is set.
            series.append({
                "date": d, "index_value": round(index_value, 4), "return_pct": None,
                "cohort_count": 0, "ad_count": 0,
                "gap_days": gap_days, "low_coverage": True,
            })
            continue

        weighted_sum = 0.0
        weight_total = 0
        matched = 0
        for key, (median_now, n_now) in current.items():
            prior = previous.get(key)
            if prior is None:
                continue  # new today: weights change, returns do not
            median_prev, n_prev = prior
            if not median_prev or n_now < MIN_COHORT_ADS or n_prev < MIN_COHORT_ADS:
                continue
            # Weight by the smaller side so a cohort cannot buy influence by
            # ballooning overnight — the return is only as solid as its thinner end.
            weight = min(n_now, n_prev)
            change = median_now / median_prev - 1.0
            weighted_sum += max(-MAX_COHORT_RETURN, min(MAX_COHORT_RETURN, change)) * weight
            weight_total += weight
            matched += 1

        if weight_total:
            day_return = weighted_sum / weight_total
            index_value *= 1.0 + day_return
        else:
            # No cohort survived both dates. Carry the level forward rather than
            # inventing a move.
            day_return = None

        series.append({
            "date": d,
            "index_value": round(index_value, 4),
            "return_pct": round(day_return * 100, 4) if day_return is not None else None,
            "cohort_count": matched,
            "ad_count": weight_total,
            "gap_days": gap_days,
            "low_coverage": False,
        })
        previous, previous_date = current, d
        reference.append(day_ads)
        del reference[:-COVERAGE_REFERENCE_DAYS]

    return series


def build_index(scope: str, scope_id: str | None = None, *, segments: dict | None = None) -> int:
    """Recompute and persist one scope's whole series. Returns rows written.

    Full rebuild, not incremental append: the series is chained, so a corrected
    snapshot changes every value after it.

    ``segments`` is passed straight through so a caller rebuilding every segment
    of an axis resolves membership once instead of once per segment.
    """
    series = compute_index(cohort_series(scope, scope_id, segments=segments))
    MarketIndex.objects.filter(scope=scope, scope_id=scope_id).delete()
    if not series:
        return 0
    MarketIndex.objects.bulk_create(
        [MarketIndex(scope=scope, scope_id=scope_id, **row) for row in series],
        batch_size=500,
    )
    return len(series)


def read_index(scope: str, scope_id: str | None = None, days: int | None = None) -> list[dict]:
    """Persisted series for one scope, oldest first, optionally the last N days."""
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
            # Both are needed to read a point honestly: how many calendar days
            # this step spans, and whether the day under it was covered enough
            # to have an opinion at all.
            "gap_days": r.gap_days,
            "low_coverage": r.low_coverage,
        }
        for r in reversed(list(qs))
    ]


# ---------------------------------------------------------------------------
# Which scopes moved
# ---------------------------------------------------------------------------

# A scope needs this many days on record inside the window before its "move" is
# a move rather than the gap between two arbitrary days.
MOVER_MIN_DAYS = 3

# ...and this many cohorts behind the last day, for the same reason the index
# itself refuses thin scopes. Matches jobs.MIN_SCOPE_COHORTS, which decides
# which scopes get a series built at all.
MOVER_MIN_COHORTS = 5

# How many days at the end of the window count as "recently". Seven, so the
# short leg is a week — short enough to turn before the long leg does, long
# enough not to be one noisy Friday.
SHORT_WINDOW_DAYS = 7

# Below this the short and long legs are both flat and calling their
# disagreement a turn would flag every scope on the board. In percent per day:
# 0.05%/day is ~1.5% a month, which is the smallest move worth a reader's
# attention given day-over-day feed drift is under 1%.
MIN_TURN_SLOPE = 0.05

# A Theil-Sen slope is the median of every pairwise slope, so the share of those
# pairs that agree with it in sign says how consistent the trend is. 0.5 is a
# coin flip; this is the bar below which a slope is not called a direction.
MIN_SLOPE_AGREEMENT = 0.6


def theil_sen(values: list[float]) -> tuple[float, float]:
    """Robust trend of an evenly-indexed series: ``(slope, agreement)``.

    The median of all pairwise slopes, rather than a least-squares fit. The
    series this runs on is chained and carries thin days whose level is simply
    carried forward, and one such flat step drags an OLS line toward zero while
    a single spike drags it the other way; the median of pairs ignores both.

    ``agreement`` is the fraction of pairwise slopes sharing the median's sign —
    a free measure of how consistent the trend is, since the pairs are computed
    anyway. A steadily rising series scores near 1.0, noise scores near 0.5.

    Slope is per index step (one point, i.e. one *available* day) rather than per
    calendar day. That is what the reader sees plotted, and normalising by
    calendar gaps would let one long hole rescale the whole trend.
    """
    n = len(values)
    if n < 2:
        return 0.0, 0.0
    slopes = [
        (values[j] - values[i]) / (j - i)
        for i in range(n - 1)
        for j in range(i + 1, n)
    ]
    slope = statistics.median(slopes)
    if slope == 0:
        return 0.0, 0.0
    agreeing = sum(1 for s in slopes if (s > 0) == (slope > 0))
    return slope, agreeing / len(slopes)


def _trend(values: list[float]) -> dict:
    """A scope's direction, its consistency, and whether it is turning.

    Three numbers instead of the one this used to publish. ``change_pct`` — the
    ratio of the last point to the first — is a chord between two arbitrary days:
    it is set entirely by which day the window happens to open on, and a scope
    that fell for three weeks and has risen for four days reports as a faller.
    It is still returned, because it is what "prices are up 3% this month"
    means, but it no longer decides the ranking.

    Slopes are expressed in percent of the window's mean level per step, so a
    2,000-toman-a-day move on a cheap cohort and on an expensive one are
    comparable.
    """
    base = statistics.fmean(values) if values else 0.0
    scale = (100.0 / base) if base else 0.0
    slope, agreement = theil_sen(values)
    short_values = values[-SHORT_WINDOW_DAYS:]
    short_slope, short_agreement = theil_sen(short_values)

    slope_pct = slope * scale
    short_pct = short_slope * scale
    # A turn is the long leg and the recent leg disagreeing about direction,
    # with both legs actually moving and the recent one consistent enough to
    # believe. Without the agreement bar a single last-day blip inverts the
    # short slope and every quiet scope reports a reversal.
    turning = (
        len(short_values) >= 3
        and abs(slope_pct) >= MIN_TURN_SLOPE
        and abs(short_pct) >= MIN_TURN_SLOPE
        and (slope_pct > 0) != (short_pct > 0)
        and short_agreement >= MIN_SLOPE_AGREEMENT
    )
    return {
        "slope_pct": round(slope_pct, 4),
        "slope_agreement": round(agreement, 3),
        "recent_slope_pct": round(short_pct, 4),
        "recent_days": len(short_values),
        "turning": turning,
        # Which way the turn goes, so the UI never has to re-derive it from two
        # signed numbers and get the edge case wrong.
        "turning_up": turning and short_pct > 0,
        # A direction only when the trend is consistent enough to be one.
        "direction": (
            "flat" if abs(slope_pct) < MIN_TURN_SLOPE or agreement < MIN_SLOPE_AGREEMENT
            else "up" if slope_pct > 0 else "down"
        ),
    }


def _scope_names(scope: str, ids: list[str]) -> dict[str, dict]:
    """Human labels for a set of scope ids, resolved in one query per scope.

    Segment scopes resolve without a query: their ids either *are* the label
    (a body type is Bama's own word for it) or encode a range the UI phrases
    from the numeric bounds this returns. Persian prose is composed in the UI,
    not here — the same rule the fair-price components follow.
    """
    if scope == MarketIndex.Scope.BRAND:
        rows = Brand.objects.filter(slug__in=ids).values_list("slug", "name_fa")
        return {str(slug): {"name": name, "brand_name": None} for slug, name in rows}
    if scope == MarketIndex.Scope.MODEL:
        rows = (
            Model.objects.filter(pk__in=[i for i in ids if i.isdigit()])
            .values_list("pk", "name_fa", "brand__name_fa")
        )
        return {str(pk): {"name": name, "brand_name": brand} for pk, name, brand in rows}
    if scope == MarketIndex.Scope.PRICE_BAND:
        return {i: {"name": i, "brand_name": None, "bounds": price_band_label(i)}
                for i in ids}
    if scope == MarketIndex.Scope.YEAR_BAND:
        return {i: {"name": i, "brand_name": None, "bounds": year_band_label(i)}
                for i in ids}
    if scope == MarketIndex.Scope.BODY_TYPE:
        return {i: {"name": i, "brand_name": None} for i in ids}
    return {}


def movers(scope: str, *, days: int = 30, limit: int = 10) -> dict:
    """Brands or models ranked by how far their price index moved.

    Reads the series the warm tick already persists for every scope with enough
    live cohorts — this adds no computation to the pipeline, it ranks what is
    there. The window is applied to the *stored* dates, so a scope whose series
    starts inside the window is measured across the part of it that exists and
    reports how many days that was.

    Every row carries ``ad_count`` and ``cohort_count``. A 4% move off three
    cohorts and one off forty are not the same claim, and a leaderboard that
    hides the difference is how the thinnest scope reaches the top of it.
    """
    cutoff = timezone.now().date() - timedelta(days=days)
    rows = (
        MarketIndex.objects.filter(scope=scope, date__gte=cutoff)
        .order_by("scope_id", "date")
        .values_list("scope_id", "date", "index_value", "ad_count", "cohort_count",
                     "low_coverage")
    )

    by_scope: dict[str, list] = defaultdict(list)
    for scope_id, day, value, ads, cohorts, low in rows:
        by_scope[str(scope_id)].append((day, value, ads, cohorts, low))

    ranked = []
    for scope_id, points in by_scope.items():
        if len(points) < MOVER_MIN_DAYS:
            continue
        first, last = points[0], points[-1]
        if not first[1]:
            continue
        # The sample test asks the last day that actually contributed one. An
        # under-covered day carries the level forward with nothing behind it, so
        # judging the scope on its counts would drop every scope whose most
        # recent day happened to land in a crawler outage.
        sampled = next((p for p in reversed(points) if not p[4]), last)
        if sampled[3] < MOVER_MIN_COHORTS:
            continue
        values = [v for _, v, _, _, _ in points]
        row = {
            "scope_id": scope_id,
            "change_pct": round((last[1] / first[1] - 1) * 100, 2),
            "latest_index": round(last[1], 2),
            "days": len(points),
            "first_date": first[0].isoformat(),
            "last_date": last[0].isoformat(),
            "ad_count": sampled[2],
            "cohort_count": sampled[3],
            # Enough to draw a sparkline beside the row, without a second call
            # per row. Rounded here so the payload is not full of float noise.
            "series": [round(v, 2) for v in values],
        }
        row.update(_trend(values))
        ranked.append(row)

    names = _scope_names(scope, [r["scope_id"] for r in ranked])
    for row in ranked:
        row.update(names.get(row["scope_id"], {"name": row["scope_id"], "brand_name": None}))

    # `change_pct` still ranks the two columns, because "which cars changed most
    # over this window" is a real question and the chord is its honest answer.
    # What it is NOT is a trend: a scope that rose for a fortnight and has been
    # flat since reports the same 100% as one still climbing. That distinction
    # used to be unavailable, so the board presented the chord *as* a direction.
    # Every row now also carries `slope_pct`, `direction` and `turning` — and
    # the turning scopes get their own list, because a scope whose recent leg
    # has just diverged from its long one sits nowhere near either end of a
    # change ranking, which is exactly why a two-column board could never
    # surface the thing a buyer most wants to know.
    ranked.sort(key=lambda r: r["change_pct"], reverse=True)
    if not ranked:
        return {"available": False, "reason": "insufficient_index_history",
                "scope": scope, "requested_days": days}
    # Split on the sign, not on the ends of the list. Slicing the two ends would
    # put the same rows in both columns whenever fewer than 2*limit scopes
    # qualify, and would file a scope that rose 2% under "biggest fallers".
    return {
        "available": True,
        "scope": scope,
        "requested_days": days,
        "scopes_ranked": len(ranked),
        "risers": [r for r in ranked if r["change_pct"] > 0][:limit],
        # Steepest fall first in its own column, rather than the reader having
        # to scan a table upwards.
        "fallers": [r for r in reversed(ranked) if r["change_pct"] < 0][:limit],
        # Sharpest reversal first. Ordered on the recent leg because that is the
        # half that just changed its mind.
        "turning": sorted(
            (r for r in ranked if r["turning"]),
            key=lambda r: abs(r["recent_slope_pct"]), reverse=True,
        )[:limit],
    }


# ---------------------------------------------------------------------------
# Time to delist (Kaplan-Meier)
# ---------------------------------------------------------------------------

# Below this a survival curve is a shape drawn through noise. Higher than the
# deal score's peer minimum: a median with confidence bounds needs more evidence
# than a median alone.
MIN_EPISODES = 20

# A listing that appears and vanishes within hours is far more often a posting
# error or a moderation removal than a car that sold that fast.
MIN_EPISODE_HOURS = 6


def clean_start() -> datetime:
    """Earliest episode start whose *end* date is trustworthy.

    Removal used to be detectable only on days a full sweep happened to finish
    (11 of 28 attempts), so episodes ended in lumps — up to 6,873 on one day,
    nothing for a week at a time. A curve fitted to that reads the sweep
    schedule, not the market: every cohort returned a median of exactly 21.02
    days. Earlier rows are kept for provenance, just not treated as evidence.
    """
    return datetime.combine(
        datetime.fromisoformat(settings.BAMA_EPISODE_CLEAN_START).date(),
        time.min,
        tzinfo=dt_timezone.utc,
    )


@dataclass
class Observation:
    """One listing's contribution: how long it lasted and whether it finished."""

    days: float
    delisted: bool  # False => still listed, i.e. censored


@dataclass
class SurvivalCurve:
    times: list[float] = field(default_factory=list)
    survival: list[float] = field(default_factory=list)
    at_risk: list[int] = field(default_factory=list)
    n: int = 0
    delisted: int = 0
    censored: int = 0
    # The longest any listing in this sample has been watched. It bounds what
    # the curve can say: past it there is no evidence either way.
    max_followup: float = 0.0

    def probability_still_listed(self, day: float) -> float:
        current = 1.0
        for t, s in zip(self.times, self.survival, strict=True):
            if t > day:
                break
            current = s
        return current

    def median_days(self) -> float | None:
        """First day survival drops to 0.5 or below.

        None when the curve never reaches it: with most listings still open, the
        honest answer is "longer than we have watched", not an extrapolation.
        """
        for t, s in zip(self.times, self.survival, strict=True):
            if s <= 0.5:
                return t
        return None

    def as_dict(self) -> dict:
        median = self.median_days()
        return {
            "n": self.n,
            "delisted": self.delisted,
            "censored": self.censored,
            "median_days": median,
            # When the curve never falls to 0.5 the median is not missing, it is
            # censored: more than half of these listings are still up, so the
            # honest answer is "longer than we have watched" and this is that
            # bound. Rendering the median as an em dash beside a large "simple
            # average, misleading" figure left the wrong number as the only one
            # on the panel with a value.
            "median_days_at_least": None if median is not None else round(self.max_followup, 1),
            "observed_days": round(self.max_followup, 1),
            "curve": [
                {"day": t, "still_listed": round(s, 4), "at_risk": r}
                for t, s, r in zip(self.times, self.survival, self.at_risk, strict=True)
            ],
        }


def kaplan_meier(observations: list[Observation]) -> SurvivalCurve:
    """Survival curve with right-censoring.

    At each day a delisting happened, the surviving fraction is
    ``1 - delisted/at_risk``; the running product is the curve. A censored
    listing stays in ``at_risk`` up to when it was last seen and then simply
    leaves — which is what keeps still-listed cars from reading as instant sales.

    One sorted pass rather than a scan of every observation per event day. The
    obvious form of this — ``sum(1 for o in observations if o.days >= day)``
    inside the loop — is O(event days x n), and both grow with the cohort: a
    model with a few thousand episodes and nearly as many distinct tenures made
    the liquidity endpoint quadratic in its own popularity. ``at_risk`` is just
    the count of observations not yet passed, so walking the days downward keeps
    it as a running total and the numbers come out identical.
    """
    curve = SurvivalCurve(n=len(observations))
    if not observations:
        return curve

    curve.delisted = sum(1 for o in observations if o.delisted)
    curve.censored = curve.n - curve.delisted
    curve.max_followup = max(o.days for o in observations)

    events_by_day: dict[float, int] = defaultdict(int)
    total_by_day: dict[float, int] = defaultdict(int)
    for o in observations:
        total_by_day[o.days] += 1
        if o.delisted:
            events_by_day[o.days] += 1

    # Walk every distinct tenure from the longest down, accumulating how many
    # listings were still being watched at that point. `at_risk` for day d is
    # everything with `days >= d`, which is exactly this running total.
    at_risk_by_day: dict[float, int] = {}
    at_risk = 0
    for day in sorted(total_by_day, reverse=True):
        at_risk += total_by_day[day]
        at_risk_by_day[day] = at_risk

    survival = 1.0
    for day in sorted(events_by_day):
        at_risk = at_risk_by_day[day]
        if at_risk == 0:  # pragma: no cover - a day with an event has someone at risk
            continue
        survival *= 1 - events_by_day[day] / at_risk
        curve.times.append(day)
        curve.survival.append(survival)
        curve.at_risk.append(at_risk)
    return curve


def _episode_qs(*, model_id=None, variant_id=None, year_jalali=None, clean=True):
    qs = verified_by_ad(ListingEpisode.objects.select_related("ad"))
    qs = (
        qs.filter(started_at__gte=clean_start()) if clean
        else qs.filter(started_at__lt=clean_start())
    )
    if model_id:
        qs = qs.filter(ad__model_id=model_id)
    if variant_id:
        qs = qs.filter(ad__variant_id=variant_id)
    if year_jalali:
        qs = qs.filter(ad__year_jalali=year_jalali)
    return qs


def survival(*, model_id=None, variant_id=None, year_jalali=None) -> dict:
    """Time-to-delist for a cohort, censoring listings that are still live."""
    now = timezone.now()
    observations = []
    for started_at, ended_at in _episode_qs(
        model_id=model_id, variant_id=variant_id, year_jalali=year_jalali
    ).values_list("started_at", "ended_at"):
        days = ((ended_at or now) - started_at).total_seconds() / 86400
        if days * 24 >= MIN_EPISODE_HOURS:
            observations.append(Observation(round(days, 2), ended_at is not None))

    if len(observations) < MIN_EPISODES:
        # Two different answers the UI must not conflate: a thin cohort may
        # simply never have enough listings, whereas one starved by the cutoff
        # fills up on its own as clean episodes accumulate.
        excluded = _episode_qs(
            model_id=model_id, variant_id=variant_id, year_jalali=year_jalali, clean=False,
        ).count()
        starved = excluded >= MIN_EPISODES
        result = {
            "available": False,
            "reason": "insufficient_clean_history" if starved else "insufficient_episodes",
            "n": len(observations),
            "required": MIN_EPISODES,
        }
        if starved:
            result["clean_start"] = clean_start().date().isoformat()
            result["excluded_episodes"] = excluded
        return result

    curve = kaplan_meier(observations)
    result = curve.as_dict()
    result["available"] = True
    # The span this was computed over, stated the way the index states its own.
    # Every figure below is bounded by it, and a "4 days" with nothing saying it
    # came from a fortnight of history reads as a property of the market.
    result["clean_start"] = clean_start().date().isoformat()
    result["clean_days"] = max(0, (now - clean_start()).days)
    # Fixed horizons as well as the median. The median is one order statistic
    # and goes degenerate when removal dates cluster — which they do in
    # backfilled history — and "odds it is still here in a month" is usually the
    # more useful number anyway.
    for horizon in (7, 14, 30, 60, 90):
        result[f"still_listed_at_{horizon}d"] = round(
            curve.probability_still_listed(horizon), 4
        )
    # Reported so the difference is visible rather than asserted: this is what a
    # naive AVG(days) would have produced.
    finished = [o.days for o in observations if o.delisted]
    result["naive_mean_days_finished_only"] = (
        round(sum(finished) / len(finished), 1) if finished else None
    )
    return result


# ---------------------------------------------------------------------------
# How fast a model's listings leave the feed
# ---------------------------------------------------------------------------

# Per model. Lower than MIN_EPISODES because this is one proportion rather than
# a whole curve, but still high enough that one lucky week cannot top the board.
TURNOVER_MIN_EPISODES = 15

# The shortest window worth *clamping down to*. Below a week, "share that left
# within N days" is a statement about one weekend, and silently answering that
# when a month was asked for would be worse than saying there is no history yet.
# It does not bound what a caller may ask for explicitly.
MIN_TURNOVER_WINDOW_DAYS = 7


def _turnover_tally(days: int, cutoff) -> tuple[dict, dict]:
    """``({model_id: {n, left}}, {model_id: (name, brand)})`` — the raw counts.

    Split out of ``turnover`` so the deal board can join the same numbers onto a
    listing without going through the leaderboard's ranking and truncation. One
    definition of "left the feed within N days", so a rate printed on a card and
    the same rate on the home page cannot disagree.
    """
    tally: dict = defaultdict(lambda: {"n": 0, "left": 0})
    meta: dict = {}
    for model_id, model_name, brand_name, started_at, ended_at in (
        _episode_qs().filter(started_at__lte=cutoff).values_list(
            "ad__model_id", "ad__model__name_fa", "ad__model__brand__name_fa",
            "started_at", "ended_at",
        )
    ):
        if model_id is None:
            continue
        entry = tally[model_id]
        entry["n"] += 1
        if ended_at is not None and (ended_at - started_at) <= timedelta(days=days):
            entry["left"] += 1
        meta.setdefault(model_id, (model_name, brand_name))
    return tally, meta


def turnover_rates(*, days: int = 30) -> dict[int, dict]:
    """``{model_id: {"left_pct", "n", "window_days"}}`` for every eligible model.

    What the deal board reads. A discount is not a deal on its own: 15% off a
    car that leaves the feed in ten days and 15% off one that sits for ninety
    are different propositions, and the board presented them identically because
    liquidity lived on a screen the buyer never had open at the same time.

    Same clamp and the same minimum as ``turnover`` — a rate computed over five
    episodes is not one to print on a card.
    """
    now = timezone.now()
    clean_days = max(0, (now - clean_start()).days)
    days = min(days, clean_days // 2)
    if days < MIN_TURNOVER_WINDOW_DAYS:
        return {}
    tally, _ = _turnover_tally(days, now - timedelta(days=days))
    return {
        model_id: {
            "left_pct": round(entry["left"] / entry["n"] * 100, 1),
            "n": entry["n"],
            "window_days": days,
        }
        for model_id, entry in tally.items()
        if entry["n"] >= TURNOVER_MIN_EPISODES
    }


def turnover(*, days: int = 30, limit: int = 10) -> dict:
    """Share of each model's listings that left the feed within ``days``.

    Counted only over episodes that *started* at least ``days`` ago, so every
    listing in the denominator has had the full window in which to leave. That
    is the whole trick: it makes the number comparable across models with no
    censoring correction and no survival curve, because there is nothing left to
    censor — each listing either left inside the window or it did not.

    Deliberately not a mean time-to-delist over finished listings. That number
    counts the cars that went quickly and silently drops the ones still sitting,
    which is why ``survival`` reports it only as a foil (see
    ``naive_mean_days_finished_only``).

    "Left the feed", never "sold": Bama publishes no reason, and a delisting is
    a sale, an expiry or a withdrawal with no way to tell which.
    """
    now = timezone.now()
    # The window is *clamped* to the trustworthy history rather than refused,
    # the same way the price index clamps a 90-day request to the 44 days it
    # has. It used to refuse outright, and the home page asks for 30 days by
    # default against 14 days of clean history — so the turnover panel was empty
    # on the front page for every reader, every time, and only the 7-day chip
    # could produce anything. An empty panel taught nobody that a shorter window
    # worked.
    #
    # Half the clean history, not all of it. The denominator is episodes that
    # *started* at least `days` ago and after the clean cut, so its width is
    # exactly `clean_days - days`: clamping to the full clean history would
    # leave a zero-width pool and answer "no listings" for a different reason.
    # Splitting it evenly gives the window as much room to close as there are
    # listings to watch close in it.
    clean_days = max(0, (now - clean_start()).days)
    requested_days = days
    days = min(days, clean_days // 2)
    # The floor applies only to a window we shortened. A caller asking for three
    # days is asking a narrow question and is entitled to the narrow answer;
    # what must not happen is *silently* answering a three-day question when
    # thirty days were requested.
    if days < requested_days and days < MIN_TURNOVER_WINDOW_DAYS:
        return {"available": False, "reason": "window_exceeds_clean_history",
                "window_days": requested_days, "clean_days": clean_days,
                "clean_start": clean_start().date().isoformat()}
    cutoff = now - timedelta(days=days)
    tally, meta = _turnover_tally(days, cutoff)

    ranked = [
        {
            "model_id": model_id,
            "name": meta[model_id][0],
            "brand_name": meta[model_id][1],
            "n": entry["n"],
            "left_within_window": entry["left"],
            "left_pct": round(entry["left"] / entry["n"] * 100, 1),
        }
        for model_id, entry in tally.items()
        if entry["n"] >= TURNOVER_MIN_EPISODES
    ]
    if not ranked:
        return {"available": False, "reason": "insufficient_episodes",
                "window_days": days, "required": TURNOVER_MIN_EPISODES}

    ranked.sort(key=lambda r: r["left_pct"], reverse=True)
    return {
        "available": True,
        "window_days": days,
        "requested_days": requested_days,
        "clamped": days < requested_days,
        "clean_days": clean_days,
        "models_ranked": len(ranked),
        "clean_start": clean_start().date().isoformat(),
        "fastest": ranked[:limit],
    }


# ---------------------------------------------------------------------------
# Arrivals: how much new supply a scope is taking on
# ---------------------------------------------------------------------------


def arrivals(*, days: int = 30, limit: int = 10) -> dict:
    """Models with the most newly-listed cars over the window.

    ``DailyInventorySnapshot.new_count`` is ads first seen on that date, already
    written per cohort every day, so this is a sum rather than a scan of the ad
    table. Paired with ``turnover`` on the home page it is the supply half of
    the picture: a lot arriving and little leaving is a market softening.
    """
    snapshots = DailyInventorySnapshot.objects.filter(model_id__isnull=False)
    latest_date = snapshots.order_by("-date").values_list("date", flat=True).first()
    if latest_date is None:
        return {"available": False, "reason": "insufficient_listings",
                "window_days": days}

    cutoff = latest_date - timedelta(days=days)
    new_by_model: dict[int, int] = defaultdict(int)
    meta: dict = {}
    for model_id, model_name, brand_name, new_count in snapshots.filter(
        date__gte=cutoff
    ).values_list("model_id", "model__name_fa", "model__brand__name_fa", "new_count"):
        new_by_model[model_id] += new_count or 0
        meta.setdefault(model_id, (model_name, brand_name))

    # Inventory is a level, not a total: summing ad_count over the window would
    # count the same standing car once per day it stayed listed. Only the most
    # recent snapshot date describes what is on the market now.
    listed_now: dict[int, int] = defaultdict(int)
    for model_id, ad_count in snapshots.filter(date=latest_date).values_list(
        "model_id", "ad_count"
    ):
        listed_now[model_id] += ad_count

    ranked = [
        {
            "model_id": model_id,
            "name": meta[model_id][0],
            "brand_name": meta[model_id][1],
            "new_listings": total_new,
            "listed_now": listed_now.get(model_id, 0),
        }
        for model_id, total_new in new_by_model.items()
        if total_new > 0
    ]
    if not ranked:
        return {"available": False, "reason": "insufficient_listings",
                "window_days": days}
    ranked.sort(key=lambda r: r["new_listings"], reverse=True)
    return {"available": True, "window_days": days, "models": ranked[:limit]}


# ---------------------------------------------------------------------------
# Buy or wait: the three panels, read as one position
# ---------------------------------------------------------------------------
#
# The front page had the price index, arrivals and turnover as three independent
# cards and left the reader to combine them. They only mean something together:
# a rising index with inventory building is a very different market from a
# rising index with stock clearing, and "should I buy now or wait" is a question
# about both at once.

# How far either side of 1.0 counts as balanced. Arrivals and departures are
# counted over the same window, so the ratio is self-normalising and 1.0 is a
# real threshold rather than a chosen one — but it is a ratio of two noisy
# counts, and without a dead band the read would flip between labels week to
# week on no real change.
ABSORPTION_DEAD_BAND = 0.05

# Below this many episodes on either side the ratio is two small numbers
# dividing into a large opinion.
MIN_FLOW_EPISODES = 30


def market_read(*, days: int = 30) -> dict:
    """Where the market is, as one position plus the evidence for it.

    Two independent facts, deliberately not blended into a score:

    * **Price direction** — the slope of the composition-controlled index, which
      cannot move because the mix of listings moved.
    * **Absorption** — departures divided by arrivals over the same window.
      Above 1.0, stock is clearing faster than it arrives and the market is
      tightening; below, inventory is building. A ratio rather than either count
      alone, because both grow with the size of the crawl and neither is
      interpretable on its own.

    The position is the pair, named. It is not a recommendation and does not
    pretend to be a forecast: it says what the market is doing now, and every
    number behind it rides along so a reader can disagree.
    """
    series = read_index(MarketIndex.Scope.MARKET, None, days=days)
    contributing = [p for p in series if not p["low_coverage"]]
    if len(contributing) < MOVER_MIN_DAYS:
        return {"available": False, "reason": "insufficient_index_history",
                "window_days": days, "days_on_record": len(contributing)}

    trend = _trend([p["index_value"] for p in series])

    # Both flows over the same window and the same population, so the ratio is
    # a like-for-like comparison. Episodes, not snapshot counts: an episode is
    # one listing's life, which is what "arrived" and "left" mean.
    now = timezone.now()
    cutoff = now - timedelta(days=days)
    episodes = _episode_qs()
    arrived = episodes.filter(started_at__gte=cutoff).count()
    departed = episodes.filter(ended_at__gte=cutoff).count()

    if arrived < MIN_FLOW_EPISODES or departed < MIN_FLOW_EPISODES:
        absorption = None
        flow = "unknown"
    else:
        absorption = departed / arrived
        flow = (
            "balanced" if abs(absorption - 1.0) <= ABSORPTION_DEAD_BAND
            else "tightening" if absorption > 1.0 else "building"
        )

    # The four readings this data can actually support. Anything finer would be
    # a claim about causes, and the feed carries none.
    if trend["direction"] == "up" and flow == "tightening":
        position = "sellers_market"
    elif trend["direction"] == "down" and flow == "building":
        position = "buyers_market"
    elif trend["direction"] == "flat" and flow == "balanced":
        position = "stable"
    else:
        position = "mixed"

    return {
        "available": True,
        "window_days": days,
        "position": position,
        # Named separately from `position` so the UI can show the two facts even
        # when their combination lands in "mixed", which is the common case and
        # the one where the reader most needs the parts.
        "price_direction": trend["direction"],
        "price_trend": trend,
        "flow": flow,
        "absorption": round(absorption, 3) if absorption is not None else None,
        "arrived": arrived,
        "departed": departed,
        "clean_start": clean_start().date().isoformat(),
        "index_days": len(contributing),
        "latest_index": series[-1]["index_value"],
    }


# ---------------------------------------------------------------------------
# What a scope's prices actually look like
# ---------------------------------------------------------------------------

# Below this a histogram is a picture of a handful of cars, and its percentiles
# are three numbers wearing a statistic's clothes.
MIN_DISTRIBUTION_ADS = 12

# Upper bound on bars across the p10-p90 band. Enough to show a second peak (two
# trims priced apart inside one model is common) without drawing a comb. The
# actual count follows the sample: 24 fixed bars over a 25-ad model year draws
# mostly bars of height one, which reads as structure a reader will interpret and
# is only the sample's own noise.
HISTOGRAM_BUCKETS = 24
MIN_HISTOGRAM_BUCKETS = 6


def price_distribution(
    *,
    brand: str | None = None,
    model_id: int | None = None,
    variant_id: int | None = None,
    year_jalali: int | None = None,
    condition: str | None = None,
    mileage_bucket: int | None = None,
) -> dict:
    """The asking-price shape of any scope, from the whole market down to a
    single model year, plus the city and model-year facets that go with it.

    Population is the one the Explorer lists — verified, ACTIVE, priced, cohort
    outliers dropped — so a median quoted here describes cars a reader can go
    and find. Installment ads are excluded: their price field holds a down
    payment, and a distribution that mixes those in reports a phantom cluster of
    cheap cars that do not exist.

    The histogram spans p10-p90 rather than min-max for the same reason the peer
    bar does: one 5.8-trillion-toman typo listing would put every real car in
    the first bucket. What falls outside is counted, not hidden.

    ``condition`` and ``mileage_bucket`` answer the question the scope filters
    alone cannot: what does *this* car cost. Without them one histogram averages
    a 300,000 km repainted example with a 20,000 km clean one and reports their
    combined spread as "what this model costs", which describes neither car.

    Two modes, because filtering alone is not enough. Where the slice itself has
    ``MIN_DISTRIBUTION_ADS`` the answer is **filtered** — real listings, nothing
    modelled. Below that (which is most trims once both conditions are applied)
    it is **adjusted**: the unconditioned distribution shifted by the pooled
    haircuts ``pricing`` already measures across the whole catalogue. The two
    are not equivalent and the payload says which one produced it, because a
    reader is entitled to know whether they are looking at cars or at an
    estimate of cars.
    """
    qs = without_cohort_outliers(verified(Ad.objects)).filter(
        status=Ad.Status.ACTIVE, current_price__gt=0,
    )
    qs = exclude_unclear_price(qs)
    if brand:
        qs = qs.filter(brand__slug=brand)
    if model_id:
        qs = qs.filter(model_id=model_id)
    if variant_id:
        qs = qs.filter(variant_id=variant_id)

    # Held before the year filter goes on, because this doubles as the option
    # list for the year picker. Computed after it, a scope narrowed to 1400
    # offers 1400 as the only choice — you could pick a year but never change to
    # a different one without clearing back to "all years" first.
    year_options = list(
        qs.exclude(year_jalali__isnull=True)
        .values("year_jalali").annotate(n=Count("code")).order_by("year_jalali")
    )
    if year_jalali:
        qs = qs.filter(year_jalali=year_jalali)

    scope = {"brand": brand, "model_id": model_id,
             "variant_id": variant_id, "year_jalali": year_jalali,
             "condition": condition, "mileage_bucket": mileage_bucket}
    prices = list(qs.values_list("current_price", flat=True))
    basis = {"mode": "unconditioned", "condition": condition,
             "mileage_bucket": mileage_bucket, "filtered_n": None}

    if condition or mileage_bucket is not None:
        narrowed = qs
        if condition:
            narrowed = narrowed.filter(condition_band_q(condition))
        if mileage_bucket is not None:
            narrowed = narrowed.filter(mileage__gte=mileage_bucket)
            ceiling = next(
                (e for e in pricing.MILEAGE_BUCKETS if e > mileage_bucket), None
            )
            if ceiling is not None:
                narrowed = narrowed.filter(mileage__lt=ceiling)
        narrowed_prices = list(narrowed.values_list("current_price", flat=True))
        basis["filtered_n"] = len(narrowed_prices)

        if len(narrowed_prices) >= MIN_DISTRIBUTION_ADS:
            # `qs` moves with it, so the city facet below describes the same
            # cars as the histogram rather than the wider scope they came from.
            prices, qs, basis["mode"] = narrowed_prices, narrowed, "filtered"
        elif prices:
            # Shift, do not filter. The haircut is measured as a ratio to the
            # cohort median across every cohort thick enough to have an opinion,
            # so applying it to the scope's own prices lands the distribution
            # where this condition trades — and it is signed by construction, so
            # it cannot mark a damaged car up.
            haircuts = pricing.condition_haircuts()
            mile_haircuts = pricing.mileage_haircuts()
            factor = 1.0
            measured = False
            if condition and condition in haircuts:
                factor *= 1.0 - haircuts[condition]
                measured = True
            if mileage_bucket is not None and mileage_bucket in mile_haircuts:
                factor *= 1.0 - mile_haircuts[mileage_bucket]
                measured = True
            if measured and factor > 0:
                prices = [int(p * factor) for p in prices]
                basis["mode"] = "adjusted"
                basis["factor"] = round(factor, 4)
            else:
                # Too thin to filter AND no measured haircut to shift by. Say
                # that rather than returning the unconditioned scope under an
                # "adjusted" label — claiming an adjustment that multiplied by
                # 1.0 is the kind of false precision this codebase spends most
                # of its comments avoiding.
                basis["mode"] = "unconditioned"
                basis["reason"] = "no_measured_adjustment"

    if len(prices) < MIN_DISTRIBUTION_ADS:
        # `years` rides along on the refusal too. The picker's options come from
        # this response, and a year thin enough to refuse is one a reader can
        # pick — answer without it and the control that got them there empties
        # and disables, stranding them on a year with nothing to show and no way
        # back except discarding the model as well.
        return {"available": False, "reason": "insufficient_listings",
                "scope": scope, "n": len(prices), "required": MIN_DISTRIBUTION_ADS,
                "basis": basis, "years": year_options}

    distribution = pricing.peer_distribution(prices)
    low, high = distribution["p10"], distribution["p90"]
    # Square-root rule: bars grow with the sample instead of slicing a thin scope
    # into two dozen near-empty ones.
    n_buckets = max(MIN_HISTOGRAM_BUCKETS,
                    min(HISTOGRAM_BUCKETS, int(math.sqrt(len(prices)))))
    # A band can be narrower than the bar count — a factory-priced trim where
    # every listing asks the same number collapses it to zero. Without this the
    # width floors to 1, the bars run off the top of the band, and the last one
    # gets its top edge pulled back below its own start.
    n_buckets = max(1, min(n_buckets, high - low))
    width = max(1, (high - low) // n_buckets)
    buckets = [
        {"from": low + i * width, "to": low + (i + 1) * width, "n": 0}
        for i in range(n_buckets)
    ]
    # Integer division leaves a remainder above the last bucket's computed edge,
    # and the clamp below folds those rows into it. Say
    # so on the bucket, or its stated range excludes listings it is counting.
    buckets[-1]["to"] = high
    below = above = 0
    for price in prices:
        if price < low:
            below += 1
        elif price > high:
            above += 1
        else:
            # The top edge belongs to the last bucket rather than to a
            # non-existent bucket past the end.
            buckets[min(n_buckets - 1, (price - low) // width)]["n"] += 1

    return {
        "available": True,
        "scope": scope,
        # Whether these are listings or an estimate of listings. Never omitted:
        # a reader comparing an adjusted band against a filtered one is
        # comparing two different kinds of claim.
        "basis": basis,
        "distribution": distribution,
        # No `bucket_size`: every bar states its own from/to, and the last one is
        # wider than the rest wherever the band does not divide evenly.
        "histogram": {"from": low, "to": high,
                      "buckets": buckets, "below": below, "above": above},
        "cities": [
            {"name": row["city__name_fa"], "n": row["n"]}
            for row in qs.exclude(city__isnull=True)
            .values("city__name_fa").annotate(n=Count("code")).order_by("-n")[:12]
        ],
        # Every year this scope has data for — see year_options above for why it
        # deliberately ignores the selected year. Choosing one cannot land on an
        # empty page, and switching between them stays one click.
        "years": [
            {"year_jalali": row["year_jalali"], "n": row["n"]} for row in year_options
        ],
    }


# ---------------------------------------------------------------------------
# What a budget actually buys
# ---------------------------------------------------------------------------
#
# Every other surface in this app starts from a car and ends at a price. This
# one runs the other way, which is how most people actually arrive: they know
# what they can spend and not what it buys. `/api/ads/?price_min=&price_max=`
# was the closest thing available and it answers a different question — it
# returns individual listings, so a reader learns that 47 cars match and nothing
# about which *models* are within reach.

# A cohort needs this many listings inside the budget before it is offered.
# Lower than MIN_DISTRIBUTION_ADS: this is a count and a median, not a
# histogram, and a shortlist that refuses everything is not a shortlist.
MIN_AFFORDABLE_ADS = 4

# Default give-or-take. A hard max would hide a car 2% over, which nobody
# actually means when they say what they can spend.
DEFAULT_TOLERANCE_PCT = 10.0
MAX_TOLERANCE_PCT = 50.0


def affordable(
    budget: int,
    *,
    tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
    brand: str | None = None,
    condition: str | None = None,
    mileage_max: int | None = None,
    limit: int = 40,
) -> dict:
    """Which cars a budget reaches, grouped by cohort rather than by listing.

    The unit is the (model, trim, model year) cohort, because that is the thing
    a reader is choosing between — "a 1398 Peugeot 207 automatic" is a decision,
    "listing ad7f2" is not.

    Ranked by how much of the cohort the budget clears rather than by how cheap
    the cohort is. A budget that buys the best-kept 80% of a model is a better
    suggestion than one that scrapes the bottom 5% of a more expensive one, and
    sorting by price alone puts exactly the wrong cars first.

    ``mileage_max`` and ``condition`` narrow before ranking, because a shortlist
    that ignores them offers cars the reader has already ruled out.
    """
    tolerance_pct = max(0.0, min(float(tolerance_pct), MAX_TOLERANCE_PCT))
    ceiling = int(budget * (1 + tolerance_pct / 100))

    qs = without_cohort_outliers(pricing.scorable_rows()).filter(
        current_price__lte=ceiling,
        model_id__isnull=False,
        year_jalali__isnull=False,
    )
    if brand:
        qs = qs.filter(brand__slug=brand)
    if condition:
        qs = qs.filter(condition_band_q(condition))
    if mileage_max is not None:
        qs = qs.filter(mileage__lte=mileage_max)

    rows = qs.values_list(
        "model_id", "variant_id", "year_jalali", "current_price", "mileage",
        "model__name_fa", "model__brand__name_fa", "model__brand__slug",
        "variant__name_fa",
    )

    grouped: dict[tuple, dict] = defaultdict(
        lambda: {"prices": [], "mileages": [], "within": 0}
    )
    meta: dict[tuple, tuple] = {}
    for (model_id, variant_id, year, price, mileage,
         model_name, brand_name, brand_slug, variant_name) in rows.iterator():
        key = (model_id, variant_id, year)
        entry = grouped[key]
        entry["prices"].append(price)
        if mileage is not None:
            entry["mileages"].append(mileage)
        # Inside the budget proper, as opposed to inside the tolerance. Both are
        # reported: "3 of these are actually at or under what you said" is a
        # different fact from "17 are within 10% of it".
        if price <= budget:
            entry["within"] += 1
        meta.setdefault(key, (model_name, brand_name, brand_slug, variant_name))

    # Cohort sizes for every candidate in one grouped query. Called per cohort
    # this was an N+1 — one scan of the ad table for each of the hundreds of
    # cohorts a broad budget matches — and it would have been the slowest
    # endpoint in the app by an order of magnitude.
    candidates = [k for k, e in grouped.items() if len(e["prices"]) >= MIN_AFFORDABLE_ADS]
    cohort_totals: dict[tuple, int] = {}
    if candidates:
        totals_qs = without_cohort_outliers(pricing.scorable_rows()).filter(
            model_id__in={k[0] for k in candidates},
            year_jalali__in={k[2] for k in candidates},
        )
        for model_id, variant_id, year, n in (
            totals_qs.values_list("model_id", "variant_id", "year_jalali")
            .annotate(n=Count("code")).values_list("model_id", "variant_id",
                                                   "year_jalali", "n")
        ):
            cohort_totals[(model_id, variant_id, year)] = n

    options = []
    for key in candidates:
        entry = grouped[key]
        prices = entry["prices"]
        model_id, variant_id, year = key
        model_name, brand_name, brand_slug, variant_name = meta[key]
        # How much of this cohort the budget reaches, measured against the
        # cohort's *whole* live population rather than the slice already under
        # the ceiling — otherwise every cohort reports 100% by construction.
        total_n = cohort_totals.get(key) or len(prices)
        reach_pct = round(min(100.0, len(prices) / total_n * 100), 1)
        options.append({
            "model_id": model_id,
            "variant_id": variant_id,
            "year_jalali": year,
            "name": model_name,
            "brand_name": brand_name,
            "brand_slug": brand_slug,
            "variant_name": variant_name or "",
            "n": len(prices),
            "within_budget": entry["within"],
            "cohort_size": total_n,
            "reach_pct": reach_pct,
            "median_price": int(statistics.median(prices)),
            "cheapest": min(prices),
            "median_mileage": (
                int(statistics.median(entry["mileages"])) if entry["mileages"] else None
            ),
        })

    if not options:
        return {"available": False, "reason": "nothing_in_range",
                "budget": budget, "tolerance_pct": tolerance_pct,
                "ceiling": ceiling, "required": MIN_AFFORDABLE_ADS}

    # Reach first, then how many are genuinely inside the budget. A cohort the
    # budget clears outright beats one it only just reaches into.
    options.sort(key=lambda r: (r["reach_pct"], r["within_budget"]), reverse=True)
    return {
        "available": True,
        "budget": budget,
        "tolerance_pct": tolerance_pct,
        "ceiling": ceiling,
        "cohorts_matched": len(options),
        "listings_matched": sum(o["n"] for o in options),
        "options": options[:limit],
    }


# ---------------------------------------------------------------------------
# Value retention by model year
# ---------------------------------------------------------------------------

# Per model-year point. Below this a median is a coin flip presented as a fact.
MIN_PER_YEAR = 8
# A model needs this many usable years before a curve is worth drawing.
MIN_YEARS = 3


def depreciation_curve(model_id: int, *, variant_id=None) -> dict:
    """Median asking price by model year, plus retention against the newest year.

    Reports ``pct_of_newest`` rather than a yearly rate: compounding a single
    average rate across a decade is how a plausible curve predicts a negative
    price. Retention is against the newest year with data, not an original sale
    price this system never observes — a statement about the *used* market.
    """
    # Outliers excluded: these are baselines, and an unbelievable price must not
    # help define what a year of age is worth.
    qs = without_cohort_outliers(verified(Ad.objects)).filter(
        status=Ad.Status.ACTIVE, current_price__gt=0,
        model_id=model_id, year_jalali__isnull=False,
    )
    if variant_id:
        qs = qs.filter(variant_id=variant_id)

    by_year: dict[int, list[int]] = defaultdict(list)
    for year, price in qs.values_list("year_jalali", "current_price"):
        by_year[year].append(price)

    points = [
        {"year_jalali": year, "n": len(prices),
         "median_price": int(statistics.median(prices))}
        for year, prices in sorted(by_year.items())
        if len(prices) >= MIN_PER_YEAR
    ]
    if len(points) < MIN_YEARS:
        return {
            # `year_count`, not `years`: price_distribution's refusal carries a
            # `years` list, and one key meaning a list in one refusal and a
            # tally in another is a trap for whoever types the two together.
            "available": False, "reason": "insufficient_years",
            "model_id": model_id, "year_count": len(points), "required": MIN_YEARS,
        }

    newest = points[-1]
    for point in points:
        point["pct_of_newest"] = round(
            point["median_price"] / newest["median_price"] * 100, 1
        )
    span = newest["year_jalali"] - points[0]["year_jalali"]
    oldest_ratio = points[0]["median_price"] / newest["median_price"]
    return {
        "available": True,
        "model_id": model_id,
        "variant_id": variant_id,
        "reference_year": newest["year_jalali"],
        "points": points,
        "retained_over_span_pct": round(oldest_ratio * 100, 1),
        "span_years": span,
        # Annualised only across the observed span, as a summary of the points
        # above rather than a rate to project forward with.
        "avg_annual_decline_pct": (
            round((1 - oldest_ratio ** (1 / span)) * 100, 1) if span > 0 else None
        ),
    }
