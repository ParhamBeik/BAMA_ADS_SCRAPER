"""Which rows are trustworthy, and which prices are not one car's cash price.

Two independent gates, deliberately not merged:

* ``verified`` / ``without_*_outliers`` — queryset filters over the flags
  ingestion and the cohort pass wrote. Every analytical read funnels through
  them so no consumer can forget the filter.
* ``price_basis_unclear`` / ``exclude_unclear_price`` — a text+field verdict on
  whether ``current_price`` is the car at all. See below; this was the whole
  product's largest error.
"""

from __future__ import annotations

import re

from django.db.models import Q

from apps.jobs.verify import HARD_RULE_IDS

# ---------------------------------------------------------------------------
# Row-level trust
# ---------------------------------------------------------------------------

# Cohort-pass verdicts, kept apart from quality_flags: quality_flags is
# recomputed from the payload on every observation, so an out-of-band verdict
# written there would be erased within one fetch tick.
FLAG_OUTLIER_HIGH = "price_outlier_high"
FLAG_OUTLIER_LOW = "price_outlier_low"
COHORT_FLAGS = frozenset({FLAG_OUTLIER_HIGH, FLAG_OUTLIER_LOW})


def verified(qs):
    """Ads that failed no *hard* verification rule.

    Soft flags stay in the data and only serve monitoring, so one unparseable
    publish phrase never removes an otherwise perfect price from the statistics.
    Uses jsonb containment, which the ``ad_quality_gin`` index serves.
    """
    disqualifying = Q()
    for rule_id in sorted(HARD_RULE_IDS):
        disqualifying |= Q(quality_flags__contains=[rule_id])
    return qs.exclude(disqualifying)


def without_cohort_outliers(qs):
    """Drop both cohort outlier sides. Only for computing a *baseline*.

    An outlier must not help define the number that judges it, but it is still a
    real listing — anything shown to a user should keep it and surface the flag.
    """
    disqualifying = Q()
    for flag in sorted(COHORT_FLAGS):
        disqualifying |= Q(cohort_flags__contains=[flag])
    return qs.exclude(disqualifying)


def without_high_outliers(qs):
    """The browse-list filter: drop prices far *above* peers, and only those.

    An absurd asking price is noise (a 206 was listed at 5.8 trillion toman); an
    absurdly cheap one is the underpriced car this product exists to find.
    """
    return qs.exclude(cohort_flags__contains=[FLAG_OUTLIER_HIGH])


def verified_by_ad(qs, field: str = "ad"):
    """Same gate for a queryset that *references* Ad (prices, scores, episodes)."""
    from apps.core.models import Ad  # local: avoids a models <-> services cycle

    return qs.filter(**{f"{field}__in": verified(Ad.objects)})


# ---------------------------------------------------------------------------
# What kind of number is in the price field
# ---------------------------------------------------------------------------
#
# The deal board assumes `current_price` is what one car costs. On Bama it often
# is not: an audit of the top 200 rows found 148 of them (74%) were installment
# (اقساطی) listings whose advertised price is the پیش‌پرداخت. Contamination
# tracked the discount almost perfectly — 3% of rows at 0-5% off, 76% at 45-50%,
# 100% above 50% — so ranking by discount selected *precisely* the artifacts.
#
# This cannot key off `price_type`: Bama's own field says "lumpsum" for most of
# them (dealers type the down payment into the cash box and the terms in free
# text), and the artifact arrives under several vocabularies.

# Written to run unchanged in both Python's `re` and Postgres' POSIX engine, so
# the queryset filter and the per-row badge can never disagree about one ad.
# `.?` absorbs the optional space or ZWNJ ("پیش فروش" / "پیش‌فروش").
FINANCE = (
    r"اقساط|قسط|لیزینگ|حواله|عاملیت"
    r"|پیش.?پرداخت|پیش.?فروش|ثبت.?نام|مرحله.?ای|چک.?ضمانت"
)

# Reasons a car is honestly cheap. Badge material, NEVER a filter: a تصادفی car
# is really for sale at really that price. The cohort key has no condition
# dimension, so the honest answer is to label the row, not to hide it.
# Free-zone plates belong here for the same reason — a real cash price for a car
# that simply cannot be driven on the mainland without a permit.
CONDITION = (
    r"تصادفی|دوررنگ|دور.?رنگ|صافکاری|اوراقی|مونتاژ|بدون.?سند|در.?رهن"
    r"|منطقه.?آزاد|مناطق.?آزاد|پلاک.?منطقه|پلاک.?اروند"
)

_FINANCE_RE = re.compile(FINANCE)
_CONDITION_RE = re.compile(CONDITION)


def price_basis_unclear(
    *,
    title: str = "",
    description: str = "",
    price_type: str = "",
    prepayment: int | None = None,
) -> bool:
    """True when ``current_price`` is a deposit/instalment rather than the car.

    Keyword-only: four strings-or-numbers about the same ad, where a positional
    slip would silently pass the description as a title.
    """
    if price_type == "installment" or (prepayment or 0) > 0:
        return True
    return bool(_FINANCE_RE.search(f"{title or ''}\n{description or ''}"))


def condition_discounted(*, title: str = "", description: str = "") -> bool:
    """True when the listing itself explains why it is under its cohort."""
    return bool(_CONDITION_RE.search(f"{title or ''}\n{description or ''}"))


def exclude_unclear_price(qs, prefix: str = ""):
    """Drop ads whose price is not one car's cash price.

    ``prefix`` is the relation path to the Ad ("" for Ad itself, "ad__" for the
    notifier's DealScoreCache). One function, so the predicate cannot drift
    between the board and the alerts that quote it. The regex runs in Postgres,
    so callers that only need a count never load the descriptions.

    ponytail: unindexed regex seq-scan over ~27k active priced ads (~0.3s on the
    scheduled deal-board rebuild). If this ever lands on a hot read path,
    persist it as a boolean written at ingest with ``price_basis_unclear``.
    """
    return qs.exclude(
        Q(**{f"{prefix}price_type": "installment"})
        | Q(**{f"{prefix}current_prepayment__gt": 0})
        | Q(**{f"{prefix}title__regex": FINANCE})
        | Q(**{f"{prefix}description__regex": FINANCE})
    )


if __name__ == "__main__":
    # Real strings from the audited rows, so a regex edit that stops catching
    # them fails here rather than on the front page.
    assert price_basis_unclear(description="مبلغ فوق، پیش پرداخت است")
    assert price_basis_unclear(description="فروش خودرو به صورت نقد و اقساط")
    assert price_basis_unclear(title="پیش فروش ام وی ام، X55 PRO")
    assert price_basis_unclear(description="ثبت نام محدود خودرو ۲۱۲")
    assert price_basis_unclear(description="پرداخت ۳ مرحله ای", price_type="lumpsum")
    assert price_basis_unclear(description="", price_type="installment")
    assert price_basis_unclear(description="", prepayment=500_000_000)
    # The false-positive side, which is the one that costs the product real deals.
    assert not price_basis_unclear(description="بسیار تمیز، فنی سالم، فوری فروشی")
    assert not price_basis_unclear(title="پژو، 207", description="")

    assert condition_discounted(description="مدل ۹۷ تصادفی که قبل تصادف رنگی نداشت")
    assert condition_discounted(description="احتیاج به صافکاری دارد")
    assert condition_discounted(description="دوررنگ به دلیل زیبایی")
    assert not condition_discounted(description="بسیار تمیز، فنی سالم")
    # Damage is a reason to look harder, never a reason to be hidden.
    assert not price_basis_unclear(description="لوکانو L7 تصادفی")
    print("ok")
