#!/usr/bin/env python3
"""Fetch Bama ads into pure JSON payload files and maintain ``code_map.db``."""

from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
import time
import unicodedata
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import jdatetime
import requests
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import (
    ADS_ROOT,
    BRAND_ALIASES_PATH,
    CODE_MAP_PATH,
    HISTORY_DB_PATH,
    ROUTE_CONFLICTS_LOG,
    TIME_DICT_PATH,
    UNKNOWN_TIMES_LOG,
)
from history import finish_run, open_history, project_lock, record_observation, start_run


SEARCH_URL = "https://bama.ir/cad/api/search"
WARMUP_URL = "https://bama.ir/car?image=1&priced=1"
MAX_ADS = int(os.getenv("BAMA_MAX_ADS", "50000"))
PAGE_PAUSE = float(os.getenv("BAMA_PAGE_PAUSE", "0.8"))
REQUEST_TIMEOUT = int(os.getenv("BAMA_REQUEST_TIMEOUT", "20"))
BATCH_SIZE = 200
OUTPUT_ROOT = ADS_ROOT
ILLEGAL_FS_CHARS = '<>:"/\\|?*'
FORBIDDEN_AD_KEYS = ("computed_publish_date_jalali", "fetch_time_ts")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa,en;q=0.9",
    "Referer": "https://bama.ir/car",
    "X-Requested-With": "XMLHttpRequest",
}
if os.getenv("BAMA_COOKIE", ""):
    HEADERS["Cookie"] = os.getenv("BAMA_COOKIE", "")

JALALI_DATE_PATTERN = re.compile(r"(?P<year>1[34]\d{2})/(?P<month>\d{1,2})/(?P<day>\d{1,2})")
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
_TIMEDELTA_KEYS = {"days", "seconds", "microseconds", "milliseconds", "minutes", "hours", "weeks"}
_logged_unknown_times: set[str] = set()
_ARABIC_TO_PERSIAN = str.maketrans({
    "ي": "ی",
    "ى": "ی",
    "ك": "ک",
    "ة": "ه",
})

SCHEMA = """
CREATE TABLE IF NOT EXISTS ad_index (
    code                TEXT PRIMARY KEY,
    file_path           TEXT NOT NULL,
    first_seen_ts       REAL,
    last_seen_ts        REAL,
    publish_date_jalali TEXT,
    fetch_time_ts       REAL
);
CREATE INDEX IF NOT EXISTS idx_ad_index_file_path ON ad_index(file_path);
"""

console = Console()


# ---------------------------------------------------------------------------
# Name cleanup and path routing
# ---------------------------------------------------------------------------

def clean_name(name: Any) -> str:
    """Return a safe folder name while preserving readable Persian text."""
    text = str(name or "")
    if not text:
        return "unknown"
    for char in ILLEGAL_FS_CHARS:
        text = text.replace(char, "_")
    text = text.translate(_ARABIC_TO_PERSIAN)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    cleaned = " ".join(text.split())
    return cleaned or "unknown"


def load_brand_aliases(path: Path = BRAND_ALIASES_PATH) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def intended_path_for_ad(
    ad: dict[str, Any],
    output_root: Path = OUTPUT_ROOT,
    brand_aliases: dict[str, str] | None = None,
) -> Path:
    """Build the one canonical file path for an ad from its current payload."""
    brand_aliases = brand_aliases or {}
    detail = ad.get("detail") if isinstance(ad.get("detail"), dict) else {}
    title = str(detail.get("title") or "")
    title_parts = title.split("،", 1)
    brand = clean_name(title_parts[0] if title_parts else "unknown")
    brand = brand_aliases.get(brand, brand)
    model = clean_name(title_parts[1] if len(title_parts) > 1 else "unknown")
    variant = clean_name(detail.get("trim") or "default")

    category_text = "آگهی ها"
    if "حواله" in title or "حواله" in variant:
        category_text = "حواله"
    elif "پیش فروش" in title or "پیش فروش" in variant:
        category_text = "پیش فروش"

    return output_root / clean_name(category_text) / brand / model / variant / "ads.json"


def pure_ad(ad: dict[str, Any]) -> dict[str, Any]:
    """Keep ads.json equal to Bama payloads, not scraper bookkeeping."""
    return {key: value for key, value in ad.items() if key not in FORBIDDEN_AD_KEYS}


# ---------------------------------------------------------------------------
# code_map.db helpers
# ---------------------------------------------------------------------------

