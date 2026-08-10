"""Unit tests for apps.parsing.normalize.

Unit level: both functions are side-effect-free string -> value transforms with
no DB or Django dependency, so unit tests are the cheapest layer that can catch
a calendar misclassification or a zero-km collapse.
"""

from __future__ import annotations

import pytest

from apps.parsing.normalize import (
    JALALI_GREGORIAN_OFFSET,
    normalize_model_year,
    parse_mileage,
)


class TestNormalizeModelYear:
    def test_jalali_and_gregorian_anchors(self):
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

    @pytest.mark.parametrize(
        ("raw", "calendar"),
        [
            (1300, "jalali"),
            (1420, "jalali"),
            (1900, "gregorian"),
            (2100, "gregorian"),
            (1421, "unknown"),
            (1899, "unknown"),
            (1600, "unknown"),
            (2101, "unknown"),
            (1299, "unknown"),
        ],
    )
    def test_band_boundaries(self, raw, calendar):
        assert normalize_model_year(raw)[2] == calendar

    @pytest.mark.parametrize("raw", [None, "", "   ", "abc", "0", "-1", "-", True, False])
    def test_unparseable_is_unknown(self, raw):
        assert normalize_model_year(raw) == (None, None, "unknown")

    def test_persian_digits(self):
        assert normalize_model_year("۱۳۹۹") == (1399, 2020, "jalali")
        assert normalize_model_year("۲۰۲۵") == (1404, 2025, "gregorian")

    def test_int_and_separator_forms_agree(self):
        assert normalize_model_year(1399) == normalize_model_year("1399")
        assert normalize_model_year(" 1399 ") == (1399, 2020, "jalali")
        assert normalize_model_year("مدل 2017") == (1396, 2017, "gregorian")

    def test_bands_do_not_overlap(self):
        """Guards the classifier: no Jalali year may land in the Gregorian band."""
        for year in range(1300, 1421):
            assert normalize_model_year(year)[2] == "jalali"

    def test_range_string_takes_leading_year(self):
        """A dash-joined range must not crash int(); take the first year."""
        assert normalize_model_year("1399-1400") == (1399, 2020, "jalali")


class TestParseMileage:
    def test_zero_kilometer_phrase_is_zero_not_none(self):
        result = parse_mileage("صفر کیلومتر")
        assert result is not None
        assert result == 0

    @pytest.mark.parametrize("raw", ["صفر", "صفر کیلومتر", "کارکرد صفر"])
    def test_zero_km_variants(self, raw):
        assert parse_mileage(raw) == 0

    @pytest.mark.parametrize("raw", ["120,000", "۱۲۰,۰۰۰", "120000 کیلومتر", "120,000 km", 120000])
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
