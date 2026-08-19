"""Listings whose price is not the price of one car.

Unit tests for the predicate (pure string work, many cases, no DB) and one
integration test for the exclusion (the deal board is a queryset against stored
rows, so the only honest check builds a real cohort and rebuilds the cache).

This is the regression suite for the audit's headline finding: 148 of the top
200 rows on the deal board were installment listings advertising a پیش‌پرداخت,
so the front page ranked artifacts as the best deals in the market. Contamination
rose monotonically with the discount — 3% of rows below 5%, 76% at 45–50%, 100%
above 50% — which is exactly the shape a "sort by discount, take 50" board turns
into a page of nonsense.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from apps.core.models import Ad, Brand, City, DealScoreCache, Model, Variant
from apps.core.services.deal_score import compute_deal_scores
from apps.core.services.listing_kind import (
    condition_discounted,
    price_basis_unclear,
)

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


# --- the predicate -----------------------------------------------------------

@pytest.mark.parametrize("text", [
    "مبلغ فوق، پیش پرداخت است",
    "فروش خودرو به صورت نقد و اقساط با سود بانک مرکزی",
    "ثبت نام محدود خودرو ۲۱۲، پرداخت طی چند مرحله",
    "پیش‌فروش با تحویل ۳ ماهه",
    "پرداخت ۳ مرحله ای برای اطلاعات بیشتر تماس بگیرید",
    "عاملیت فروش نمایندگی ها",
    "فروش با لیزینگ بدون ضامن",
])
def test_finance_vocabularies_are_all_caught(text):
    """Each of these appeared verbatim on a row the old board ranked top-15."""
    assert price_basis_unclear(description=text)


def test_the_structured_field_is_honoured_when_bama_sets_it():
    assert price_basis_unclear(price_type="installment")
    assert price_basis_unclear(prepayment=500_000_000)


def test_bama_labels_most_of_them_lumpsum():
    """Why the text rule exists at all.

    Dealers type the down payment into the cash-price box, so the structured
    field alone caught 41 of 200 contaminated rows where the text caught 148.
    """
    assert price_basis_unclear(
        price_type="lumpsum", description="پیش پرداخت ۵۰٪، اقساط ۳۶ ماهه"
    )


@pytest.mark.parametrize("text", [
    "بسیار تمیز، تمام رنگ‌ها سالم، کولر و فنی سلامت، فوری فروشی",
    "کیربوکس جدید، بیمه ۶ ماه، فنی درجه یک",
    "تودوزی نو، صندلی نو",
])
def test_clean_private_listings_survive(text):
    """The false-positive side, and the one that costs the product real deals.

    Measured cost of this rule on the healthy part of the board was 4% of the
    0–5% discount band; a rule that ate ordinary listings would be worse than
    the bug it fixes.
    """
    assert not price_basis_unclear(description=text)


@pytest.mark.parametrize("text", [
    "مدل ۹۷ تصادفی که قبل تصادف هیچ رنگی نداشته",
    "احتیاج به صافکاری دارد",
    "دوررنگ به دلیل زیبایی",
    "تمامی مناطق آزاد، تحویل فوری",
])
def test_condition_is_flagged_but_never_excluded(text):
    """A تصادفی car is really for sale at really that price.

    The cohort key is (model, variant, year) and has no condition dimension, so
    the gap is real and unexplained — the reader needs telling, not protecting.
    """
    assert condition_discounted(description=text)
    assert not price_basis_unclear(description=text)


# --- the exclusion ------------------------------------------------------------

@pytest.fixture
def cohort(db):
    brand = Brand.objects.create(slug="peugeot", name_fa="پژو", is_confirmed=True)
    model = Model.objects.create(brand=brand, name_fa="207", is_confirmed=True)
    variant = Variant.objects.create(model=model, name_fa="پانوراما")
    city = City.objects.create(name_fa="تهران")

    def make(code, price, **kw):
        return Ad.objects.create(
            code=code, brand=brand, model=model, variant=variant, city=city,
            year_jalali=1402, mileage=50_000, current_price=price,
            status=Ad.Status.ACTIVE, title=kw.pop("title", "پژو، 207"),
            first_seen_at=NOW - timedelta(days=5), last_seen_at=NOW, publish_at=NOW,
            **kw,
        )

    # Ten peers at 2B so the cohort clears MIN_PEERS and the median is 2B.
    for i in range(10):
        make(f"peer{i:04d}", 2_000_000_000)
    return make


@pytest.mark.django_db
def test_an_installment_ad_never_reaches_the_board(cohort):
    """1.05B against a 2B median is a 47% "discount" and pure artifact."""
    cohort("instal01", 1_050_000_000,
           description="نقد و اقساط، پیش پرداخت ۵۰ درصد")

    compute_deal_scores()

    assert not DealScoreCache.objects.filter(ad_id="instal01").exists()


@pytest.mark.django_db
def test_a_genuinely_cheap_car_still_reaches_the_board(cohort):
    """The other half of the property: the filter must not empty the board."""
    cohort("cheap001", 1_700_000_000, description="بسیار تمیز، فنی سالم")

    compute_deal_scores()

    row = DealScoreCache.objects.get(ad_id="cheap001")
    assert row.discount_pct == pytest.approx(15.0, abs=0.1)


@pytest.mark.django_db
def test_a_damaged_car_stays_on_the_board(cohort):
    """Condition explains the gap; it does not disqualify the listing."""
    cohort("crash001", 1_500_000_000, description="تصادفی، شاسی سالم")

    compute_deal_scores()

    assert DealScoreCache.objects.filter(ad_id="crash001").exists()


@pytest.mark.django_db
def test_havaleh_is_still_excluded_after_the_special_case_was_removed(cohort):
    """The old rule was `title__startswith("حواله")`. The regex subsumes it."""
    cohort("havale01", 1_100_000_000, title="حواله پژو، 207")

    compute_deal_scores()

    assert not DealScoreCache.objects.filter(ad_id="havale01").exists()
