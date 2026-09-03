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

from apps.core.rules import HARD_RULE_IDS  # noqa: F401 - public compatibility export

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
    """Ads that failed no *hard* verification rule, and that a human has not
    excluded.

    Soft flags stay in the data and only serve monitoring, so one unparseable
    publish phrase never removes an otherwise perfect price from the statistics.

    Reads the ``is_verified`` generated column, not the flags themselves. This
    used to be five NOT-containment predicates over ``quality_flags``, which no
    index can serve — GIN accelerates a positive ``@>`` and this asks the
    opposite — so every analytical read sequential-scanned the table. Postgres
    computes the column, which means none of the three write paths can forget to
    maintain it.

    ``excluded_from_analytics`` is the manual override, set only from the review
    queue. It belongs here rather than at each call site for the same reason
    everything else in this module does: this function is the single definition
    of "an ad the numbers may be built from", and a rule enforced in four places
    is a rule enforced in three.
    """
    return qs.filter(is_verified=True)


def without_cohort_outliers(qs):
    """Drop both cohort outlier sides. Only for computing a *baseline*.

    An outlier must not help define the number that judges it, but it is still a
    real listing — anything shown to a user should keep it and surface the flag.
    """
    return qs.filter(has_high_outlier=False, has_low_outlier=False)


def without_high_outliers(qs):
    """The browse-list filter: drop prices far *above* peers, and only those.

    An absurd asking price is noise (a 206 was listed at 5.8 trillion toman); an
    absurdly cheap one is the underpriced car this product exists to find.
    """
    return qs.filter(has_high_outlier=False)


def verified_by_ad(qs, field: str = "ad"):
    """Same gate for a queryset that *references* Ad (prices, scores, episodes)."""
    return qs.filter(**{f"{field}__is_verified": True})


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

# Reasons a car is honestly cheap, read off the seller's prose. Badge material,
# NEVER a filter: a تصادفی car is really for sale at really that price, so the
# honest answer is to label the row, not to hide it. Free-zone plates belong
# here for the same reason — a real cash price for a car that simply cannot be
# driven on the mainland without a permit.
#
# This regex is the *weak* signal and always was: measured on production it
# fires on 2,133 of the 7,907 active ads whose structured `body_status` already
# declares damage, because sellers fill in Bama's own field instead of repeating
# themselves in the description. `condition_band` below is the strong one.
CONDITION = (
    r"تصادفی|دوررنگ|دور.?رنگ|صافکاری|اوراقی|مونتاژ|بدون.?سند|در.?رهن"
    r"|منطقه.?آزاد|مناطق.?آزاد|پلاک.?منطقه|پلاک.?اروند"
)

_FINANCE_RE = re.compile(FINANCE)
_CONDITION_RE = re.compile(CONDITION)


# ---------------------------------------------------------------------------
# Body condition — the strongest price signal in the corpus
# ---------------------------------------------------------------------------
#
# `Ad.body_status` carries Bama's own paint/repair verdict on 100% of ads, in 19
# values that form a clean severity ladder. Measured against cohort medians on
# production, the market prices that ladder monotonically: a fully-repainted car
# trades ~16.5% under its cohort, دور رنگ ~9%, a single spot ~1.4%. Scoring that
# was blind to it reported those as bargains — 69% of the served board was a
# damage-declared car against 26% of the catalogue.
#
# Four bands, not 19: the raw values split cohorts far too thin to clear
# MIN_PEERS. Derived from keywords rather than an exact list of the 19 known
# strings so a value Bama adds later ("سه لکه رنگ") lands in the right band
# instead of silently falling through to "unknown".
#
# Order matters and is load-bearing:
#   * structural first — "کاپوت تعویض" must not be read as paint;
#   * cosmetic before clean — "صافکاری بدون رنگ" contains "بدون رنگ";
#   * clean before painted — "بدون رنگ" contains "رنگ".
CLEAN, COSMETIC, PAINTED, STRUCTURAL = "clean", "cosmetic", "painted", "structural"

# Severity order, for anything that needs to compare two bands.
CONDITION_BANDS = (CLEAN, COSMETIC, PAINTED, STRUCTURAL)

_BAND_RULES = (
    # تعویض (panel replaced), تصادفی (accident), سوخته, اوراقی, اتاق (chassis swap)
    (STRUCTURAL, re.compile(r"تعویض|تصادفی|سوخته|اوراقی|اتاق")),
    # Multi-spot and full panel paint: چند لکه, دو لکه, دور رنگ, کامل رنگ, درب/گلگیر/کاپوت رنگ
    (
        PAINTED,
        re.compile(
            r"چند.?لکه|دو.?لکه|سه.?لکه|چهار.?لکه|دور.?رنگ|کامل.?رنگ|تمام.?رنگ"
            r"|درب.?رنگ|گلگیر.?رنگ|کاپوت.?رنگ|سقف.?رنگ"
        ),
    ),
    # Minor cosmetic blemishes: یک لکه رنگ, صافکاری, خط و خش, لیسه, قلم گیری
    (COSMETIC, re.compile(r"یک.?لکه|لکه|صافکاری|خط.?و.?خش|لیسه|قلم.?گیری")),
    # Clean: بدون رنگ
    (CLEAN, re.compile(r"بدون.?رنگ")),
    # Catch-all paint
    (PAINTED, re.compile(r"رنگ")),
)


