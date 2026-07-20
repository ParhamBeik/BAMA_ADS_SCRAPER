from pathlib import Path

from store import open_store, upsert_ad, mark_inactive, counts


def _fields(price=100.0, **over):
    base = {
        "brand": "تویوتا", "model": "کمری", "variant": "GLX", "category": "آگهی ها",
        "year": 2018, "mileage": 50000.0, "price": price, "price_type": "fixed",
        "transmission": "اتوماتیک", "publish_date_jalali": None, "fetch_time_ts": 1.0,
        "raw_payload": {"detail": {"code": "x"}},
    }
    base.update(over)
    return base


def test_upsert_inserts_then_updates(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "bama.db")
    assert upsert_ad(conn, "a", _fields(price=100.0), 10.0) is True
    assert upsert_ad(conn, "a", _fields(price=200.0), 20.0) is False
    row = conn.execute("SELECT price, first_seen_ts, last_seen_ts FROM ads WHERE code='a';").fetchone()
    assert row["price"] == 200.0
    assert row["first_seen_ts"] == 10.0  # preserved across updates
    assert row["last_seen_ts"] == 20.0


def test_resight_clears_removed_status(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "bama.db")
    upsert_ad(conn, "a", _fields(), 10.0)
    mark_inactive(conn, cutoff_ts=15.0)
    assert conn.execute("SELECT status FROM ads WHERE code='a';").fetchone()["status"] == "removed"
    upsert_ad(conn, "a", _fields(), 20.0)  # seen again
    row = conn.execute("SELECT status, removed_at FROM ads WHERE code='a';").fetchone()
    assert row["status"] == "active"
    assert row["removed_at"] is None


def test_mark_inactive_only_flips_stale_active(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "bama.db")
    upsert_ad(conn, "old", _fields(), 10.0)
    upsert_ad(conn, "new", _fields(), 30.0)
    flipped = mark_inactive(conn, cutoff_ts=20.0)
    assert flipped == 1
    assert counts(conn) == {"ads_active": 1, "ads_removed": 1, "ads_total": 2}


def test_raw_payload_roundtrips(tmp_path: Path) -> None:
    import json
    conn = open_store(tmp_path / "bama.db")
    payload = {"detail": {"code": "a", "title": "برند،مدل"}, "price": {"price": "100"}}
    upsert_ad(conn, "a", _fields(raw_payload=payload), 10.0)
    stored = conn.execute("SELECT raw_payload FROM ads WHERE code='a';").fetchone()["raw_payload"]
    assert json.loads(stored) == payload