def open_db(path: Path = CODE_MAP_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def lookup(conn: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM ad_index WHERE code = ?;", (str(code),)).fetchone()


def upsert_map(
    conn: sqlite3.Connection,
    code: str,
    file_path: str,
    fetch_time_ts: float | None,
    publish_date_jalali: str | None,
) -> bool:
    """Insert/update routing metadata without overwriting known publish dates."""
    row = lookup(conn, code)
    if row is None:
        conn.execute(
            """
            INSERT INTO ad_index
                (code, file_path, first_seen_ts, last_seen_ts, publish_date_jalali, fetch_time_ts)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (str(code), file_path, fetch_time_ts, fetch_time_ts, publish_date_jalali, fetch_time_ts),
        )
        return publish_date_jalali is not None

    filled = row["publish_date_jalali"] is None and publish_date_jalali is not None
    conn.execute(
        """
        UPDATE ad_index
           SET file_path = ?,
               last_seen_ts = ?,
               fetch_time_ts = ?,
               publish_date_jalali = COALESCE(publish_date_jalali, ?)
         WHERE code = ?;
        """,
        (file_path, fetch_time_ts, fetch_time_ts, publish_date_jalali, str(code)),
    )
    return filled


def relocate_map(conn: sqlite3.Connection, code: str, new_path: str) -> None:
    conn.execute("UPDATE ad_index SET file_path = ? WHERE code = ?;", (new_path, str(code)))


def remaining_null_dates(conn: sqlite3.Connection) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) AS c FROM ad_index WHERE publish_date_jalali IS NULL;"
    ).fetchone()["c"])


# ---------------------------------------------------------------------------
# Publish-time parsing
# ---------------------------------------------------------------------------

def normalize_digits(value: str) -> str:
    return value.translate(PERSIAN_DIGITS)


def load_time_dictionary(path: str | Path) -> dict[str, dict[str, int] | None]:
    path = Path(path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def parse_absolute_jalali_date(value: str) -> datetime.datetime | None:
    text = normalize_digits(value.strip())
    match = JALALI_DATE_PATTERN.search(text)
    if not match:
        return None
    try:
        jalali_date = jdatetime.date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError:
        return None
    return datetime.datetime.combine(jalali_date.togregorian(), datetime.datetime.min.time(), tzinfo=timezone.utc)


def _log_unknown_time(value: str, unknown_log_path: str | Path | None) -> None:
    if not unknown_log_path or value in _logged_unknown_times:
        return
    _logged_unknown_times.add(value)
    try:
        path = Path(unknown_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(value + "\n")
    except OSError:
        pass


def parse_relative_time(
    value: str,
    observed_at: datetime.datetime,
    time_dictionary: dict[str, dict[str, int] | None],
) -> datetime.datetime | None:
    delta_config = time_dictionary.get(value.strip())
    if not delta_config:
        return None
    delta_kwargs = {
        key: int(amount) for key, amount in delta_config.items() if key in _TIMEDELTA_KEYS
    }
    return observed_at - timedelta(**delta_kwargs) if delta_kwargs else None


def parse_bama_time(
    value: str | None,
    observed_at: datetime.datetime | None = None,
    time_dictionary: dict[str, dict[str, int] | None] | None = None,
    unknown_log_path: str | Path | None = None,
) -> datetime.datetime | None:
    """Parse Bama's absolute Jalali dates or curated relative phrases."""
    if not value:
        return None
    observed_at = observed_at or datetime.datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    absolute_date = parse_absolute_jalali_date(value)
    if absolute_date:
        return absolute_date
    resolved = parse_relative_time(value, observed_at, time_dictionary or {})
    if resolved is None:
        _log_unknown_time(value.strip(), unknown_log_path)
    return resolved


def to_jalali_string(moment: datetime.datetime | None) -> str | None:
    if moment is None:
        return None
    naive = moment.replace(tzinfo=None)
    return jdatetime.datetime.fromgregorian(datetime=naive).strftime("%Y/%m/%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Bama HTTP fetching
# ---------------------------------------------------------------------------

def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def warmup(session: requests.Session) -> None:
    try:
        session.get(WARMUP_URL, timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        pass


def fetch_page(session: requests.Session, page: int) -> list[dict[str, Any]]:
    response = session.get(f"{SEARCH_URL}?pageIndex={page}", timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    ads = data.get("data", {}).get("ads", [])
    return [ad for ad in ads if isinstance(ad, dict) and ad.get("type") != "banner"]


def iter_ads(
    session: requests.Session,
    max_ads: int = MAX_ADS,
    page_pause: float = PAGE_PAUSE,
) -> Iterator[tuple[dict[str, Any], int]]:
    """Yield non-banner ads page by page until the limit or feed end."""
    page = 1
    yielded = 0
    while yielded < max_ads:
        ads = fetch_page(session, page)
        if not ads:
            break
        for ad in ads:
            if yielded >= max_ads:
                break
            yield ad, page
            yielded += 1
        page += 1
        time.sleep(page_pause)


# ---------------------------------------------------------------------------
# File buffering and self-healing relocation
# ---------------------------------------------------------------------------

def relocate_code_to_intended(
    conn: sqlite3.Connection,
    output_root: Path,
    code: str,
    intended: Path,
) -> str | None:
    """Move a mapped code away from its old file when the payload route changes."""
    intended_rel = intended.relative_to(output_root).as_posix()
    row = lookup(conn, code)
    if row is None or row["file_path"] == intended_rel:
        return None

    old_rel = row["file_path"]
    old_abs = output_root / old_rel
    try:
        data = json.loads(old_abs.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = []
    if not isinstance(data, list):
        data = []

    kept = [
        item for item in data
        if not (isinstance(item, dict) and (item.get("detail") or {}).get("code") == code)
    ]
    old_abs.parent.mkdir(parents=True, exist_ok=True)
    old_abs.write_text(json.dumps(kept, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    relocate_map(conn, code, intended_rel)
    return old_rel


class AdFileManager:
    """Batch ads by destination file and keep JSON, code_map, and history in sync."""

    def __init__(
        self,
        output_root: Path,
        time_dict: dict[str, dict[str, int] | None],
        batch_size: int,
        conn: sqlite3.Connection,
        history_conn: sqlite3.Connection,
        history_run_id: int,
        brand_aliases: dict[str, str] | None = None,
    ) -> None:
        self.output_root = output_root
        self.time_dict = time_dict
        self.batch_size = batch_size
        self.conn = conn
        self.history_conn = history_conn
        self.history_run_id = history_run_id
        self.brand_aliases = brand_aliases or {}
        self.buffer: dict[Path, dict[str, tuple[dict[str, Any], float, str | None]]] = {}
        self.buffer_count = 0
        self.relocated_old_paths: set[str] = set()
        self.total_new = 0
        self.total_updated = 0
        self.total_relocated = 0
        self.total_publish_dates_filled = 0
        self.total_versions_created = 0
        self.total_events_created = 0

    def compute_publish_date(self, relative_time: str, fetch_time_ts: float) -> str | None:
        observed_at = datetime.datetime.fromtimestamp(fetch_time_ts, tz=timezone.utc)
        moment = parse_bama_time(relative_time, observed_at, self.time_dict, UNKNOWN_TIMES_LOG)
        return to_jalali_string(moment)

    def _log_route_conflict(self, code: str, intended: Path, old: Path) -> None:
        try:
            ROUTE_CONFLICTS_LOG.parent.mkdir(parents=True, exist_ok=True)
            with ROUTE_CONFLICTS_LOG.open("a", encoding="utf-8") as handle:
                handle.write(f"code {code} relocated {old} -> {intended}\n")
        except OSError:
            pass

    def buffer_ad(self, ad: dict[str, Any]) -> None:
        detail = ad.get("detail") if isinstance(ad.get("detail"), dict) else {}
        code = detail.get("code")
        if not code:
            return

        fetch_time_ts = time.time()
        publish_date_jalali = self.compute_publish_date(str(detail.get("time") or ""), fetch_time_ts)
        intended = intended_path_for_ad(ad, self.output_root, self.brand_aliases)
        # The current Bama payload decides the path. Old map rows are repaired here.
        old_rel = relocate_code_to_intended(self.conn, self.output_root, str(code), intended)
        if old_rel is not None:
            self._log_route_conflict(str(code), intended, self.output_root / old_rel)
            self.relocated_old_paths.add(old_rel)
            self.total_relocated += 1

        self.buffer.setdefault(intended, {})[str(code)] = (pure_ad(ad), fetch_time_ts, publish_date_jalali)
        self.buffer_count += 1
        if self.buffer_count >= self.batch_size:
            self.flush()

    def sweep_emptied_paths(self) -> tuple[int, int]:
        files_deleted = 0
        dirs_pruned = 0
        for rel in sorted(self.relocated_old_paths, key=lambda p: p.count("/"), reverse=True):
            file_path = self.output_root / rel
            if not file_path.exists():
                continue
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, list) and not data:
                file_path.unlink()
                files_deleted += 1
                parent = file_path.parent
                while parent != self.output_root:
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    dirs_pruned += 1
                    parent = parent.parent
        self.relocated_old_paths.clear()
        return files_deleted, dirs_pruned

    def flush(self) -> None:
        """Write each touched ads.json once, then commit both SQLite databases."""
        if not self.buffer:
            return

        batch_new = 0
        batch_updated = 0
        batch_filled = 0

        for file_path, ads_to_save in self.buffer.items():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            existing: dict[str, dict[str, Any]] = {}
            if file_path.exists():
                try:
                    data = json.loads(file_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = []
                if isinstance(data, list):
                    existing = {
                        str((item.get("detail") or {}).get("code")): item
                        for item in data
                        if isinstance(item, dict) and (item.get("detail") or {}).get("code")
                    }

            rel_path = file_path.relative_to(self.output_root).as_posix()
            for code, (ad, fetch_time_ts, publish_date_jalali) in ads_to_save.items():
                if code in existing:
                    batch_updated += 1
                else:
                    batch_new += 1
                existing[code] = ad
                if upsert_map(self.conn, code, rel_path, fetch_time_ts, publish_date_jalali):
                    batch_filled += 1
                # One observation per run/code; unchanged semantic payloads reuse versions.
                version_created, event_created = record_observation(
                    self.history_conn,
                    self.history_run_id,
                    code,
                    ad,
                    fetch_time_ts,
                    rel_path,
                    "live_fetch",
                )
                self.total_versions_created += int(version_created)
                self.total_events_created += int(event_created)

            file_path.write_text(
                json.dumps(list(existing.values()), ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )

        self.conn.commit()
        self.history_conn.commit()
        self.total_new += batch_new
        self.total_updated += batch_updated
        self.total_publish_dates_filled += batch_filled
        console.print(
            f"[green]Batch saved[/] new={batch_new:,} updated={batch_updated:,} "
            f"publish_dates_filled={batch_filled:,}"
        )
        self.buffer.clear()
        self.buffer_count = 0


# ---------------------------------------------------------------------------
# CLI workflow
# ---------------------------------------------------------------------------

def fetch_ads() -> None:
    console.print(Panel.fit("[bold blue]Bama Ads Fetcher Started[/bold blue]"))
    time_dict = load_time_dictionary(TIME_DICT_PATH)
    brand_aliases = load_brand_aliases()
    with project_lock(exclusive=True):
        conn = open_db(CODE_MAP_PATH)
        history_conn = open_history(HISTORY_DB_PATH)
        history_run_id = start_run(history_conn, "live_fetch", MAX_ADS)
        manager = AdFileManager(OUTPUT_ROOT, time_dict, BATCH_SIZE, conn, history_conn, history_run_id, brand_aliases)
        session = create_session()
        warmup(session)

        total_fetched = 0
        page_count = 0
        reached_end = False
        interrupted = False
        files_deleted = 0
        dirs_pruned = 0
        null_dates = 0
        error_message = None

        try:
            try:
                with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
                    task = progress.add_task("Fetching pages...", total=MAX_ADS)
                    for ad, page in iter_ads(session, MAX_ADS):
                        page_count = max(page_count, page)
                        manager.buffer_ad(ad)
                        total_fetched += 1
                        progress.update(task, advance=1, description=f"Fetching... {total_fetched:,}")
                    reached_end = total_fetched < MAX_ADS
            except KeyboardInterrupt:
                interrupted = True
                console.print("\n[yellow]Interrupted; flushing buffered ads before exit...[/yellow]")

            # Even interrupted runs save the final partial batch before closing.
            manager.flush()
            files_deleted, dirs_pruned = manager.sweep_emptied_paths()
            null_dates = remaining_null_dates(conn)
        except Exception as error:
            error_message = str(error)
            finish_run(history_conn, history_run_id, "failed", total_fetched, page_count, reached_end, error_message)
            raise
        finally:
            if error_message is None:
                finish_run(
                    history_conn,
                    history_run_id,
                    "interrupted" if interrupted else "completed",
                    total_fetched,
                    page_count,
                    reached_end,
                )
            conn.commit()
            history_conn.commit()
            conn.close()
            history_conn.close()

    console.print(Panel.fit(
        f"{'Fetch interrupted safely' if interrupted else 'Fetch completed'}\n"
        f"fetched={total_fetched:,} new={manager.total_new:,} updated={manager.total_updated:,}\n"
        f"relocated={manager.total_relocated:,} publish_dates_filled={manager.total_publish_dates_filled:,}\n"
        f"history_versions_created={manager.total_versions_created:,} history_events_created={manager.total_events_created:,}\n"
        f"remaining_null_dates={null_dates:,} emptied_files={files_deleted:,} pruned_dirs={dirs_pruned:,}",
        title="Summary",
    ))


if __name__ == "__main__":
    fetch_ads()
