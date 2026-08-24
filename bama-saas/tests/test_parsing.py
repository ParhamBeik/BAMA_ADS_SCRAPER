"""Unit tests for apps.jobs.parsing.

Unit level throughout: every function here is a side-effect-free
string -> value transform with no DB or Django dependency, so this is the
cheapest layer that can catch a calendar misclassification, a zero-km collapse
or a semantic hash that stopped meaning anything.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.jobs import parsing as P
from apps.jobs.parsing import (
    JALALI_GREGORIAN_OFFSET,
    extract_ad,
    fingerprint,
    normalize_model_year,
    parse_int,
    parse_mileage,
    parse_publish_time,
    payload_hashes,
)


def test_parse_int_handles_bama_formats():
    assert parse_int("۱,۲۳۴ km", positive=True) == 1234
    assert parse_int("0", positive=True) is None
    assert parse_int(None) is None
    # A dash-joined range must not crash int(); take the leading number.
    assert parse_int("1399-1400") == 1399


def test_parse_publish_time_absolute_relative_and_unknown():
    observed = datetime(2026, 7, 5, 12, tzinfo=timezone.utc)
    assert parse_publish_time("2 ساعت پیش", observed).hour == 10
    assert parse_publish_time("1405/2/10", observed) is not None
    assert parse_publish_time("بعداً شاید", observed) is None


def test_extract_ad_normalizes_query_fields():
    payload = {
        "detail": {"code": "abc", "title": "پیکان، سدان", "brand_fa": "پیکان",
                   "year": "۱۳۸۳", "mileage": "400,000 km", "type": "car", "time": "دیروز"},
        "price": {"price": "290,000,000", "payment": "0", "type": "lumpsum"},
    }
    row = extract_ad(payload, datetime.now(timezone.utc))
    assert row and row["code"] == "abc"
    assert row["model"] == "سدان"
    assert row["year"] == 1383 and row["mileage"] == 400000
    assert row["current_price"] == 290000000 and row["current_payment"] is None


def test_extract_ad_needs_a_code():
    assert extract_ad({"detail": {"title": "x"}}, datetime.now(timezone.utc)) is None


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def test_fingerprint_is_stable_across_key_order():
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})


def test_semantic_hash_ignores_observation_only_fields():
    base = {"detail": {"code": "abc", "time": "دیروز", "rank": "1"}, "price": {"price": "1"}}
    moved = {"detail": {"code": "abc", "time": "امروز", "rank": "2"}, "price": {"price": "1"}}
    assert payload_hashes(base)[0] != payload_hashes(moved)[0]
    assert payload_hashes(base)[1] == payload_hashes(moved)[1]


def test_semantic_hash_ignores_dealer_wide_and_derived_fields():
    """Measured, not guessed: across 1,125 consecutive version pairs from the
    live database, 81.2% differed ONLY in fields like these — dealer-wide counts,
    SEO metadata, the source's own bookkeeping timestamp. It matters beyond
    storage: "a new version appeared" is how the delta crawler knows a page is
    still moving, and a signal that fires 81% of the time is not a signal.
    """
    base = {
        "detail": {"code": "abc", "modified_date": "2026-08-01"},
        "dealer": {"name": "X", "ad_count": 10, "score": 4.1},
        "metadata": {"title_tag": "a"},
        "price": {"price": "1"},
    }
    noise = {
        "detail": {"code": "abc", "modified_date": "2026-08-09"},
        "dealer": {"name": "X", "ad_count": 47, "score": 4.8},
        "metadata": {"title_tag": "b"},
        "price": {"price": "1"},
    }
    assert payload_hashes(base)[0] != payload_hashes(noise)[0], "raw_hash records everything"
    assert payload_hashes(base)[1] == payload_hashes(noise)[1], "semantic_hash must not move"


def test_real_content_changes_still_produce_a_new_semantic_hash():
    """The other half: over-excluding would make the crawler blind to a live page."""
    base = {
        "detail": {"code": "abc", "description": "clean", "image_count": 3},
        "images": ["a.jpg"],
        "price": {"price": "1000"},
    }
    for changed in (
        {**base, "detail": {**base["detail"], "description": "rewritten"}},
        {**base, "detail": {**base["detail"], "image_count": 9}},
        {**base, "images": ["a.jpg", "b.jpg"]},
        {**base, "price": {"price": "900"}},
    ):
        assert payload_hashes(base)[1] != payload_hashes(changed)[1], changed


def test_dropping_a_missing_path_is_harmless():
    """Payloads are not uniform; a private listing has no dealer key."""
    assert payload_hashes({"detail": {"code": "abc"}})[1]
    assert payload_hashes({})[1]


# ---------------------------------------------------------------------------
# Model year
# ---------------------------------------------------------------------------


class TestNormalizeModelYear:
    def test_anchors(self):
        assert normalize_model_year("1399") == (1399, 2020, "jalali")
        assert normalize_model_year("2025") == (1404, 2025, "gregorian")

    @pytest.mark.parametrize("jalali", [1300, 1382, 1399, 1404, 1420])
    def test_round_trip_jalali(self, jalali):
        _, gregorian, calendar = normalize_model_year(jalali)
        assert calendar == "jalali"
        assert gregorian - JALALI_GREGORIAN_OFFSET == jalali

    @pytest.mark.parametrize("gregorian", [1900, 1976, 2007, 2025, 2100])
    def test_round_trip_gregorian(self, gregorian):
        jalali, out, calendar = normalize_model_year(gregorian)
        assert calendar == "gregorian"
        assert out == gregorian
        assert jalali + JALALI_GREGORIAN_OFFSET == gregorian

    @pytest.mark.parametrize(("raw", "calendar"), [
        (1300, "jalali"), (1420, "jalali"), (1900, "gregorian"), (2100, "gregorian"),
        (1421, "unknown"), (1899, "unknown"), (1600, "unknown"),
        (2101, "unknown"), (1299, "unknown"),
    ])
    def test_band_boundaries(self, raw, calendar):
        assert normalize_model_year(raw)[2] == calendar

    @pytest.mark.parametrize("raw", [None, "", "   ", "abc", "0", "-1", "-", True, False])
    def test_unparseable_is_unknown(self, raw):
        assert normalize_model_year(raw) == (None, None, "unknown")

    def test_persian_digits_and_separators(self):
        assert normalize_model_year("۱۳۹۹") == (1399, 2020, "jalali")
        assert normalize_model_year("۲۰۲۵") == (1404, 2025, "gregorian")
        assert normalize_model_year(1399) == normalize_model_year("1399")
        assert normalize_model_year(" 1399 ") == (1399, 2020, "jalali")
        assert normalize_model_year("مدل 2017") == (1396, 2017, "gregorian")
        assert normalize_model_year("1399-1400") == (1399, 2020, "jalali")

    def test_bands_do_not_overlap(self):
        """No Jalali year may land in the Gregorian band."""
        for year in range(1300, 1421):
            assert normalize_model_year(year)[2] == "jalali"


# ---------------------------------------------------------------------------
# Mileage
# ---------------------------------------------------------------------------


class TestParseMileage:
    @pytest.mark.parametrize("raw", ["صفر", "صفر کیلومتر", "کارکرد صفر"])
    def test_zero_km_phrase_is_zero_not_none(self, raw):
        # parse_int returns None here, which would lose a *known* mileage of 0
        # on ~33% of the corpus.
        assert parse_mileage(raw) == 0

    @pytest.mark.parametrize(
        "raw", ["120,000", "۱۲۰,۰۰۰", "120000 کیلومتر", "120,000 km", 120000]
    )
    def test_numeric_forms(self, raw):
        assert parse_mileage(raw) == 120000

    def test_explicit_zero_digits(self):
        assert parse_mileage("0 km") == 0
        assert parse_mileage(0) == 0

    @pytest.mark.parametrize("raw", [None, "", "   ", "abc", "km", True, False])
    def test_missing_or_garbage_is_none(self, raw):
        assert parse_mileage(raw) is None

    @pytest.mark.parametrize("raw", ["-1", -1, "-5000 km"])
    def test_negative_sentinel_is_none(self, raw):
        assert parse_mileage(raw) is None

    def test_low_but_real_mileage_survives(self):
        """Seed data has 160 ads at "1 km"; that is real, not a sentinel."""
        assert parse_mileage("1 km") == 1


# --- listing identity across ad codes -------------------------------------------
#
# Pure unit level: the function takes flat values and returns a hash, so the
# whole contract is observable without a database.


def _fp(**overrides):
    base = dict(
        brand="پژو", model="405", trim="دنده‌ای", year="1399", mileage="120,000",
        location="تهران - مرکز", body_color="سفید", description="خودرو سالم",
    )
    return P.listing_fingerprint(**{**base, **overrides})


def test_the_same_car_relisted_keeps_its_fingerprint():
    """The whole point: a new Bama code must not change the identity."""
    assert _fp() == _fp()


def test_price_is_not_part_of_the_identity():
    """Relisting cheaper is the commonest reason to relist. Including price
    would miss exactly the cases this exists to catch — there is no price
    argument at all, and this test says that is deliberate."""
    import inspect

    assert "price" not in inspect.signature(P.listing_fingerprint).parameters


def test_formatting_and_persian_digits_do_not_change_the_identity():
    assert _fp(description="خودرو   سالم") == _fp()
    assert _fp(mileage="۱۲۰,۰۰۰") == _fp()
    assert _fp(year="۱۳۹۹") == _fp()


def test_a_different_car_gets_a_different_fingerprint():
    assert _fp(mileage="90,000") != _fp()
    assert _fp(body_color="مشکی") != _fp()
    assert _fp(description="تصادفی") != _fp()


def test_both_calendars_of_one_model_year_agree():
    """1399 and 2020 are the same model year, and a repost must survive Bama
    switching which calendar it publishes."""
    assert _fp(year="2020") == _fp(year="1399")


def test_an_unidentifiable_ad_gets_no_fingerprint():
    """Blank, so callers skip it. Two thin ads must never match each other."""
    assert _fp(model=None) == ""
    assert _fp(year=None) == ""
    assert _fp(year="not a year") == ""


def test_missing_mileage_is_still_identifiable():
    """Bama omits mileage on some ads; that is not enough to give up on."""
    assert _fp(mileage=None) != ""
