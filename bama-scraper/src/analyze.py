#!/usr/bin/env python3
"""Create one ``analysis.png`` beside each usable leaf ``ads.json`` file."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

os.environ["MPLBACKEND"] = "Agg"

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from scipy.stats import linregress

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import ADS_ROOT


MIN_ADS_FOR_PLOT = 3
MIN_ADS_FOR_REGRESSION = 6
PRICE_TRIM_LOW_PERCENTILE = 5
PRICE_TRIM_HIGH_PERCENTILE = 95
PLOT_DPI = 140
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


# ---------------------------------------------------------------------------
# Bama value parsing
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


def extract_rows(ads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only ads with enough numeric data for a leaf-level plot."""
    rows: list[dict[str, Any]] = []
    for ad in ads:
        price_obj = ad.get("price") if isinstance(ad.get("price"), dict) else {}
        if price_obj.get("type") == "negotiable":
            continue
        price = parse_price(price_obj.get("price"))
        detail = ad.get("detail") if isinstance(ad.get("detail"), dict) else {}
        mileage = parse_mileage(detail.get("mileage"))
        if price is None or mileage is None:
            continue
        rows.append({
            "price": price,
            "mileage": mileage,
            "year": parse_year(detail.get("year")),
            "transmission": normalize_transmission(detail.get("gear") or detail.get("transmission")),
        })
    return rows


# ---------------------------------------------------------------------------
# Plot building
# ---------------------------------------------------------------------------

def trim_price_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """Trim extreme prices so one odd listing does not dominate the chart."""
    if df.empty:
        return df
    low, high = np.percentile(df["price"].to_numpy(dtype=float), [PRICE_TRIM_LOW_PERCENTILE, PRICE_TRIM_HIGH_PERCENTILE])
    return df[(df["price"] >= low) & (df["price"] <= high)].reset_index(drop=True)


def leaf_title(leaf_dir: Path) -> str:
    try:
        category = leaf_dir.relative_to(ADS_ROOT).parts[:4]
    except ValueError:
        category = leaf_dir.parts[-4:]
    return " › ".join(category)


def empty_panel(ax, message: str) -> None:
    ax.text(0.5, 0.5, message, ha="center", va="center", transform=ax.transAxes, fontsize=11, color="gray")
    ax.set_xticks([])
    ax.set_yticks([])


