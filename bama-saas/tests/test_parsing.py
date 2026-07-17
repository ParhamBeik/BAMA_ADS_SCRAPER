from datetime import datetime, timezone

from apps.parsing import categories_for, extract_ad, fingerprint, parse_int, payload_hashes, parse_publish_time


def test_parse_int_handles_bama_formats() -> None:
    assert parse_int("۱,۲۳۴ km", positive=True) == 1234
    assert parse_int("0", positive=True) is None
    assert parse_int(None) is None


def test_parse_publish_time_absolute_relative_and_unknown() -> None:
    observed = datetime(2026, 7, 5, 12, tzinfo=timezone.utc)
    assert parse_publish_time("2 ساعت پیش", observed).hour == 10
    assert parse_publish_time("1405/2/10", observed) is not None
    unknown: list[str] = []
    assert parse_publish_time("بعداً شاید", observed, unknown.append) is None
    assert unknown == ["بعداً شاید"]


def test_extract_ad_normalizes_query_fields() -> None:
    payload = {"detail": {"code": "abc", "title": "پیکان، سدان", "brand_fa": "پیکان", "year": "۱۳۸۳",
               "mileage": "400,000 km", "type": "car", "time": "دیروز"},
               "price": {"price": "290,000,000", "payment": "0", "type": "lumpsum"}}
    row = extract_ad(payload, datetime.now(timezone.utc))
    assert row and row["code"] == "abc"
    assert row["model"] == "سدان"
    assert row["year"] == 1383 and row["mileage"] == 400000
    assert row["current_price"] == 290000000 and row["current_payment"] is None


def test_fingerprint_is_stable_across_key_order() -> None:
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})


def test_semantic_hash_ignores_observation_only_fields() -> None:
    base = {"detail": {"code": "abc", "time": "دیروز", "rank": "1"}, "price": {"price": "1"}}
    changed_observation = {"detail": {"code": "abc", "time": "امروز", "rank": "2"}, "price": {"price": "1"}}
    assert payload_hashes(base)[0] != payload_hashes(changed_observation)[0]
    assert payload_hashes(base)[1] == payload_hashes(changed_observation)[1]


def test_categories_cover_core_change_paths() -> None:
    assert categories_for(["/price/price"]) == ["price/payment"]
    assert categories_for(["/detail/mileage"]) == ["mileage"]
    assert categories_for(["/images"]) == ["media"]
