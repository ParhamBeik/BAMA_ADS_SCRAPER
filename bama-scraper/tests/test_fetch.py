from pathlib import Path

import fetch
from fetch import AdWriter, derive_dims, ad_columns, iter_ads
from history import start_run
from store import open_store


def _ad(code, brand="تویوتا", model="کمری", price="100", mileage="۵۰,۰۰۰"):
    return {
        "detail": {"code": code, "title": f"{brand}، {model}", "year": "1397",
                   "mileage": mileage, "gear": "اتوماتیک", "time": "دیروز", "rank": "1"},
        "price": {"price": price, "type": "fixed"},
    }


def test_derive_dims_splits_title_and_applies_alias():
    dims = derive_dims(_ad("a", brand="تویوتا"), {"تویوتا": "TOYOTA"})
    assert dims["brand"] == "TOYOTA"
    assert dims["model"] == "کمری"
    assert dims["category"] == "آگهی ها"


def test_ad_columns_flattens_payload():
    cols = ad_columns(_ad("a"), {}, publish_date_jalali="1402/05/10", fetch_time_ts=5.0)
    assert cols["year"] == 1397
    assert cols["mileage"] == 50000.0
    assert cols["price"] == 100.0
    assert cols["price_type"] == "fixed"
    assert cols["publish_date_jalali"] == "1402/05/10"
    assert cols["raw_payload"]["detail"]["code"] == "a"


def test_iter_ads_dedups_and_stops_on_stale(monkeypatch):
    # Page 1 has fresh codes; deep pages repeat them => stale-page stop.
    def fake_fetch_page(session, page):
        return [_ad("a"), _ad("b")]  # every page wraps to the same seen codes
    monkeypatch.setattr(fetch, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(fetch.time, "sleep", lambda *_: None)
    got = [ad["detail"]["code"] for ad, _ in iter_ads(session=None, max_ads=100, page_pause=0)]
    assert got == ["a", "b"]  # each code yielded once, then stale pages end it


def test_writer_flush_writes_ads_and_observations(tmp_path: Path):
    conn = open_store(tmp_path / "bama.db")
    run_id = start_run(conn, "live_fetch", 5)
    writer = AdWriter(conn, run_id, time_dict={}, brand_aliases={}, batch_size=100)
    writer.buffer_ad(_ad("a"))
    writer.buffer_ad(_ad("b"))
    writer.flush()
    assert conn.execute("SELECT COUNT(*) FROM ads;").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM ad_observations WHERE run_id=?;", (run_id,)).fetchone()[0] == 2
    assert writer.total_new == 2
    assert writer.total_versions_created == 2
