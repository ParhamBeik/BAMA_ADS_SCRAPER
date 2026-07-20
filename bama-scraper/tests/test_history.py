import sqlite3
from pathlib import Path

from history import record_observation, start_run
from store import open_store


def payload(code: str, price: str = "100", time: str = "دیروز", rank: str = "1") -> dict:
    return {"detail": {"code": code, "title": "برند، مدل", "time": time, "rank": rank}, "price": {"price": price}}


def count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def test_record_observation_reuses_versions_and_tracks_reverts(tmp_path: Path) -> None:
    conn = open_history(tmp_path / "history.db")
    runs = [start_run(conn, "live_fetch", 1) for _ in range(4)]
    assert record_observation(conn, runs[0], "abc", payload("abc"), 1.0, "a/ads.json", "live_fetch") == (True, False)
    assert record_observation(conn, runs[1], "abc", payload("abc", time="امروز", rank="2"), 2.0, "a/ads.json", "live_fetch") == (False, False)
    assert record_observation(conn, runs[2], "abc", payload("abc", price="90"), 3.0, "a/ads.json", "live_fetch") == (True, True)
    assert record_observation(conn, runs[3], "abc", payload("abc"), 4.0, "a/ads.json", "live_fetch") == (False, True)
    assert count(conn, "ad_versions") == 2
    assert count(conn, "ad_observations") == 4
    assert count(conn, "change_events") == 2


def test_record_observation_is_idempotent_per_run_code(tmp_path: Path) -> None:
    conn = open_history(tmp_path / "history.db")
    run_id = start_run(conn, "live_fetch", 1)
    assert record_observation(conn, run_id, "abc", payload("abc"), 1.0, "a/ads.json", "live_fetch") == (True, False)
    assert record_observation(conn, run_id, "abc", payload("abc"), 1.0, "a/ads.json", "live_fetch") == (False, False)
    assert count(conn, "ad_versions") == 1
    assert count(conn, "ad_observations") == 1
