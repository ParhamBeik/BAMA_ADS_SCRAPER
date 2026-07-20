#!/usr/bin/env python3
"""Fetch Bama ads into the single ``bama.db`` store and run the auto-pipeline.

Each run records a fetch snapshot: every sighted ad is upserted into the ``ads``
table (current state + dimensions) and appended to the history tables. After a
complete run the pipeline marks vanished ads inactive, runs DB integrity checks,
and refreshes per-category analysis stats -- turning continuous runs into a
snapshot-grabbing machine for the whole Bama inventory.
"""

from __future__ import annotations

import datetime
import json
import os
import re
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
from paths import BRAND_ALIASES_PATH, TIME_DICT_PATH, UNKNOWN_TIMES_LOG
from history import finish_run, project_lock, record_observation, start_run
from store import open_store, upsert_ad, mark_inactive, counts
from analyze import parse_price, parse_mileage, parse_year, normalize_transmission


SEARCH_URL = "https://bama.ir/cad/api/search"
WARMUP_URL = "https://bama.ir/car?image=1&priced=1"
MAX_ADS = int(os.getenv("BAMA_MAX_ADS", "50000"))
PAGE_PAUSE = float(os.getenv("BAMA_PAGE_PAUSE", "0.8"))
REQUEST_TIMEOUT = int(os.getenv("BAMA_REQUEST_TIMEOUT", "20"))
MAX_RETRIES = int(os.getenv("BAMA_MAX_RETRIES", "4"))
RETRY_BACKOFF = float(os.getenv("BAMA_RETRY_BACKOFF", "2.0"))
# Stop after this many consecutive pages that add no previously-unseen codes.
# bama.ir keeps returning already-seen ads on pages past the real inventory, so
# without this the run churns forever until the server throttles it.
MAX_STALE_PAGES = int(os.getenv("BAMA_MAX_STALE_PAGES", "3"))
RETRIABLE_STATUS = frozenset({429, 500, 502, 503, 504})
BATCH_SIZE = 200
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

console = Console()


# ---------------------------------------------------------------------------
# Name cleanup and dimension derivation
# ---------------------------------------------------------------------------

def clean_name(name: Any) -> str:
    """Return a safe, readable label preserving Persian text."""
    text = str(name or "")
    if not text:
        return "unknown"
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


def derive_dims(ad: dict[str, Any], brand_aliases: dict[str, str] | None = None) -> dict[str, str]:
    """Derive brand/model/variant/category grouping columns from an ad payload.

    Replaces the old folder-routing scheme: the same values that used to become
    directory names are now denormalized columns on the ``ads`` row.
    """
    brand_aliases = brand_aliases or {}
    detail = ad.get("detail") if isinstance(ad.get("detail"), dict) else {}
    title = str(detail.get("title") or "")
    title_parts = title.split("،", 1)
    brand = clean_name(title_parts[0] if title_parts else "unknown")
    brand = brand_aliases.get(brand, brand)
    model = clean_name(title_parts[1] if len(title_parts) > 1 else "unknown")
    variant = clean_name(detail.get("trim") or "default")

    category = "آگهی ها"
    if "حواله" in title or "حواله" in variant:
        category = "حواله"
    elif "پیش فروش" in title or "پیش فروش" in variant:
        category = "پیش فروش"

    return {"brand": brand, "model": model, "variant": variant, "category": clean_name(category)}


def pure_ad(ad: dict[str, Any]) -> dict[str, Any]:
    """Keep stored payloads equal to Bama's, not scraper bookkeeping."""
    return {key: value for key, value in ad.items() if key not in FORBIDDEN_AD_KEYS}


def ad_columns(
    ad: dict[str, Any],
    brand_aliases: dict[str, str],
    publish_date_jalali: str | None,
    fetch_time_ts: float,
) -> dict[str, Any]:
    """Flatten one ad payload into the ``ads`` table column set."""
    detail = ad.get("detail") if isinstance(ad.get("detail"), dict) else {}
    price_obj = ad.get("price") if isinstance(ad.get("price"), dict) else {}
    dims = derive_dims(ad, brand_aliases)
    return {
        **dims,
        "year": parse_year(detail.get("year")),
        "mileage": parse_mileage(detail.get("mileage")),
        "price": parse_price(price_obj.get("price")),
        "price_type": price_obj.get("type"),
        "transmission": normalize_transmission(detail.get("gear") or detail.get("transmission")),
        "publish_date_jalali": publish_date_jalali,
        "fetch_time_ts": fetch_time_ts,
        "raw_payload": pure_ad(ad),
    }


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


