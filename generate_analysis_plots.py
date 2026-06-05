#!/usr/bin/env python3
from __future__ import annotations

import os
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ["MPLBACKEND"] = "Agg"

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
from scipy.stats import variation

# ===================== CONFIGURATION =====================
BASE_DIR = Path(__file__).resolve().parent
ADS_ROOT = BASE_DIR / "BAMA ADS"
RANKING_REPORT_PATH = BASE_DIR / "liquidity_ranking_report.txt"
MIN_ADS_FOR_PLOT = 3
PRICE_TRIM_LOW_PERCENTILE = 5
PRICE_TRIM_HIGH_PERCENTILE = 95

# Liquidity score weights:
# score = w1 * log1p(N) + w2 * (1 - min(CV, 1))
# N = cleaned ad volume, CV = price std / mean
WEIGHT_VOLUME = 0.6
WEIGHT_PRICE_STABILITY = 0.4
# =========================================================


@dataclass
class LiquidityRecord:
    brand: str
    model: str
    variant: str
    volume: int
    mean_price: float
    price_cv: float
    liquidity_score: float

    @property
    def car_name(self) -> str:
        return f"{self.brand} / {self.model} / {self.variant}"


def parse_price(raw_value: Any) -> float | None:
    """Parse price text into numeric toman value."""
    if raw_value in (None, "", "0"):
        return None
    digits = re.findall(r"[\d,]+", str(raw_value))
    if not digits:
        return None
    try:
        return float(digits[0].replace(",", ""))
    except ValueError:
        return None


def mileage_km(text: Any) -> float | None:
    """Convert mileage text to kilometer float."""
    if not text:
        return None
    text = str(text).strip()
    if "صفر" in text and "کیلومتر" in text:
        return 0.0
    match = re.search(r"[\d,]+", text)
    return float(match.group().replace(",", "")) if match else None


