"""Single SQLite store for BAMA ads: current snapshot, append-only history, and
per-category analysis. Replaces the old JSON tree + ``code_map.db`` + ``history.db``.

The ``ads`` table is both the current-state fact and the code index (``code`` PK),
so there is no separate routing DB and no file tree. History tables are carried
over verbatim from the legacy ``history.db`` schema; ``analysis_stats`` replaces
the per-leaf ``analysis.png`` outputs.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from paths import BAMA_DB_PATH

SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS ads (
    code                TEXT PRIMARY KEY,
    brand               TEXT,
    model               TEXT,
    variant             TEXT,
    category            TEXT,
    year                INTEGER,
    mileage             REAL,
    price               REAL,
    price_type          TEXT,
    transmission        TEXT,
    publish_date_jalali TEXT,
    first_seen_ts       REAL,
    last_seen_ts        REAL,
    fetch_time_ts       REAL,
    status              TEXT NOT NULL DEFAULT 'active',
    removed_at          REAL,
    raw_payload         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ads_dims ON ads(category, brand, model, variant);
CREATE INDEX IF NOT EXISTS idx_ads_status ON ads(status);
CREATE INDEX IF NOT EXISTS idx_ads_price ON ads(model, price);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    status TEXT NOT NULL,
    started_ts REAL NOT NULL,
    finished_ts REAL,
    max_ads INTEGER,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER NOT NULL DEFAULT 0,
    reached_end INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE TABLE IF NOT EXISTS ad_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    semantic_hash TEXT NOT NULL,
    raw_hash TEXT NOT NULL,
    payload_zlib BLOB NOT NULL,
    origin TEXT NOT NULL,
    first_observed_ts REAL NOT NULL,
    UNIQUE(code, semantic_hash)
);
CREATE INDEX IF NOT EXISTS idx_versions_code ON ad_versions(code);
CREATE TABLE IF NOT EXISTS ad_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES fetch_runs(id),
    code TEXT NOT NULL,
    version_id INTEGER NOT NULL REFERENCES ad_versions(id),
    observed_ts REAL NOT NULL,
    publish_phrase TEXT,
    rank TEXT,
    canonical_path TEXT NOT NULL,
    raw_hash TEXT NOT NULL,
    UNIQUE(run_id, code)
);
CREATE INDEX IF NOT EXISTS idx_observations_code_time ON ad_observations(code, observed_ts);
CREATE TABLE IF NOT EXISTS change_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id INTEGER NOT NULL REFERENCES ad_observations(id),
    code TEXT NOT NULL,
    previous_version_id INTEGER REFERENCES ad_versions(id),
    new_version_id INTEGER NOT NULL REFERENCES ad_versions(id),
    event_type TEXT NOT NULL,
    categories_json TEXT NOT NULL,
    changed_paths_json TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    origin TEXT NOT NULL,
    created_ts REAL NOT NULL,
    UNIQUE(observation_id, event_type)
);
CREATE INDEX IF NOT EXISTS idx_events_code_time ON change_events(code, created_ts);

CREATE TABLE IF NOT EXISTS analysis_stats (
    category            TEXT,
    brand               TEXT,
    model               TEXT,
    variant             TEXT,
    raw_count           INTEGER,
    cleaned_count       INTEGER,
    mean_price          REAL,
    median_price        REAL,
    price_cv            REAL,
    market_depth        REAL,
    regression_slope    REAL,
    regression_r2       REAL,
    computed_ts         REAL,
    PRIMARY KEY (category, brand, model, variant)
);
"""


def open_store(path: Path = BAMA_DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# ads table writers
# ---------------------------------------------------------------------------

# Dimension/denormalized columns written on every sighting. ``code`` and
# ``first_seen_ts`` are handled separately (only set on insert).
_AD_MUTABLE_COLS = (
    "brand", "model", "variant", "category", "year", "mileage", "price",
    "price_type", "transmission", "publish_date_jalali", "last_seen_ts",
    "fetch_time_ts", "raw_payload",
)


def upsert_ad(conn: sqlite3.Connection, code: str, fields: dict[str, Any], observed_ts: float) -> bool:
    """Insert or refresh one ad snapshot. Re-sighting an ad marks it active again.

    ``fields`` carries the dimension/denormalized values plus ``raw_payload``
    (a dict, serialized here). Returns True when the row was newly inserted.
    """
    payload = fields.get("raw_payload")
    values = {col: fields.get(col) for col in _AD_MUTABLE_COLS}
    values["raw_payload"] = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    values["last_seen_ts"] = observed_ts
    values["code"] = str(code)

    row = conn.execute("SELECT 1 FROM ads WHERE code = ?;", (str(code),)).fetchone()
    if row is None:
        values["first_seen_ts"] = observed_ts
        values["status"] = "active"
        cols = ["code", "first_seen_ts", "status", *_AD_MUTABLE_COLS]
        placeholders = ", ".join(f":{c}" for c in cols)
        conn.execute(f"INSERT INTO ads ({', '.join(cols)}) VALUES ({placeholders});", values)
        return True

    set_clause = ", ".join(f"{c} = :{c}" for c in _AD_MUTABLE_COLS)
    conn.execute(
        f"UPDATE ads SET {set_clause}, status = 'active', removed_at = NULL WHERE code = :code;",
        values,
    )
    return False


def mark_inactive(conn: sqlite3.Connection, cutoff_ts: float) -> int:
    """Flag ads not seen since ``cutoff_ts`` as removed. Returns rows changed."""
    cur = conn.execute(
        """UPDATE ads SET status = 'removed', removed_at = last_seen_ts
           WHERE status = 'active' AND last_seen_ts < ?;""",
        (cutoff_ts,),
    )
    return cur.rowcount


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    active = conn.execute("SELECT COUNT(*) FROM ads WHERE status='active';").fetchone()[0]
    removed = conn.execute("SELECT COUNT(*) FROM ads WHERE status='removed';").fetchone()[0]
    return {"ads_active": int(active), "ads_removed": int(removed), "ads_total": int(active + removed)}


if __name__ == "__main__":
    conn = open_store()
    print(f"Opened store at {BAMA_DB_PATH}")
    print(counts(conn))
    conn.close()
