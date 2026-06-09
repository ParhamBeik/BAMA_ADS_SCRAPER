#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
import random
from pathlib import Path
from typing import Any, Dict, List, Set

import requests

# ===================== CONFIGURATION =====================
MAX_ADS = 50_000
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT = SCRIPT_DIR / "BAMA ADS"
SAVE_EVERY = 50
ILLEGAL_FS_CHARS = '<>:"/\\|?*'

# --- API layer ---
SEARCH_URL = "https://bama.ir/cad/api/search"
WARMUP_URL = "https://bama.ir/car?image=1&priced=1"

MAX_RETRIES = 5
BACKOFF_BASE = 1.5      # ثانیه
PAGE_PAUSE = 0.8        # مکث بین صفحات
REQUEST_TIMEOUT = 20

# مقدار واقعی Cookie را از متغیر محیطی بخوان تا وارد VC نشود.
COOKIE = os.environ.get("BAMA_COOKIE", "")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa,en;q=0.9",
    "Referer": "https://bama.ir/car",
    "X-Requested-With": "XMLHttpRequest",
}
# =========================================================


class BamaScraper:
    def __init__(self) -> None:
        self.session: requests.Session | None = None
        self.output_root = OUTPUT_ROOT
        self.output_root.mkdir(parents=True, exist_ok=True)

        # Session dedupe state
        self.seen_codes: Set[str] = set()

        # Buffered writes grouped by leaf ads.json path
        self.pending_by_path: Dict[Path, List[Dict[str, Any]]] = {}
        self._unsaved = 0

        # Running total of newly saved ads this session
        self.total_saved = 0

        self._load_existing_seen_codes()

    # ----------------- path/category helpers -----------------
    @staticmethod
    def clean_name(name: Any) -> str:
        """Sanitize text into a filesystem-safe folder name."""
        value = str(name or "").strip()
        for char in ILLEGAL_FS_CHARS:
            value = value.replace(char, "_")
        value = " ".join(value.split())
        return value or "unknown"

    @staticmethod
    def get_category(ad: Dict[str, Any]) -> str:
        """Determine category: حواله / پیش فروش / آگهی ها."""
        detail = ad.get("detail", {})
        title = str(detail.get("title", "")).lower()
        trim = str(detail.get("trim", "")).lower()

        if "حواله" in title or "حواله" in trim:
            return "حواله"
        if "پیش فروش" in title or "پیش فروش" in trim:
            return "پیش فروش"
        return "آگهی ها"

    def route_path_for_ad(self, ad: Dict[str, Any]) -> Path:
        """Build leaf ads.json path: BAMA ADS/{category}/{brand}/{model}/{variant}/ads.json"""
        detail = ad.get("detail", {})
        title = str(detail.get("title", ""))
        title_parts = title.split("،", 1)
        brand = self.clean_name(title_parts[0] if title_parts else "unknown")
        model = self.clean_name(title_parts[1] if len(title_parts) > 1 else "unknown")
        variant = self.clean_name(detail.get("trim") or "default")
        category = self.clean_name(self.get_category(ad))

        leaf_dir = self.output_root / category / brand / model / variant
        return leaf_dir / "ads.json"

    # ----------------- startup indexing -----------------
    def _load_existing_seen_codes(self) -> None:
        """
        Index existing distributed files to preserve dedupe across restarts.
        """
        ads_files = list(self.output_root.rglob("ads.json"))
        if not ads_files:
            print("📁 No existing distributed dataset found.")
            return

        loaded_codes = 0
        broken_files = 0
        for ads_file in ads_files:
            try:
                with open(ads_file, "r", encoding="utf-8") as file:
                    payload = json.load(file)
                if not isinstance(payload, list):
                    continue
                for ad in payload:
                    if not isinstance(ad, dict):
                        continue
                    code = ad.get("detail", {}).get("code")
                    if code:
                        self.seen_codes.add(code)
                        loaded_codes += 1
            except Exception:
                broken_files += 1
                continue

        print(
            f"📁 Indexed {len(ads_files)} files, loaded {loaded_codes} codes "
            f"({len(self.seen_codes)} unique)."
        )
        if broken_files:
            print(f"⚠️ Skipped {broken_files} unreadable ads.json files during indexing.")

    # ----------------- file I/O -----------------
    def _append_ads_to_leaf_file(self, target_file: Path, new_ads: List[Dict[str, Any]]) -> None:
        """
        Safe read-append-write for one leaf ads.json file.
        Writes atomically via temporary file + replace.
        """
        target_file.parent.mkdir(parents=True, exist_ok=True)
        existing: List[Dict[str, Any]] = []

        if target_file.exists():
            try:
                with open(target_file, "r", encoding="utf-8") as file:
                    payload = json.load(file)
                if isinstance(payload, list):
                    existing = payload
            except Exception:
                print(f"⚠️ Corrupt/unreadable file, recreating: {target_file}")
                existing = []

        merged = existing + new_ads

        tmp_file = target_file.with_suffix(".json.tmp")
        with open(tmp_file, "w", encoding="utf-8") as file:
            json.dump(merged, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        tmp_file.replace(target_file)

    def _flush_pending(self, force: bool = False) -> None:
        """Flush buffered ads to distributed leaf files."""
        if not force and self._unsaved < SAVE_EVERY:
            return
        if not self.pending_by_path:
            return

        flushed_ads = 0
        flushed_files = 0
        errors = 0
        for target_path, ads_batch in list(self.pending_by_path.items()):
            if not ads_batch:
                continue
            try:
                self._append_ads_to_leaf_file(target_path, ads_batch)
                flushed_ads += len(ads_batch)
                flushed_files += 1
            except Exception as exc:
                errors += 1
                print(f"❌ Failed writing {target_path}: {exc}")

        self.pending_by_path.clear()
        self._unsaved = 0
        print(f"💾 Flushed {flushed_ads} ads into {flushed_files} files.")
        if errors:
            print(f"⚠️ Write errors on {errors} files.")

    # ----------------- fetch layer -----------------
    def _build_session(self) -> None:
        """جایگزین _build_driver: ساخت سشن با هدرها و گرم‌کردن کوکی."""
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        if COOKIE:
            self.session.headers["Cookie"] = COOKIE

        # warm-up: گرفتن کوکی‌های اولیه از صفحه‌ی لیست
        try:
            self.session.get(WARMUP_URL, timeout=REQUEST_TIMEOUT)
        except requests.RequestException:
            # گرم‌کردن اختیاری است؛ اگر شکست خورد با همان Cookie ادامه می‌دهیم
            pass

    def _fetch_page(self, page_index: int) -> List[Dict[str, Any]]:
        """جایگزین _get_responses: گرفتن یک صفحه از API با retry نمایی."""
        params = {"pageIndex": page_index}

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(
                    SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise requests.RequestException(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                payload = resp.json()
            except (requests.RequestException, ValueError) as exc:
                if attempt == MAX_RETRIES:
                    print(f"[page {page_index}] failed after {attempt} tries: {exc}")
                    return []
                sleep_for = BACKOFF_BASE ** attempt + random.uniform(0, 0.5)
                print(f"[page {page_index}] retry {attempt} in {sleep_for:.1f}s ({exc})")
                time.sleep(sleep_for)
                continue

            # همان فیلتری که _get_responses داشت
            if not payload.get("status") or not payload.get("data"):
                return []
            ads = payload["data"].get("ads", [])
            return [item for item in ads if item.get("type") == "ad"]

        return []

    # ----------------- processing loop -----------------
    def _process_ads(self, items: List[Dict[str, Any]]) -> int:
        """
        Deduplicate by detail.code and route accepted ads to per-variant buffers.
        """
        added = 0
        for item in items:
            code = item.get("detail", {}).get("code")
            if not code:
                continue
            if code in self.seen_codes:
                continue

            self.seen_codes.add(code)
            target = self.route_path_for_ad(item)
            self.pending_by_path.setdefault(target, []).append(item)
            self._unsaved += 1
            added += 1
        return added

    def run(self) -> None:
        self._build_session()

        page_index = 1
        new_since_flush = 0

        try:
            while self.total_saved < MAX_ADS:
                ads = self._fetch_page(page_index)

                # شرط توقف: لیست خالی یعنی پایان صفحات
                if not ads:
                    print(f"[page {page_index}] empty -> stop")
                    break

                added = self._process_ads(ads)
                new_since_flush += added
                self.total_saved += added

                print(
                    f"[page {page_index}] fetched={len(ads)} "
                    f"new={added} total={self.total_saved}"
                )

                if new_since_flush >= SAVE_EVERY:
                    self._flush_pending()
                    new_since_flush = 0

                page_index += 1
                time.sleep(PAGE_PAUSE)
        finally:
            self._flush_pending(force=True)
            if self.session is not None:
                self.session.close()


if __name__ == "__main__":
    scraper = BamaScraper()
    scraper.run()
