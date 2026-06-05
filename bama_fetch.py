#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By

# ===================== CONFIGURATION =====================
MAX_ADS = 20_000
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT = SCRIPT_DIR / "BAMA ADS"
SAVE_EVERY = 50  # write every N newly accepted ads

# Timing (seconds)
INITIAL_WAIT = 4.0
SCROLL_PAUSE = 0.6
AFTER_BUTTON_WAIT = 8.0
MAX_STALE_SCROLLS = 6
MAX_NO_BUTTON_ATTEMPTS = 5

ILLEGAL_FS_CHARS = '<>:"/\\|?*'
# =========================================================


class BamaScraper:
    def __init__(self) -> None:
        self.driver: webdriver.Chrome | None = None
        self.output_root = OUTPUT_ROOT
        self.output_root.mkdir(parents=True, exist_ok=True)

        # Session dedupe state
        self.seen_codes: Set[str] = set()

        # Buffered writes grouped by leaf ads.json path
        self.pending_by_path: Dict[Path, List[Dict[str, Any]]] = {}
        self._unsaved = 0

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

    # ----------------- selenium/network capture -----------------
    def _get_driver(self) -> webdriver.Chrome:
        options = ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1920,1080")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        )
        try:
            driver = webdriver.Chrome(options=options)
            print("✅ ChromeDriver started via Selenium automatic detection.")
            return driver
        except Exception:
            print("⚠️ Automatic driver detection failed. Trying webdriver-manager ...")

        try:
            from webdriver_manager.chrome import ChromeDriverManager

            service = ChromeService(executable_path=ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            print("✅ ChromeDriver started via webdriver-manager.")
            return driver
        except Exception as exc:
            print(f"❌ Failed to start ChromeDriver: {exc}")
            raise

    def _build_driver(self) -> None:
        self.driver = self._get_driver()
        self.driver.set_page_load_timeout(20)

    def _inject_interceptor(self) -> None:
        if self.driver is None:
            raise RuntimeError("Driver is not initialized")

        script = """
        window.__bamaResponses = [];
        (function() {
            const originalFetch = window.fetch;
            window.fetch = async function(url, ...args) {
                const response = await originalFetch(url, ...args);
                if (typeof url === 'string' && url.includes('/cad/api/search')) {
                    try {
                        const clone = response.clone();
                        const payload = await clone.json();
                        window.__bamaResponses.push({url, json: payload});
                    } catch(e) {}
                }
                return response;
            };

            const originalOpen = XMLHttpRequest.prototype.open;
            const originalSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url) {
                this._url = url;
                return originalOpen.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function(body) {
                this.addEventListener('load', function() {
                    if (this._url && this._url.includes('/cad/api/search')) {
                        try {
                            const payload = JSON.parse(this.responseText);
                            window.__bamaResponses.push({url: this._url, json: payload});
                        } catch(e) {}
                    }
                });
                return originalSend.apply(this, arguments);
            };
        })();
        """
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": script})
        self.driver.execute_script(script)

    def _get_responses(self) -> List[Dict[str, Any]]:
        if self.driver is None:
            return []
        try:
            responses = self.driver.execute_script("return window.__bamaResponses.splice(0);")
            if not responses:
                return []

            ads: List[Dict[str, Any]] = []
            for entry in responses:
                payload = entry.get("json")
                if payload and payload.get("status") and payload.get("data"):
                    for item in payload["data"].get("ads", []):
                        if item.get("type") == "ad":
                            ads.append(item)
            return ads
        except Exception as exc:
            print(f"⚠️ Error collecting intercepted responses: {exc}")
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

    def _scroll_down(self) -> None:
        if self.driver is None:
            return
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(SCROLL_PAUSE)

    def _click_more_button(self) -> bool:
        if self.driver is None:
            return False
        try:
            button = self.driver.find_element(By.XPATH, "//button[contains(., 'بیشتر')]")
            if button.is_displayed() and button.is_enabled():
                self.driver.execute_script("arguments[0].scrollIntoView(true);", button)
                time.sleep(0.2)
                button.click()
                return True
        except NoSuchElementException:
            pass
        except Exception as exc:
            print(f"⚠️ Button click error: {exc}")
        return False

    def run(self) -> None:
        print("🚀 Launching headless Chrome ...")
        self._build_driver()
        assert self.driver is not None

        try:
            self.driver.get("https://bama.ir/car?image=1&priced=1")
            time.sleep(INITIAL_WAIT)
        except Exception as exc:
            print(f"❌ Failed to load page: {exc}")
            self._flush_pending(force=True)
            return

        self._inject_interceptor()
        total_unique = len(self.seen_codes)

        initial_ads = self._get_responses()
        added = self._process_ads(initial_ads)
        self._flush_pending(force=True)
        total_unique += added
        print(f"📥 Initial capture: +{added} ads (session total: {total_unique})")

        stale_scrolls = 0
        button_not_found = 0

        while total_unique < MAX_ADS:
            self._scroll_down()
            new_ads = self._get_responses()
            added = self._process_ads(new_ads)
            if added > 0:
                total_unique += added
                print(f"✅ +{added} ads (session total: {total_unique})")
                self._flush_pending(force=False)
                stale_scrolls = 0
            else:
                stale_scrolls += 1

            if stale_scrolls >= MAX_STALE_SCROLLS:
                print("🔍 Scrolling stalled, looking for 'بیشتر' ...")
                if self._click_more_button():
                    print("🖱️ Clicked 'بیشتر'. Waiting for new data ...")
                    time.sleep(AFTER_BUTTON_WAIT)
                    new_ads = self._get_responses()
                    added = self._process_ads(new_ads)
                    if added > 0:
                        total_unique += added
                        print(f"📥 +{added} ads after button (session total: {total_unique})")
                    self._flush_pending(force=True)
                    stale_scrolls = 0
                    button_not_found = 0
                else:
                    button_not_found += 1
                    print(f"🚫 Button not found ({button_not_found}/{MAX_NO_BUTTON_ATTEMPTS})")
                    if button_not_found >= MAX_NO_BUTTON_ATTEMPTS:
                        print("❌ No more pages available.")
                        break
                    stale_scrolls = 0

        self._flush_pending(force=True)
        print(f"🏁 Finished. Session unique ads accepted: {total_unique}")
        if self.driver is not None:
            self.driver.quit()


if __name__ == "__main__":
    scraper = BamaScraper()
    scraper.run()
