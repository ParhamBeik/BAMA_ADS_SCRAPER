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

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime, time
from datetime import timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

from apps.core.models import Ad, DailyInventorySnapshot, ListingEpisode, MarketIndex
from apps.core.quality import verified, verified_by_ad, without_cohort_outliers

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


def cohort_series(scope: str, scope_id: str | None = None) -> dict:
    """``{date: {cohort_key: (median_price, ad_count)}}`` for one scope."""
    qs = DailyInventorySnapshot.objects.filter(
        median_price__isnull=False, model_id__isnull=False
    )
    if scope == MarketIndex.Scope.BRAND:
        qs = qs.filter(model__brand__slug=scope_id)
    elif scope == MarketIndex.Scope.MODEL:
        qs = qs.filter(model_id=scope_id)

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
            "available": False, "reason": "insufficient_years",
            "model_id": model_id, "years": len(points), "required": MIN_YEARS,
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
