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


def cohort_series(
    scope: str,
    scope_id: str | None = None,
    *,
    variant_id: int | None = None,
    year_jalali: int | None = None,
) -> dict:
    """``{date: {cohort_key: (median_price, ad_count)}}`` for one scope.

    ``variant_id`` / ``year_jalali`` narrow below the three persisted scopes.
    Nothing builds an index at that granularity on a schedule — a trim-level
    series for every trim in the catalogue would multiply what the warm tick
    writes every thirty minutes, for a question most sessions never ask — so
    those two are answered on demand instead. The snapshot rows are already
    keyed on both, so this is a filter, not a new aggregation.
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

    by_date: dict = defaultdict(dict)
    for d, model_id, variant_id, year, median_price, ad_count in qs.values_list(
        "date", "model_id", "variant_id", "year_jalali", "median_price", "ad_count"
    ):
        by_date[d][(model_id, variant_id, year)] = (median_price, ad_count)
    return by_date


def compute_index(by_date: dict) -> list[dict]:
    """Chain per-date cohort medians into a series, oldest first.

    Gaps are bridged: consecutive *available* dates are chained, so a day the
    worker did not run costs resolution, never a break in the series.
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
            # The base date: an index needs somewhere to start, and the first
            # observation cannot have a return by definition.
            series.append({
                "date": d, "index_value": index_value, "return_pct": None,
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
        })
        previous = current

    return series


def build_index(scope: str, scope_id: str | None = None) -> int:
    """Recompute and persist one scope's whole series. Returns rows written.

    Full rebuild, not incremental append: the series is chained, so a corrected
    snapshot changes every value after it.
    """
    series = compute_index(cohort_series(scope, scope_id))
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


def _scope_names(scope: str, ids: list[str]) -> dict[str, dict]:
    """Human labels for a set of scope ids, resolved in one query per scope."""
    if scope == MarketIndex.Scope.BRAND:
        rows = Brand.objects.filter(slug__in=ids).values_list("slug", "name_fa")
        return {str(slug): {"name": name, "brand_name": None} for slug, name in rows}
    if scope == MarketIndex.Scope.MODEL:
        rows = (
            Model.objects.filter(pk__in=[i for i in ids if i.isdigit()])
            .values_list("pk", "name_fa", "brand__name_fa")
        )
        return {str(pk): {"name": name, "brand_name": brand} for pk, name, brand in rows}
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
        .values_list("scope_id", "date", "index_value", "ad_count", "cohort_count")
    )

    by_scope: dict[str, list] = defaultdict(list)
    for scope_id, day, value, ads, cohorts in rows:
        by_scope[str(scope_id)].append((day, value, ads, cohorts))

    ranked = []
    for scope_id, points in by_scope.items():
        if len(points) < MOVER_MIN_DAYS:
            continue
        first, last = points[0], points[-1]
        if not first[1]:
            continue
        if last[3] < MOVER_MIN_COHORTS:
            continue
        ranked.append({
            "scope_id": scope_id,
            "change_pct": round((last[1] / first[1] - 1) * 100, 2),
            "latest_index": round(last[1], 2),
            "days": len(points),
            "first_date": first[0].isoformat(),
            "last_date": last[0].isoformat(),
            "ad_count": last[2],
            "cohort_count": last[3],
            # Enough to draw a sparkline beside the row, without a second call
            # per row. Rounded here so the payload is not full of float noise.
            "series": [round(v, 2) for _, v, _, _ in points],
        })

    names = _scope_names(scope, [r["scope_id"] for r in ranked])
    for row in ranked:
        row.update(names.get(row["scope_id"], {"name": row["scope_id"], "brand_name": None}))

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
        return {
            "n": self.n,
            "delisted": self.delisted,
            "censored": self.censored,
            "median_days": self.median_days(),
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
    """
    curve = SurvivalCurve(n=len(observations))
    if not observations:
        return curve

    curve.delisted = sum(1 for o in observations if o.delisted)
    curve.censored = curve.n - curve.delisted

    survival = 1.0
    for day in sorted({o.days for o in observations if o.delisted}):
        at_risk = sum(1 for o in observations if o.days >= day)
        if at_risk == 0:
            continue
        events = sum(1 for o in observations if o.delisted and o.days == day)
        survival *= 1 - events / at_risk
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
    cutoff = now - timedelta(days=days)
    # A listing cannot have completed a window that started before the earliest
    # date whose endings we trust. Said plainly rather than reported as "too few
    # listings", because the two have different answers: this one fixes itself
    # as clean history accrues, and a shorter window works today.
    clean_days = (now - clean_start()).days
    if clean_days < days:
        return {"available": False, "reason": "window_exceeds_clean_history",
                "window_days": days, "clean_days": max(0, clean_days),
                "clean_start": clean_start().date().isoformat()}

    rows = _episode_qs().filter(started_at__lte=cutoff).values_list(
        "ad__model_id", "ad__model__name_fa", "ad__model__brand__name_fa",
        "started_at", "ended_at",
    )

    tally: dict = defaultdict(lambda: {"n": 0, "left": 0})
    meta: dict = {}
    for model_id, model_name, brand_name, started_at, ended_at in rows:
        if model_id is None:
            continue
        entry = tally[model_id]
        entry["n"] += 1
        if ended_at is not None and (ended_at - started_at) <= timedelta(days=days):
            entry["left"] += 1
        meta.setdefault(model_id, (model_name, brand_name))

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
             "variant_id": variant_id, "year_jalali": year_jalali}
    prices = list(qs.values_list("current_price", flat=True))
    if len(prices) < MIN_DISTRIBUTION_ADS:
        # `years` rides along on the refusal too. The picker's options come from
        # this response, and a year thin enough to refuse is one a reader can
        # pick — answer without it and the control that got them there empties
        # and disables, stranding them on a year with nothing to show and no way
        # back except discarding the model as well.
        return {"available": False, "reason": "insufficient_listings",
                "scope": scope, "n": len(prices), "required": MIN_DISTRIBUTION_ADS,
                "years": year_options}

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
