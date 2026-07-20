#!/usr/bin/env python3
"""One-shot migration of the legacy JSON tree + code_map.db + history.db into bama.db.

Run once to seed ``data/bama.db`` from the old stores. Idempotent: the history
copy uses ``INSERT OR IGNORE`` on the unique keys, and ``upsert_ad`` refreshes
rows in place, so re-running does not duplicate data.

    python src/migrate_to_db.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import ADS_ROOT, BAMA_DB_PATH, CODE_MAP_PATH, HISTORY_DB_PATH
from store import open_store, upsert_ad, counts
from fetch import load_brand_aliases, ad_columns

_HISTORY_TABLES = ("fetch_runs", "ad_versions", "ad_observations", "change_events")


def copy_history(conn: sqlite3.Connection) -> dict[str, int]:
    """Copy the 4 history tables verbatim from the legacy history.db."""
    if not HISTORY_DB_PATH.exists():
        return {}
    conn.execute("ATTACH DATABASE ? AS legacy;", (str(HISTORY_DB_PATH),))
    copied: dict[str, int] = {}
    try:
        for table in _HISTORY_TABLES:
            cols = [r[1] for r in conn.execute(f"PRAGMA legacy.table_info({table});").fetchall()]
            if not cols:
                continue
            col_list = ", ".join(cols)
            conn.execute(
                f"INSERT OR IGNORE INTO {table} ({col_list}) SELECT {col_list} FROM legacy.{table};"
            )
            copied[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table};").fetchone()[0])
        conn.commit()
    finally:
        conn.execute("DETACH DATABASE legacy;")
    return copied


def _code_map() -> dict[str, sqlite3.Row]:
    """code -> row from the legacy routing DB (publish date + timestamps)."""
    if not CODE_MAP_PATH.exists():
        return {}
    src = sqlite3.connect(str(CODE_MAP_PATH))
    src.row_factory = sqlite3.Row
    try:
        return {row["code"]: row for row in src.execute("SELECT * FROM ad_index;")}
    finally:
        src.close()


def _latest_payloads() -> dict[str, dict]:
    """Walk the JSON tree, keeping one payload per code (last file wins)."""
    payloads: dict[str, dict] = {}
    if not ADS_ROOT.exists():
        return payloads
    for path in sorted(ADS_ROOT.rglob("ads.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
            code = detail.get("code")
            if code:
                payloads[str(code)] = item
    return payloads


def migrate_ads(conn: sqlite3.Connection) -> int:
    """Upsert every JSON ad into the ads table using code_map timestamps/dates."""
    brand_aliases = load_brand_aliases()
    code_map = _code_map()
    payloads = _latest_payloads()
    migrated = 0
    for code, ad in payloads.items():
        row = code_map.get(code)
        publish = row["publish_date_jalali"] if row else None
        ts_candidates = [row["fetch_time_ts"], row["last_seen_ts"], row["first_seen_ts"]] if row else []
        fetch_ts = float(next((t for t in ts_candidates if t), 0.0))
        fields = ad_columns(ad, brand_aliases, publish, fetch_ts)
        upsert_ad(conn, code, fields, fetch_ts)
        migrated += 1
    conn.commit()
    return migrated


def main() -> None:
    conn = open_store(BAMA_DB_PATH)
    hist = copy_history(conn)
    ads = migrate_ads(conn)
    print(f"Migrated {ads:,} ads into {BAMA_DB_PATH}", flush=True)
    print(f"History copied: {hist}", flush=True)
    print(f"Store counts: {counts(conn)}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
