"""How long a car takes to leave the feed, and what makes it faster.

The naive answer — average ``ended_at - started_at`` over finished listings — is
wrong in a specific, one-directional way. Cars still listed have not finished
waiting, and they are disproportionately the *slow* ones: a car that leaves in
three days is finished and counted, a car sitting for ninety days is unfinished
and excluded. Averaging only the finished ones therefore understates time-to-sell
by more the slower the market is, which is exactly when the number matters.

Across the live database that naive average is 8.7 days over 22,599 closed
episodes while 38,890 episodes are still open and uncounted.

The standard fix is the Kaplan-Meier estimator, which uses the open listings for
as long as they were observed and stops counting them at the point they were last
seen ("right-censoring"). It is about fifteen lines over numpy, which is already a
dependency, so no new library is needed.

One thing this module never says is "sold". The feed cannot distinguish a car
that sold from one that expired or was withdrawn; every name here is about
*delisting*, which is the only event actually observed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import Q
from django.utils import timezone

from apps.core.models import ListingEpisode
from apps.core.services.quality import verified_by_ad

# Below this a survival curve is a shape drawn through noise. Deliberately higher
# than the deal score's peer minimum: a median with confidence bounds needs more
# evidence than a median alone.
MIN_EPISODES = 20

# Episodes shorter than this are treated as noise rather than as lightning-fast
# sales — a listing that appears and vanishes within a day is far more often a
# posting error or a moderation removal than a car that sold in hours.
MIN_EPISODE_HOURS = 6


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
        """S(day) — the chance a listing is still on the feed after ``day`` days."""
        current = 1.0
        for t, s in zip(self.times, self.survival):
            if t > day:
                break
            current = s
        return current

    def median_days(self) -> float | None:
        """The first day survival drops to 0.5 or below.

        None when the curve never reaches it: with most listings still open, the
        honest answer is "more than the longest we have watched", not a number
        invented by extrapolation.
        """
        for t, s in zip(self.times, self.survival):
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
                for t, s, r in zip(self.times, self.survival, self.at_risk)
            ],
        }


def kaplan_meier(observations: list[Observation]) -> SurvivalCurve:
    """Survival curve with right-censoring.

    At each day where a delisting happened, the fraction that survived it is
    ``1 - delisted/at_risk``; multiplying those together gives the curve. A
    censored listing stays in ``at_risk`` for every day up to when it was last
    seen and then simply leaves — it never counts as a delisting, which is what
    keeps still-listed cars from being read as instant sales.
    """
    curve = SurvivalCurve(n=len(observations))
    if not observations:
        return curve

    curve.delisted = sum(1 for o in observations if o.delisted)
    curve.censored = curve.n - curve.delisted

    event_days = sorted({o.days for o in observations if o.delisted})
    survival = 1.0
    for day in event_days:
        at_risk = sum(1 for o in observations if o.days >= day)
        if at_risk == 0:
            continue
        events = sum(1 for o in observations if o.delisted and o.days == day)
        survival *= 1 - events / at_risk
        curve.times.append(day)
        curve.survival.append(survival)
        curve.at_risk.append(at_risk)
    return curve


def _episode_qs(*, model_id=None, variant_id=None, year_jalali=None):
    qs = verified_by_ad(ListingEpisode.objects.select_related("ad"))
    if model_id:
        qs = qs.filter(ad__model_id=model_id)
    if variant_id:
        qs = qs.filter(ad__variant_id=variant_id)
    if year_jalali:
        qs = qs.filter(ad__year_jalali=year_jalali)
    return qs


def _observations(qs) -> list[Observation]:
    now = timezone.now()
    out = []
    for started_at, ended_at in qs.values_list("started_at", "ended_at"):
        end = ended_at or now
        days = (end - started_at).total_seconds() / 86400
        if days * 24 < MIN_EPISODE_HOURS:
            continue
        out.append(Observation(days=round(days, 2), delisted=ended_at is not None))
    return out


def survival(*, model_id=None, variant_id=None, year_jalali=None) -> dict:
    """Time-to-delist for a cohort, censoring listings that are still live."""
    observations = _observations(
        _episode_qs(model_id=model_id, variant_id=variant_id, year_jalali=year_jalali)
    )
    if len(observations) < MIN_EPISODES:
        return {
            "available": False, "reason": "insufficient_episodes",
            "n": len(observations), "required": MIN_EPISODES,
        }
    curve = kaplan_meier(observations)
    result = curve.as_dict()
    result["available"] = True
    # Survival at fixed horizons as well as the median. The median is a single
    # order statistic and goes degenerate when removal dates cluster — which they
    # do in backfilled history, because mark_inactive_ads closed thousands of
    # episodes at one timestamp, so every cohort's median lands on the same day.
    # Fixed-horizon survival reads correctly through that clustering, and is
    # usually the more useful number anyway ("what are the odds it is still here
    # in a month" beats "the middle listing took N days").
    for horizon in (7, 14, 30, 60, 90):
        result[f"still_listed_at_{horizon}d"] = round(
            curve.probability_still_listed(horizon), 4
        )
    # Reported alongside so the difference is visible rather than asserted: this
    # is the number a naive AVG(days) would have produced.
    finished = [o.days for o in observations if o.delisted]
    result["naive_mean_days_finished_only"] = (
        round(sum(finished) / len(finished), 1) if finished else None
    )
    return result


def hazard_by_price_position(*, model_id: int, year_jalali=None, deciles: int = 5) -> dict:
    """Does asking above the cohort median actually cost you time on the market?

    Listings are bucketed by where their price sits within their own cohort, and
    each bucket gets its own survival curve. This is the one question a seller and
    a buyer both want answered, and it is only answerable because the price and the
    lifecycle are recorded against the same episode.

    Association, not causation: an overpriced car and a slow car may share a cause
    (an unpopular trim, a bad location) rather than one producing the other.
    """
    qs = _episode_qs(model_id=model_id, year_jalali=year_jalali).filter(
        ad__current_price__gt=0
    )
    rows = list(qs.values_list("started_at", "ended_at", "ad__current_price"))
    if len(rows) < MIN_EPISODES * 2:
        return {"available": False, "reason": "insufficient_episodes", "n": len(rows)}

    prices = sorted(r[2] for r in rows)
    median = prices[len(prices) // 2]
    if not median:
        return {"available": False, "reason": "no_price_baseline"}

    now = timezone.now()
    buckets: dict[int, list[Observation]] = {i: [] for i in range(deciles)}
    for started_at, ended_at, price in rows:
        end = ended_at or now
        days = (end - started_at).total_seconds() / 86400
        if days * 24 < MIN_EPISODE_HOURS:
            continue
        ratio = price / median
        # Buckets are relative to the cohort median, so they mean the same thing
        # for a Pride and a Land Cruiser.
        index = min(int(max(ratio - 0.8, 0) / 0.1), deciles - 1)
        buckets[index].append(Observation(days=round(days, 2), delisted=ended_at is not None))

    out = []
    for index, observations in sorted(buckets.items()):
        if len(observations) < MIN_EPISODES:
            continue
        curve = kaplan_meier(observations)
        out.append({
            "price_band": f"{0.8 + index * 0.1:.2f}-{0.9 + index * 0.1:.2f}x median",
            "n": curve.n,
            "delisted": curve.delisted,
            "censored": curve.censored,
            "median_days": curve.median_days(),
            "still_listed_at_30d": round(curve.probability_still_listed(30), 4),
        })
    return {
        "available": bool(out),
        "model_id": model_id,
        "year_jalali": year_jalali,
        "cohort_median_price": median,
        "bands": out,
    }
