"""What kind of number is in the price field.

The deal board assumes ``current_price`` is what one car costs. On Bama it often
is not, and the gap between those two facts was the whole product's largest
error: an audit of the top 200 rows found **148 of them (74%) were installment
(اقساطی) listings whose advertised price is the پیش‌پرداخت**, not the car. One
said so in its own description — «مبلغ فوق، پیش پرداخت است» — while sitting on
the board at 50% off.

Contamination tracked the discount almost perfectly: 3% of rows in the 0–5%
band, 34% at 30–35%, 76% at 45–50%, 100% above 50%. Ranking by discount and
cutting at 50 rows therefore selected *precisely* the artifacts, which is why
the front page was a wall of impossible bargains while ~8,600 genuine 3–20%
deals sat unreachable behind a missing paginator.

Two reasons this cannot key off ``price_type``:

* Bama's own field says ``lumpsum`` for most of them — dealers type the down
  payment into the cash-price box and put the terms in free text. Only 41 of
  those 200 carried ``price_type='installment'`` or a prepayment; the text
  caught 148.
* The same artifact arrives under several vocabularies — اقساط, پیش فروش,
  ثبت نام, عاملیت فروش — so a single keyword is not enough.

``CONDITION_RE`` is separate and is **never** used to exclude. A تصادفی car is
genuinely cheap and genuinely for sale; the cohort key
``(model, variant, year_jalali)`` simply has no condition dimension, so the
model cannot tell "bargain" from "damaged". That is the honest ceiling of the
estimate, so the answer is to label the row, not to hide it.
"""

from __future__ import annotations

import re

# Money that is not the price of the car: financing, deposits, pre-sales and
# factory allocations. Written to run unchanged in both Python's ``re`` and
# Postgres' POSIX engine so the queryset filter and the per-row badge can never
# disagree about the same ad.
#
# ``.?`` absorbs the optional space or ZWNJ Persian compounds are written with
# either way ("پیش فروش" / "پیش‌فروش").
FINANCE = (
    r"اقساط|قسط|لیزینگ|حواله|عاملیت"
    r"|پیش.?پرداخت|پیش.?فروش|ثبت.?نام|مرحله.?ای|چک.?ضمانت"
)

# Reasons a car is honestly cheap. Badge material, never a filter.
#
# Free-zone plates (منطقه آزاد) belong here rather than in FINANCE: the price is
# a real cash price for a real car, it is simply a car that cannot be driven on
# the mainland without a permit. Same shape of problem as accident damage — the
# cohort key does not know, so the reader has to be told.
CONDITION = (
    r"تصادفی|دوررنگ|دور.?رنگ|صافکاری|اوراقی|مونتاژ|بدون.?سند|در.?رهن"
    r"|منطقه.?آزاد|مناطق.?آزاد|پلاک.?منطقه|پلاک.?اروند"
)

FINANCE_RE = re.compile(FINANCE)
CONDITION_RE = re.compile(CONDITION)


def price_basis_unclear(
    *,
    title: str = "",
    description: str = "",
    price_type: str = "",
    prepayment: int | None = None,
) -> bool:
    """True when ``current_price`` is a deposit/instalment rather than the car.

    Keyword-only because the four arguments are all strings-or-numbers about the
    same ad and a positional slip would silently pass the description as a title.
    """
    if price_type == "installment" or (prepayment or 0) > 0:
        return True
    return bool(FINANCE_RE.search(f"{title or ''}\n{description or ''}"))


def condition_discounted(*, title: str = "", description: str = "") -> bool:
    """True when the listing itself explains why it is under its cohort."""
    return bool(CONDITION_RE.search(f"{title or ''}\n{description or ''}"))


def exclude_unclear_price(qs, prefix: str = ""):
    """Drop ads whose price is not one car's cash price.

    ``prefix`` is the relation path to the ``Ad`` — empty when filtering ``Ad``
    itself, ``"ad__"`` when filtering something that points at one (the notifier
    filters ``DealScoreCache``). One function rather than two so the predicate
    cannot drift between the board and the alerts that quote it.

    The regex runs in Postgres rather than in Python so callers that only need a
    count or a slice never load the descriptions.

    ponytail: unindexed regex seq-scan over ~27k active priced ads (~0.3s on the
    deal-board rebuild, which runs on a schedule, not per request). If this ever
    lands on a hot read path, persist it as a boolean column written at ingest
    using ``price_basis_unclear`` above — same predicate, no second definition.
    """
    from django.db.models import Q

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
    # A clean private listing must survive: this is the false-positive side, and
    # it is the one that costs the product real deals.
    assert not price_basis_unclear(description="بسیار تمیز، فنی سالم، فوری فروشی")
    assert not price_basis_unclear(title="پژو، 207", description="")

    assert condition_discounted(description="مدل ۹۷ تصادفی که قبل تصادف رنگی نداشت")
    assert condition_discounted(description="احتیاج به صافکاری دارد")
    assert condition_discounted(description="دوررنگ به دلیل زیبایی")
    assert not condition_discounted(description="بسیار تمیز، فنی سالم")
    # Damage is a reason to look harder, never a reason to be hidden.
    assert not price_basis_unclear(description="لوکانو L7 تصادفی")
    print("ok")
