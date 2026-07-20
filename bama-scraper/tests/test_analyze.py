from pathlib import Path

import numpy as np

from analyze import compute_all, group_stats, parse_price, parse_mileage, parse_year
from store import open_store, upsert_ad


def _ad(code, price, mileage, brand="تویوتا", model="کمری", variant="GLX",
        category="آگهی ها", price_type="fixed"):
    return {
        "brand": brand, "model": model, "variant": variant, "category": category,
        "year": 2018, "mileage": float(mileage), "price": float(price),
        "price_type": price_type, "transmission": "اتوماتیک",
        "publish_date_jalali": None, "fetch_time_ts": 1.0,
        "raw_payload": {"detail": {"code": code}},
    }


def test_parse_helpers():
    assert parse_price("1,234") == 1234.0
    assert parse_price("0") is None
    assert parse_mileage("صفر کیلومتر") == 0.0
    assert parse_mileage("۵۰,۰۰۰") == 50000.0
    assert parse_year("۱۳۹۷") == 1397
    assert parse_year("2018") == 2018
    assert parse_year("abcd") is None


def test_group_stats_math_on_known_fixture():
    import pandas as pd
    prices = [100.0 + 10 * i for i in range(10)]        # 100..190
    mileages = [10000.0 + 10000 * i for i in range(10)]  # 10k..100k
    df = pd.DataFrame({"price": prices, "mileage": mileages})
    stats = group_stats(df)
    assert stats is not None
    low, high = np.percentile(prices, [5, 95])
    trimmed = [p for p in prices if low <= p <= high]
    assert len(trimmed) >= 6  # regression needs the trimmed set to stay large enough
    assert stats["mean_price"] == float(np.mean(trimmed))
    assert stats["median_price"] == float(np.median(trimmed))
    # Perfectly linear price vs mileage => slope 0.001, r2 == 1.
    assert stats["regression_r2"] == 1.0
    assert abs(stats["regression_slope"] - 0.001) < 1e-9


def test_group_stats_too_sparse_returns_none():
    import pandas as pd
    df = pd.DataFrame({"price": [100.0, 110.0], "mileage": [1000.0, 2000.0]})
    assert group_stats(df) is None


def test_compute_all_writes_one_row_per_group(tmp_path: Path):
    conn = open_store(tmp_path / "bama.db")
    for i in range(6):
        upsert_ad(conn, f"c{i}", _ad(f"c{i}", 100 + i * 10, 10000 + i * 10000), 1.0)
    # A negotiable ad should not feed the group stats.
    upsert_ad(conn, "neg", _ad("neg", 999, 5000, price_type="negotiable"), 1.0)
    written = compute_all(conn)
    assert written == 1
    row = conn.execute("SELECT category, brand, model, variant, raw_count FROM analysis_stats;").fetchone()
    assert (row["category"], row["brand"], row["model"], row["variant"]) == ("آگهی ها", "تویوتا", "کمری", "GLX")
    assert row["raw_count"] == 6  # negotiable excluded


def test_compute_all_empty_db_is_zero(tmp_path: Path):
    conn = open_store(tmp_path / "bama.db")
    assert compute_all(conn) == 0
