from pathlib import Path

from audit import run_checks, backfill_dates
from history import record_observation, start_run
from store import open_store, upsert_ad, mark_inactive


def _ad(code, price=100.0, publish=None, detail_time="", raw=None):
    return {
        "brand": "تویوتا", "model": "کمری", "variant": "GLX", "category": "آگهی ها",
        "year": 2018, "mileage": 50000.0, "price": price, "price_type": "fixed",
        "transmission": "اتوماتیک", "publish_date_jalali": publish, "fetch_time_ts": 1.0,
        "raw_payload": raw if raw is not None else {"detail": {"code": code, "time": detail_time}},
    }


def test_run_checks_counts(tmp_path: Path):
    conn = open_store(tmp_path / "bama.db")
    run_id = start_run(conn, "live_fetch", 2)
    upsert_ad(conn, "a", _ad("a", price=100.0), 10.0)
    upsert_ad(conn, "b", _ad("b", price=None), 10.0)
    record_observation(conn, run_id, "a", {"detail": {"code": "a"}}, 10.0, "c/b/m/v", "live_fetch")
    mark_inactive(conn, cutoff_ts=20.0)  # both stale -> removed
    checks = run_checks(conn)
    assert checks["ads_active"] == 0
    assert checks["ads_removed"] == 2
    assert checks["priced_ads"] == 1
    assert checks["missing_publish_dates"] == 2
    assert checks["unfinished_runs"] == 1  # run never finished
    assert checks["orphan_observations"] == 0


def test_backfill_dates_from_absolute_time(tmp_path: Path):
    conn = open_store(tmp_path / "bama.db")
    upsert_ad(conn, "a", _ad("a", detail_time="1402/05/10"), 10.0)  # absolute Jalali
    upsert_ad(conn, "b", _ad("b", detail_time="دیروز"), 10.0)       # relative -> unfillable
    filled = backfill_dates(conn)
    assert filled == 1
    row_a = conn.execute("SELECT publish_date_jalali FROM ads WHERE code='a';").fetchone()
    row_b = conn.execute("SELECT publish_date_jalali FROM ads WHERE code='b';").fetchone()
    assert row_a["publish_date_jalali"] is not None and "1402/05/10" in row_a["publish_date_jalali"]
    assert row_b["publish_date_jalali"] is None
