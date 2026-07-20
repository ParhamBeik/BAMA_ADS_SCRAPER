#!/usr/bin/env python3
"""Compute per-category market stats from ``bama.db`` into ``analysis_stats``.

Replaces the old per-leaf ``analysis.png`` outputs: instead of drawing charts,
``compute_all`` groups the ``ads`` table by (category, brand, model, variant),
runs the same trim/CV/depth/regression math, and stores one row per group so the
numbers are queryable straight from SQL.
"""

from __future__ import annotations

import re
import time
from typing import Any

import numpy as np
import pandas as pd


MIN_ADS_FOR_STATS = 3
MIN_ADS_FOR_REGRESSION = 6
PRICE_TRIM_LOW_PERCENTILE = 5
PRICE_TRIM_HIGH_PERCENTILE = 95
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

_DIMS = ("category", "brand", "model", "variant")


# ---------------------------------------------------------------------------
# Bama value parsing (also used by fetch.py to fill the ads columns)
# ---------------------------------------------------------------------------

def normalize_digits(text: str) -> str:
    return text.translate(PERSIAN_DIGITS)


def parse_price(raw_value: Any) -> float | None:
    if raw_value in (None, "", "0"):
        return None
    digits = re.findall(r"[\d,]+", normalize_digits(str(raw_value)))
    if not digits:
        return None
    try:
        value = float(digits[0].replace(",", ""))
    except ValueError:
        return None
    return value if value > 0 else None


def parse_mileage(raw_value: Any) -> float | None:
    if not raw_value:
        return None
    text = normalize_digits(str(raw_value).strip())
    if "صفر" in text and "کیلومتر" in text:
        return 0.0
    match = re.search(r"[\d,]+", text)
    return float(match.group().replace(",", "")) if match else None


def parse_year(raw_value: Any) -> int | None:
    if not raw_value:
        return None
    match = re.search(r"\d{4}", normalize_digits(str(raw_value)))
    if not match:
        return None
    year = int(match.group())
    return year if 1300 <= year <= 1420 or 1990 <= year <= 2030 else None


def normalize_transmission(raw_value: Any) -> str:
    if not raw_value:
        return "نامشخص"
    text = str(raw_value)
    if "اتومات" in text:
        return "اتوماتیک"
    if "دنده" in text or "معمول" in text:
        return "دنده‌ای"
    return text.strip() or "نامشخص"


# ---------------------------------------------------------------------------
# Stats math
# ---------------------------------------------------------------------------

def trim_price_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """Trim extreme prices so one odd listing does not dominate the group stats."""
    if df.empty:
        return df
    low, high = np.percentile(df["price"].to_numpy(dtype=float), [PRICE_TRIM_LOW_PERCENTILE, PRICE_TRIM_HIGH_PERCENTILE])
    return df[(df["price"] >= low) & (df["price"] <= high)].reset_index(drop=True)


def group_stats(df: pd.DataFrame) -> dict[str, Any] | None:
    """Return market stats for one dimension group, or None if too sparse.

    ``df`` holds priced+mileaged ads for a single (category, brand, model,
    variant). Regression is a numpy least-squares fit (slope + R^2).
    """
    raw_count = len(df)
    trimmed = trim_price_outliers(df)
    cleaned_count = len(trimmed)
    if cleaned_count < MIN_ADS_FOR_STATS:
        return None

    prices = trimmed["price"].to_numpy(dtype=float)
    mileages = trimmed["mileage"].to_numpy(dtype=float)
    mean_price = float(np.mean(prices))
    median_price = float(np.median(prices))
    price_cv = float(np.std(prices) / mean_price) if mean_price else 0.0
    p10, p90 = np.percentile(prices, [10, 90])
    market_depth = float((p90 - p10) / median_price) if median_price else 0.0

    slope = r2 = None
    if cleaned_count >= MIN_ADS_FOR_REGRESSION and float(np.ptp(mileages)) > 0:
        slope_val, intercept = np.polyfit(mileages, prices, 1)
        predicted = slope_val * mileages + intercept
        ss_res = float(np.sum((prices - predicted) ** 2))
        ss_tot = float(np.sum((prices - np.mean(prices)) ** 2))
        slope = float(slope_val)
        r2 = float(1 - ss_res / ss_tot) if ss_tot else 0.0

    return {
        "raw_count": raw_count,
        "cleaned_count": cleaned_count,
        "mean_price": mean_price,
        "median_price": median_price,
        "price_cv": price_cv,
        "market_depth": market_depth,
        "regression_slope": slope,
        "regression_r2": r2,
    }


# ---------------------------------------------------------------------------
# DB entry point (invoked by fetch.py's auto-pipeline)
# ---------------------------------------------------------------------------

def compute_all(conn) -> int:
    """Recompute ``analysis_stats`` from all active priced+mileaged ads.

    Returns the number of stat rows written. Only active ads with a numeric
    price and mileage feed the stats; negotiable-price ads are excluded.
    """
    rows = conn.execute(
        """SELECT category, brand, model, variant, price, mileage
             FROM ads
            WHERE status = 'active' AND price IS NOT NULL AND mileage IS NOT NULL
              AND (price_type IS NULL OR price_type != 'negotiable');"""
    ).fetchall()
    conn.execute("DELETE FROM analysis_stats;")
    if not rows:
        conn.commit()
        return 0

    df = pd.DataFrame([dict(r) for r in rows])
    computed_ts = time.time()
    written = 0
    cols = (*_DIMS, "raw_count", "cleaned_count", "mean_price", "median_price",
            "price_cv", "market_depth", "regression_slope", "regression_r2", "computed_ts")
    for keys, group in df.groupby(list(_DIMS)):
        stats = group_stats(group)
        if stats is None:
            continue
        payload = dict(zip(_DIMS, keys))
        payload.update(stats)
        payload["computed_ts"] = computed_ts
        conn.execute(
            f"INSERT OR REPLACE INTO analysis_stats ({', '.join(cols)}) "
            f"VALUES ({', '.join(':' + c for c in cols)});",
            payload,
        )
        written += 1
    conn.commit()
    return written


def main() -> None:
    from store import open_store
    conn = open_store()
    written = compute_all(conn)
    print(f"analysis_stats rows written: {written:,}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