def create_analysis_plot(leaf_dir: Path, ads: list[dict[str, Any]]) -> tuple[bool, int, int]:
    """Create one analysis.png beside a leaf ads.json when enough data exists."""
    raw_rows = extract_rows(ads)
    raw_count = len(raw_rows)
    if not raw_rows:
        return False, 0, raw_count

    df = trim_price_outliers(pd.DataFrame(raw_rows))
    cleaned_count = len(df)
    if cleaned_count < MIN_ADS_FOR_PLOT:
        return False, cleaned_count, raw_count

    prices = df["price"].to_numpy(dtype=float)
    mileages = df["mileage"].to_numpy(dtype=float)
    mean_price = float(np.mean(prices))
    median_price = float(np.median(prices))
    price_cv = float(np.std(prices) / mean_price) if mean_price else 0.0
    p10, p90 = np.percentile(prices, [10, 90])
    market_depth = float((p90 - p10) / median_price) if median_price else 0.0

    sns.set_style("whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(15, 11))
    ax_hist, ax_scatter = axes[0]
    ax_trans, ax_year = axes[1]

    # Panel 1: overall price shape and dispersion.
    sns.histplot(df["price"], kde=True, ax=ax_hist, color="steelblue", edgecolor="white")
    ax_hist.axvline(mean_price, color="red", linestyle="--", label=f"Mean: {mean_price:,.0f}")
    ax_hist.axvline(median_price, color="green", linestyle="-.", label=f"Median: {median_price:,.0f}")
    ax_hist.set_title("Price Distribution (Trimmed)")
    ax_hist.set_xlabel("Price (Toman)")
    ax_hist.set_ylabel("Count")
    ax_hist.legend()
    ax_hist.text(
        0.05,
        0.95,
        f"CV: {price_cv:.3f}\nDepth: {market_depth:.3f}",
        transform=ax_hist.transAxes,
        fontsize=9,
        va="top",
        bbox=dict(boxstyle="round", alpha=0.5),
    )

    # Panel 2: mileage relationship, with a trend line only when it is meaningful.
    sns.scatterplot(data=df, x="mileage", y="price", ax=ax_scatter, color="darkorange", alpha=0.7)
    ax_scatter.set_title("Mileage vs Price")
    ax_scatter.set_xlabel("Mileage (km)")
    ax_scatter.set_ylabel("Price (Toman)")
    if cleaned_count >= MIN_ADS_FOR_REGRESSION and np.ptp(mileages) > 0:
        reg = linregress(mileages, prices)
        sns.regplot(data=df, x="mileage", y="price", ax=ax_scatter, scatter=False, color="black", line_kws={"linestyle": ":"})
        ax_scatter.text(
            0.05,
            0.05,
            f"R²: {reg.rvalue ** 2:.3f}\nΔ/10k km: {reg.slope * 10_000:,.0f}",
            transform=ax_scatter.transAxes,
            fontsize=9,
            bbox=dict(boxstyle="round", alpha=0.5),
        )

    # Panels 3-4: optional comparisons; empty panels explain missing data.
    if df["transmission"].nunique() > 1:
        sns.boxplot(data=df, x="transmission", y="price", ax=ax_trans)
        ax_trans.set_title("Price by Transmission")
        ax_trans.set_xlabel("")
        ax_trans.set_ylabel("Price (Toman)")
    else:
        empty_panel(ax_trans, "Single transmission type")

    year_df = df.dropna(subset=["year"])
    if not year_df.empty and year_df["year"].nunique() > 1:
        sns.scatterplot(data=year_df, x="year", y="price", ax=ax_year, color="purple", alpha=0.7)
        ax_year.set_title("Price by Model Year")
        ax_year.set_xlabel("Year")
        ax_year.set_ylabel("Price (Toman)")
    else:
        empty_panel(ax_year, "Year data unavailable")

    fig.suptitle(leaf_title(leaf_dir), fontsize=14, fontweight="bold")
    plt.tight_layout()
    plt.savefig(leaf_dir / "analysis.png", dpi=PLOT_DPI)
    plt.close(fig)
    return True, cleaned_count, raw_count


# ---------------------------------------------------------------------------
# CLI workflow
# ---------------------------------------------------------------------------

def main() -> None:
    print("Starting leaf analysis...", flush=True)
    if not ADS_ROOT.exists():
        print(f"BAMA ADS folder not found at {ADS_ROOT}", flush=True)
        return

    leaf_dirs = [path.parent for path in sorted(ADS_ROOT.rglob("ads.json"))]
    print(f"Found {len(leaf_dirs):,} leaf folders.", flush=True)
    processed = skipped = errors = 0

    for index, leaf_dir in enumerate(leaf_dirs, 1):
        rel = leaf_dir.relative_to(ADS_ROOT)
        print(f"({index}/{len(leaf_dirs)}) {rel} ", end="", flush=True)
        try:
            data = json.loads((leaf_dir / "ads.json").read_text(encoding="utf-8"))
            ads = data if isinstance(data, list) else []
            done, cleaned, raw = create_analysis_plot(leaf_dir, ads)
            if done:
                processed += 1
                print(f"created raw={raw} cleaned={cleaned}", flush=True)
            else:
                skipped += 1
                print(f"skipped raw={raw} cleaned={cleaned}", flush=True)
        except Exception as exc:
            errors += 1
            print(f"error: {exc}", flush=True)

    print(f"Finished. analysis_png={processed:,} skipped={skipped:,} errors={errors:,}", flush=True)


if __name__ == "__main__":
    main()