def condition_band(body_status: str | None) -> str | None:
    """Bama's `body_status` collapsed to one of four severity bands.

    ``None`` for an empty or unrecognised value — which must mean "no
    adjustment", never "clean". Guessing clean on an unknown string would
    reintroduce exactly the bug this exists to fix.
    """
    text = (body_status or "").strip()
    if not text:
        return None
    for band, pattern in _BAND_RULES:
        if pattern.search(text):
            return band
    return None


def condition_band_q(band: str, *, prefix: str = "") -> Q:
    """``condition_band`` as a queryset predicate, derived from the same rules.

    ``_BAND_RULES`` is ordered and first-match-wins, and that ordering is
    load-bearing — "صافکاری بدون رنگ" contains "بدون رنگ", "بدون رنگ" contains
    "رنگ", and PAINTED appears twice because the last rule is a catch-all that
    must only fire once the specific ones have not. Reproducing that as a
    hand-written include/exclude pair per band is how the two definitions drift,
    so this builds the predicate *from the rules themselves*: for each position
    the band occupies, match that rule and exclude every rule before it.

    ``apps/core/filters.py`` used to carry its own copy of these regexes. It now
    calls this, so a rule added to ``_BAND_RULES`` reaches the filter, the
    distribution and the scorer at once.
    """
    field = f"{prefix}body_status"
    combined = Q()
    matched = False
    for i, (rule_band, pattern) in enumerate(_BAND_RULES):
        if rule_band != band:
            continue
        matched = True
        clause = Q(**{f"{field}__regex": pattern.pattern})
        for _, earlier in _BAND_RULES[:i]:
            clause &= ~Q(**{f"{field}__regex": earlier.pattern})
        combined |= clause
    if not matched:
        # An unknown band must select nothing, never everything: an empty Q()
        # passed to filter() is a no-op and would return the whole scope under a
        # label saying it had been narrowed.
        return Q(pk__in=[])
    return combined


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


def condition_discounted(*, title: str = "", description: str = "",
                         body_status: str = "") -> bool:
    """True when the listing itself explains why it is under its cohort.

    Reads Bama's structured ``body_status`` first (populated on every ad);
    the description regex is the fallback for free-zone plates and prose
    that never made it into the column.
    """
    band = condition_band(body_status)
    if band in (COSMETIC, PAINTED, STRUCTURAL):
        return True
    return bool(_CONDITION_RE.search(f"{title or ''}\n{description or ''}"))


def exclude_unclear_price(qs, prefix: str = ""):
    """Drop ads whose price is not one car's cash price.

    ``prefix`` is the relation path to the Ad ("" for Ad itself, "ad__" for the
    notifier's DealScoreCache). One function, so the predicate cannot drift
    between the board and the alerts that quote it.

    Reads the indexed column ``Ad.price_basis_unclear`` rather than re-running
    ``FINANCE`` over every description. That regex has no index and scanned all
    ~27k active priced ads per call — tolerable once per deal-board rebuild, and
    the reason the browse endpoints never applied this filter at all, which is
    how «حواله» rows ended up at the top of "cheapest first" while the analytics
    on the next screen counted a different population. ``Ad.save`` derives the
    column from ``price_basis_unclear``, so the verdict is still defined in
    exactly one place.
    """
    return qs.exclude(**{f"{prefix}price_basis_unclear": True})


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
    # The structured column is the strong signal — a clean description with a
    # painted body_status must still badge.
    assert condition_discounted(body_status="کامل رنگ")
    assert condition_discounted(body_status="کاپوت تعویض")
    assert not condition_discounted(body_status="بدون رنگ")
    # Damage is a reason to look harder, never a reason to be hidden.
    assert not price_basis_unclear(description="لوکانو L7 تصادفی")

    # All 19 `body_status` values present in production, with their counts, so a
    # rule reordering that silently reclassifies 55k clean cars fails here.
    for value, expected in (
        ("بدون رنگ", CLEAN),                 # 55,535
        ("یک لکه رنگ", COSMETIC),            #  3,482
        ("چند لکه رنگ", COSMETIC),           #  3,055
        ("دو لکه رنگ", COSMETIC),            #  2,770
        ("صافکاری بدون رنگ", COSMETIC),      #  1,711 — contains "بدون رنگ"
        ("خط و خش جزئی", COSMETIC),          #  1,594
        ("دور رنگ", PAINTED),                #  1,703
        ("گلگیر رنگ", PAINTED),              #    767
        ("کامل رنگ", PAINTED),               #    530
        ("یک درب رنگ", PAINTED),             #    327
        ("کاپوت رنگ", PAINTED),              #    302
        ("دو درب رنگ", PAINTED),             #    198
        ("کاپوت تعویض", STRUCTURAL),         #    622 — paint word absent
        ("گلگیر تعویض", STRUCTURAL),         #    592
        ("درب تعویض", STRUCTURAL),           #    355
        ("اتاق تعویض", STRUCTURAL),          #     90
        ("تصادفی", STRUCTURAL),              #    108
        ("سوخته", STRUCTURAL),               #     16
        ("اوراقی", STRUCTURAL),              #     10
    ):
        assert condition_band(value) == expected, f"{value} -> {condition_band(value)}"

    # A value Bama has not shipped yet still lands in the right band.
    assert condition_band("سه لکه رنگ") == COSMETIC
    assert condition_band("سپر تعویض") == STRUCTURAL
    # Unknown must be "no opinion", never "clean".
    assert condition_band("") is None
    assert condition_band(None) is None
    assert condition_band("نامشخص") is None
    print("ok")