def compute_publish_date(
    relative_time: str,
    fetch_time_ts: float,
    time_dict: dict[str, dict[str, int] | None],
) -> str | None:
    observed_at = datetime.datetime.fromtimestamp(fetch_time_ts, tz=timezone.utc)
    moment = parse_bama_time(relative_time, observed_at, time_dict, UNKNOWN_TIMES_LOG)
    return to_jalali_string(moment)


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
    """Fetch one page, retrying transient network/server errors with backoff."""
    url = f"{SEARCH_URL}?pageIndex={page}"
    delay = RETRY_BACKOFF
    for attempt in range(MAX_RETRIES):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
        except requests.HTTPError as error:
            # Fail fast on hard client errors; back off only on rate-limit/server errors.
            if error.response is None or error.response.status_code not in RETRIABLE_STATUS:
                raise
            retry_error: Exception = error
        except (requests.Timeout, requests.ConnectionError, ValueError) as error:
            retry_error = error
        else:
            ads = data.get("data", {}).get("ads", [])
            return [ad for ad in ads if isinstance(ad, dict) and ad.get("type") != "banner"]
        if attempt + 1 >= MAX_RETRIES:
            raise retry_error
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("fetch_page exhausted retries without returning")  # pragma: no cover


def iter_ads(
    session: requests.Session,
    max_ads: int = MAX_ADS,
    page_pause: float = PAGE_PAUSE,
) -> Iterator[tuple[dict[str, Any], int]]:
    """Yield non-banner ads page by page until the limit or feed end.

    Codes already yielded this run are skipped, so wrapped/repeated ads on deep
    pages aren't re-counted; when ``MAX_STALE_PAGES`` consecutive pages add no
    new code the feed is treated as exhausted and iteration stops.
    """
    page = 1
    yielded = 0
    seen: set[str] = set()
    stale_pages = 0
    while yielded < max_ads:
        ads = fetch_page(session, page)
        if not ads:
            break
        fresh_on_page = False
        for ad in ads:
            detail = ad.get("detail") if isinstance(ad.get("detail"), dict) else {}
            code = detail.get("code")
            code_s = str(code) if code else ""
            if code_s and code_s in seen:
                continue  # already yielded this run; the feed has wrapped
            if code_s:
                seen.add(code_s)
                fresh_on_page = True
            if yielded >= max_ads:
                break
            yield ad, page
            yielded += 1
        if fresh_on_page:
            stale_pages = 0
        else:
            stale_pages += 1
            if stale_pages >= MAX_STALE_PAGES:
                break
        page += 1
        time.sleep(page_pause)


# ---------------------------------------------------------------------------
# DB writer
# ---------------------------------------------------------------------------