def extract_price_mileage_pairs(ads: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """Extract valid (price, mileage) pairs from ads."""
    pairs: list[tuple[float, float]] = []
    for ad in ads:
        price_obj = ad.get("price", {})
        if price_obj.get("type") == "negotiable":
            continue
        price_val = parse_price(price_obj.get("price"))
        if price_val is None:
            continue
        km_val = mileage_km(ad.get("detail", {}).get("mileage"))
        if km_val is None:
            continue
        pairs.append((price_val, km_val))
    return pairs


def trim_pairs_by_price_percentile(
    pairs: list[tuple[float, float]],
    low_percentile: float = PRICE_TRIM_LOW_PERCENTILE,
    high_percentile: float = PRICE_TRIM_HIGH_PERCENTILE,
) -> list[tuple[float, float]]:
    """Remove outliers by keeping only pairs within [P5, P95] price range."""
    if not pairs:
        return []
    prices = np.array([price for price, _ in pairs], dtype=float)
    p_low, p_high = np.percentile(prices, [low_percentile, high_percentile])
    return [(price, km) for price, km in pairs if p_low <= price <= p_high]


def compute_liquidity_record(
    brand: str,
    model: str,
    variant: str,
    cleaned_pairs: list[tuple[float, float]],
) -> LiquidityRecord:
    """Compute liquidity metrics and final composite score."""
    prices = np.array([price for price, _ in cleaned_pairs], dtype=float)
    volume = int(len(prices))
    mean_price = float(np.mean(prices))
    cv = float(variation(prices, ddof=0, nan_policy="omit"))
    if not np.isfinite(cv):
        cv = 0.0
    cv = abs(cv)

    score = (
        WEIGHT_VOLUME * np.log1p(volume)
        + WEIGHT_PRICE_STABILITY * (1.0 - min(cv, 1.0))
    )
    return LiquidityRecord(
        brand=brand,
        model=model,
        variant=variant,
        volume=volume,
        mean_price=mean_price,
        price_cv=cv,
        liquidity_score=float(score),
    )


def get_leaf_identity(leaf_dir: Path) -> tuple[str, str, str]:
    """Extract (brand, model, variant) from leaf folder path."""
    variant = leaf_dir.name
    model = leaf_dir.parent.name
    brand = leaf_dir.parent.parent.name
    return brand, model, variant


def create_analysis_plot(
    leaf_dir: Path,
    ads: list[dict[str, Any]],
) -> tuple[bool, int, int, LiquidityRecord | None]:
    """
    Create analysis plot from cleaned data.

    Returns:
        (plot_saved, cleaned_count, raw_valid_count, liquidity_record_or_none)
    """
    raw_pairs = extract_price_mileage_pairs(ads)
    cleaned_pairs = trim_pairs_by_price_percentile(raw_pairs)
    cleaned_count = len(cleaned_pairs)

    if cleaned_count < MIN_ADS_FOR_PLOT:
        return False, cleaned_count, len(raw_pairs), None

    prices_arr = np.array([price for price, _ in cleaned_pairs], dtype=float)
    mileages_arr = np.array([km for _, km in cleaned_pairs], dtype=float)
    mean_price = float(np.mean(prices_arr))
    var_price = float(np.var(prices_arr))

    sns.set_style("whitegrid")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    sns.histplot(prices_arr, kde=True, ax=ax1, color="steelblue", edgecolor="white")
    ax1.axvline(mean_price, color="red", linestyle="--", label=f"Mean: {mean_price:,.0f}")
    ax1.set_title("Price Distribution (Trimmed P5-P95)")
    ax1.set_xlabel("Price (Toman)")
    ax1.set_ylabel("Count")
    ax1.legend()
    ax1.text(
        0.05,
        0.95,
        f"Variance: {var_price:,.0f}",
        transform=ax1.transAxes,
        fontsize=9,
        verticalalignment="top",
        bbox=dict(boxstyle="round", alpha=0.5),
    )

    sns.scatterplot(x=mileages_arr, y=prices_arr, ax=ax2, color="darkorange", alpha=0.7)
    ax2.set_title("Mileage vs Price (Trimmed P5-P95)")
    ax2.set_xlabel("Mileage (km)")
    ax2.set_ylabel("Price (Toman)")
    if cleaned_count > 5:
        sns.regplot(
            x=mileages_arr,
            y=prices_arr,
            ax=ax2,
            scatter=False,
            color="black",
            line_kws={"linestyle": ":"},
        )

    brand, model, variant = get_leaf_identity(leaf_dir)
    fig.suptitle(f"{brand} › {model} › {variant}", fontsize=14, fontweight="bold")
    plt.tight_layout()
    plt.savefig(leaf_dir / "analysis.png", dpi=150)
    plt.close(fig)

    record = compute_liquidity_record(brand, model, variant, cleaned_pairs)
    return True, cleaned_count, len(raw_pairs), record


def write_liquidity_report(records: list[LiquidityRecord], report_path: Path) -> None:
    """Write sorted global liquidity ranking report to disk."""
    sorted_records = sorted(records, key=lambda row: row.liquidity_score, reverse=True)

    with open(report_path, "w", encoding="utf-8") as file:
        file.write("GLOBAL LIQUIDITY RANKING REPORT\n")
        file.write("=" * 112 + "\n")
        file.write(
            f"{'Rank':>4}  {'Car Name':<62} {'Volume':>8} {'Mean Price':>16} {'CV':>10} {'Liquidity':>10}\n"
        )
        file.write("-" * 112 + "\n")

        for idx, record in enumerate(sorted_records, start=1):
            file.write(
                f"{idx:>4}  "
                f"{record.car_name[:62]:<62} "
                f"{record.volume:>8} "
                f"{record.mean_price:>16,.0f} "
                f"{record.price_cv:>10.4f} "
                f"{record.liquidity_score:>10.4f}\n"
            )


def main() -> None:
    print("🔍 Starting analysis...", flush=True)
    if not ADS_ROOT.exists():
        print(f"❌ 'BAMA ADS' folder not found at {ADS_ROOT}", flush=True)
        return

    leaf_dirs = [path.parent for path in ADS_ROOT.rglob("ads.json")]
    total = len(leaf_dirs)
    print(f"📊 Found {total} leaf folders in BAMA ADS.", flush=True)
    if total == 0:
        print("⚠️ No ads.json files found – nothing to process.", flush=True)
        return

    processed = 0
    skipped = 0
    liquidity_records: list[LiquidityRecord] = []

    for idx, leaf_dir in enumerate(leaf_dirs, start=1):
        rel_path = leaf_dir.relative_to(ADS_ROOT)
        print(f"🔄 Processing ({idx}/{total}): {rel_path}  ", end="", flush=True)

        ads_file = leaf_dir / "ads.json"
        try:
            with open(ads_file, "r", encoding="utf-8") as file:
                ads = json.load(file)

            done, cleaned_count, raw_valid_count, record = create_analysis_plot(leaf_dir, ads)
            if done and record:
                liquidity_records.append(record)
                print(
                    f"✅ plot saved (raw={raw_valid_count}, cleaned={cleaned_count}, score={record.liquidity_score:.3f})",
                    flush=True,
                )
                processed += 1
            else:
                print(
                    f"⏭️  skipped (cleaned={cleaned_count}, raw={raw_valid_count})",
                    flush=True,
                )
                skipped += 1
        except Exception as exc:
            print(f"❌ error: {exc}", flush=True)

    write_liquidity_report(liquidity_records, RANKING_REPORT_PATH)
    print(
        f"🧾 Liquidity ranking report created: {RANKING_REPORT_PATH}",
        flush=True,
    )
    print(
        f"🏁 Finished. Plots created: {processed}, skipped: {skipped}, ranked: {len(liquidity_records)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
