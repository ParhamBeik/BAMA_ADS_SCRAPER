"""Unit and integration tests for Persian search tokenization, body condition classification, and low-mileage pricing adjustments.

Testing rationale:
Unit tests verify pure-logic text normalization and condition band classification at the base of the pyramid, while integration tests verify HTTP query filtering and pricing calculations across component boundaries.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.utils import timezone

from apps.core.filters import AdFilter
from apps.core.models import Ad, Brand, Model
from apps.core.normalization import (
    normalize_text,
    search_document,
    search_tokens,
    to_persian_digits,
)
from apps.core.pricing import MILEAGE_BUCKETS, Baseline, _monotone
from apps.core.quality import CLEAN, COSMETIC, PAINTED, STRUCTURAL, condition_band, verified
from apps.core.rules import HARD_RULE_IDS


def test_normalization_digits_and_characters():
    """Unit: Verifies Persian/Arabic digits, characters, and punctuation normalization."""
    assert normalize_text("پژو، ۲۰۶") == "پژو 206"
    assert normalize_text("تندر-۹۰") == "تندر 90"
    assert normalize_text("دنا‌ پلاس") == "دنا پلاس"
    assert normalize_text("كویيك") == "کوییک"
    assert search_tokens("پژو، ۲۰۶ تیپ ۵") == ["پژو", "206", "تیپ", "5"]
    assert to_persian_digits("206") == "۲۰۶"
    assert search_document("كيا", "سورنتو", "توضیح") == "کیا سورنتو توضیح"


def test_condition_band_classification_monotonicity():
    """Unit: Verifies condition classification maps multi-spot paint to PAINTED and single spot to COSMETIC."""
    assert condition_band("بدون رنگ") == CLEAN
    assert condition_band("صافکاری بدون رنگ") == COSMETIC
    assert condition_band("خط و خش جزئی") == COSMETIC
    assert condition_band("یک لکه رنگ") == COSMETIC
    assert condition_band("دو لکه رنگ") == PAINTED
    assert condition_band("چند لکه رنگ") == PAINTED
    assert condition_band("گلگیر رنگ") == PAINTED
    assert condition_band("یک درب رنگ") == PAINTED
    assert condition_band("کاپوت تعویض") == STRUCTURAL
    assert condition_band("اتاق تعویض") == STRUCTURAL


def test_signed_mileage_adjustment_premium():
    """Unit: Verifies low-mileage bucket adjustments allow negative haircuts (premiums)."""
    measured = {
        0: -0.15,       # 15% low-mileage premium
        20_000: -0.05,  # 5% premium
        50_000: 0.0,    # baseline
        100_000: 0.05,  # 5% haircut
        200_000: 0.15,  # 15% haircut
    }
    mono = _monotone(measured, MILEAGE_BUCKETS, non_negative=False)
    assert mono[0] == -0.15
    assert mono[20_000] == -0.05
    assert mono[50_000] == 0.0
    assert mono[100_000] == 0.05
    assert mono[200_000] == 0.15

    # Baseline adjustment test with thin bucket
    base = Baseline(base=500_000_000, peer_count=10)
    adjusted_0km = base.adjusted(mileage=5_000, mileage_haircuts=mono)
    assert adjusted_0km.adjustment is not None
    assert adjusted_0km.fair_value > 500_000_000  # Low mileage car must be worth more than worn-out median


@pytest.mark.django_db
def test_ad_filter_tokenized_persian_search():
    """Integration: Verifies AdFilter finds compound title listings across Persian and English digits."""
    brand = Brand.objects.create(name_fa="پژو", slug="peugeot")
    model = Model.objects.create(brand=brand, name_fa="206")
    ad = Ad.objects.create(
        code="ad-test-206",
        title="پژو، 206",
        brand=brand,
        model=model,
        year=1399,
        year_jalali=1399,
        year_calendar="jalali",
        current_price=450_000_000,
        status="active",
        category="car",
        transmission="دنده‌ای",
        body_status="بدون رنگ",
        publish_at=timezone.now(),
        last_seen_at=timezone.now(),
    )

    # Search with space "پژو 206"
    qs = AdFilter({"q": "پژو 206"}, queryset=Ad.objects.all()).qs
    assert qs.filter(code=ad.code).exists()

    # Search with Persian numbers "پژو ۲۰۶"
    qs_persian = AdFilter({"q": "پژو ۲۰۶"}, queryset=Ad.objects.all()).qs
    assert qs_persian.filter(code=ad.code).exists()
    assert ad.search_text == "پژو 206 206 پژو"


@pytest.mark.django_db
def test_database_verification_gate_matches_every_hard_rule():
    brand = Brand.objects.create(name_fa="کیا", slug="kia")
    model = Model.objects.create(brand=brand, name_fa="سراتو")
    ad = Ad.objects.create(code="verified01", brand=brand, model=model, title="کیا سراتو")

    assert verified(Ad.objects).get() == ad
    ad.quality_flags = [next(iter(HARD_RULE_IDS))]
    ad.save(update_fields=["quality_flags"])

    assert not verified(Ad.objects).exists()


@pytest.mark.django_db
def test_database_verification_expression_names_every_hard_rule():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT generation_expression FROM information_schema.columns "
            "WHERE table_name = 'catalog_ad' AND column_name = 'is_verified'"
        )
        expression, = cursor.fetchone()

    assert expression
    assert all(rule in expression for rule in HARD_RULE_IDS)