class AdWriter:
    """Buffer ads and flush them to the ``ads`` table plus append-only history."""

    def __init__(
        self,
        conn,
        run_id: int,
        time_dict: dict[str, dict[str, int] | None],
        brand_aliases: dict[str, str],
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self.conn = conn
        self.run_id = run_id
        self.time_dict = time_dict
        self.brand_aliases = brand_aliases
        self.batch_size = batch_size
        self.buffer: dict[str, tuple[dict[str, Any], float]] = {}
        self.total_new = 0
        self.total_updated = 0
        self.total_versions_created = 0
        self.total_events_created = 0

    def buffer_ad(self, ad: dict[str, Any]) -> None:
        detail = ad.get("detail") if isinstance(ad.get("detail"), dict) else {}
        code = detail.get("code")
        if not code:
            return
        self.buffer[str(code)] = (ad, time.time())
        if len(self.buffer) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self.buffer:
            return
        batch_new = 0
        for code, (ad, fetch_time_ts) in self.buffer.items():
            detail = ad.get("detail") if isinstance(ad.get("detail"), dict) else {}
            publish = compute_publish_date(str(detail.get("time") or ""), fetch_time_ts, self.time_dict)
            fields = ad_columns(ad, self.brand_aliases, publish, fetch_time_ts)
            inserted = upsert_ad(self.conn, code, fields, fetch_time_ts)
            batch_new += int(inserted)
            # Canonical path = the dim grouping, so route_changed events still fire on regroup.
            canonical = "/".join((fields["category"], fields["brand"], fields["model"], fields["variant"]))
            version_created, event_created = record_observation(
                self.conn, self.run_id, code, fields["raw_payload"], fetch_time_ts, canonical, "live_fetch",
            )
            self.total_versions_created += int(version_created)
            self.total_events_created += int(event_created)
        self.conn.commit()
        self.total_new += batch_new
        self.total_updated += len(self.buffer) - batch_new
        console.print(f"[green]Batch saved[/] new={batch_new:,} updated={len(self.buffer) - batch_new:,}")
        self.buffer.clear()


# ---------------------------------------------------------------------------
# Auto-pipeline (runs after every complete fetch)
# ---------------------------------------------------------------------------

def run_pipeline(conn, run_started_ts: float) -> dict[str, int]:
    """Mark vanished ads inactive, run integrity checks, refresh analysis stats.

    Each step is isolated so a failure logs but does not lose the fetch snapshot.
    """
    import audit
    import analyze

    summary: dict[str, int] = {}
    summary["marked_inactive"] = mark_inactive(conn, run_started_ts)
    conn.commit()
    for name, fn in (("audit", lambda: audit.run_checks(conn)), ("analyze", lambda: analyze.compute_all(conn))):
        try:
            fn()
            conn.commit()
            summary[f"{name}_ran"] = 1
        except Exception as error:  # keep the fetch even if a downstream step fails
            console.print(f"[yellow]{name} step failed: {error}[/yellow]")
            summary[f"{name}_ran"] = 0
    return summary


# ---------------------------------------------------------------------------
# CLI workflow
# ---------------------------------------------------------------------------

def fetch_ads() -> None:
    console.print(Panel.fit("[bold blue]Bama Ads Fetcher Started[/bold blue]"))
    time_dict = load_time_dictionary(TIME_DICT_PATH)
    brand_aliases = load_brand_aliases()
    with project_lock(exclusive=True):
        conn = open_store()
        run_started_ts = time.time()
        run_id = start_run(conn, "live_fetch", MAX_ADS)
        writer = AdWriter(conn, run_id, time_dict, brand_aliases)
        session = create_session()
        warmup(session)

        total_fetched = 0
        page_count = 0
        reached_end = False
        interrupted = False
        error_message = None
        pipeline_summary: dict[str, int] = {}

        try:
            try:
                with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
                    task = progress.add_task("Fetching pages...", total=MAX_ADS)
                    for ad, page in iter_ads(session, MAX_ADS):
                        page_count = max(page_count, page)
                        writer.buffer_ad(ad)
                        total_fetched += 1
                        progress.update(task, advance=1, description=f"Fetching... {total_fetched:,}")
                    reached_end = total_fetched < MAX_ADS
            except KeyboardInterrupt:
                interrupted = True
                console.print("\n[yellow]Interrupted; flushing buffered ads before exit...[/yellow]")

            writer.flush()
        except Exception as error:
            error_message = str(error)
            finish_run(conn, run_id, "failed", total_fetched, page_count, reached_end, error_message)
            conn.close()
            raise

        finish_run(conn, run_id, "interrupted" if interrupted else "completed",
                   total_fetched, page_count, reached_end)
        # Only a complete run knows which ads truly vanished; skip inactivation on interrupt.
        if not interrupted:
            pipeline_summary = run_pipeline(conn, run_started_ts)
        totals = counts(conn)
        conn.close()

    console.print(Panel.fit(
        f"{'Fetch interrupted safely' if interrupted else 'Fetch completed'}\n"
        f"fetched={total_fetched:,} new={writer.total_new:,} updated={writer.total_updated:,}\n"
        f"history_versions_created={writer.total_versions_created:,} history_events_created={writer.total_events_created:,}\n"
        f"ads_active={totals['ads_active']:,} ads_removed={totals['ads_removed']:,}\n"
        f"pipeline={pipeline_summary}",
        title="Summary",
    ))


if __name__ == "__main__":
    fetch_ads()
