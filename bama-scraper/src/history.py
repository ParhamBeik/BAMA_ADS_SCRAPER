"""Append-only SQLite history for scraper sightings and semantic changes."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import sqlite3
import time
import zlib
from typing import Any, Iterator

from paths import PROJECT_LOCK_PATH

VOLATILE_DETAIL_KEYS = {"time", "rank"}
REAPPEAR_AFTER_SECONDS = 14 * 86400


# ---------------------------------------------------------------------------
# Process locking
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def project_lock(exclusive: bool) -> Iterator[None]:
    """Prevent fetch/audit writers from editing JSON and SQLite at the same time."""
    PROJECT_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PROJECT_LOCK_PATH.open("a+") as handle:
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(handle.fileno(), mode | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another scraper fetch or audit writer is active") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Payload hashing and change classification
# ---------------------------------------------------------------------------

def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def semantic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop fields that change every fetch but do not mean the ad content changed."""
    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    detail = normalized.get("detail")
    if isinstance(detail, dict):
        for key in VOLATILE_DETAIL_KEYS:
            detail.pop(key, None)
    return normalized


def payload_hashes(payload: dict[str, Any]) -> tuple[str, str]:
    raw_hash = hashlib.sha256(canonical_json(payload)).hexdigest()
    semantic_hash = hashlib.sha256(canonical_json(semantic_payload(payload))).hexdigest()
    return raw_hash, semantic_hash


def _summary(value: Any) -> Any:
    packed = canonical_json(value)
    if len(packed) <= 1000:
        return value
    return {"sha256": hashlib.sha256(packed).hexdigest(), "bytes": len(packed)}


def diff_payloads(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    """Return simple path-based differences for human-readable change events."""
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}/{key}"
            if key not in before:
                changes.append({"path": child, "before": None, "after": _summary(after[key])})
            elif key not in after:
                changes.append({"path": child, "before": _summary(before[key]), "after": None})
            else:
                changes.extend(diff_payloads(before[key], after[key], child))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        return [] if before == after else [{"path": path or "/", "before": _summary(before), "after": _summary(after)}]
    return [] if before == after else [{"path": path or "/", "before": _summary(before), "after": _summary(after)}]


def categories_for(paths: list[str]) -> list[str]:
    """Group low-level JSON paths into readable change categories."""
    categories: set[str] = set()
    for path in paths:
        if path.startswith("/price/"):
            categories.add("price/payment")
        elif path == "/detail/description":
            categories.add("description")
        elif path == "/detail/mileage":
            categories.add("mileage")
        elif path == "/detail/location":
            categories.add("location")
        elif path.startswith("/images") or path.startswith("/videos") or path == "/detail/image":
            categories.add("media")
        elif path.startswith("/dealer") or "/seller" in path:
            categories.add("seller/dealer")
        elif path.startswith("/promotion") or path in {"/detail/pin", "/detail/badge", "/detail/specialcase"}:
            categories.add("promotion")
        elif path in {"/detail/type", "/detail/title", "/detail/brand", "/detail/brand_fa", "/detail/trim"}:
            categories.add("route/category")
        elif path.startswith("/detail/") or path.startswith("/specs/"):
            categories.add("vehicle attributes")
        else:
            categories.add("other")
    return sorted(categories)


def unpack_payload(blob: bytes) -> dict[str, Any]:
    return json.loads(zlib.decompress(blob).decode())


# ---------------------------------------------------------------------------
# Fetch runs, observations, and repair events
# ---------------------------------------------------------------------------

def start_run(conn: sqlite3.Connection, origin: str, max_ads: int | None = None) -> int:
    cur = conn.execute("INSERT INTO fetch_runs(origin,status,started_ts,max_ads) VALUES(?, 'running', ?, ?)",
                       (origin, time.time(), max_ads))
    conn.commit()
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, fetched: int = 0,
               pages: int = 0, reached_end: bool = False, error: str | None = None) -> None:
    conn.execute("""UPDATE fetch_runs SET status=?,finished_ts=?,fetched_count=?,page_count=?,reached_end=?,error=? WHERE id=?""",
                 (status, time.time(), fetched, pages, int(reached_end), error, run_id))
    conn.commit()


def record_observation(conn: sqlite3.Connection, run_id: int, code: str, payload: dict[str, Any],
                       observed_ts: float, canonical_path: str, origin: str) -> tuple[bool, bool]:
    """Record one sighting and create events only when meaningfully changed."""
    raw_hash, semantic_hash = payload_hashes(payload)
    previous = conn.execute("""SELECT o.*,v.semantic_hash FROM ad_observations o JOIN ad_versions v ON v.id=o.version_id
        WHERE o.code=? ORDER BY o.observed_ts DESC,o.id DESC LIMIT 1""", (code,)).fetchone()
    version = conn.execute("SELECT * FROM ad_versions WHERE code=? AND semantic_hash=?", (code, semantic_hash)).fetchone()
    if version is None:
        # New semantic content gets one immutable compressed version.
        cur = conn.execute("""INSERT INTO ad_versions(code,semantic_hash,raw_hash,payload_zlib,origin,first_observed_ts)
            VALUES(?,?,?,?,?,?)""", (code, semantic_hash, raw_hash, zlib.compress(canonical_json(payload), 9), origin, observed_ts))
        version_id = int(cur.lastrowid)
        version_created = True
    else:
        version_id = int(version["id"])
        version_created = False
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    cur = conn.execute("""INSERT OR IGNORE INTO ad_observations
        (run_id,code,version_id,observed_ts,publish_phrase,rank,canonical_path,raw_hash) VALUES(?,?,?,?,?,?,?,?)""",
        (run_id, code, version_id, observed_ts, str(detail.get("time") or ""), str(detail.get("rank") or ""), canonical_path, raw_hash))
    if cur.rowcount == 0:
        return False, False
    observation_id = int(cur.lastrowid)
    event_created = False
    if previous and int(previous["version_id"]) != version_id:
        # Time/rank changes alone do not create content_changed events.
        old = conn.execute("SELECT payload_zlib FROM ad_versions WHERE id=?", (previous["version_id"],)).fetchone()
        changes = diff_payloads(semantic_payload(unpack_payload(old["payload_zlib"])), semantic_payload(payload))
        paths = [item["path"] for item in changes]
        _insert_event(conn, observation_id, code, int(previous["version_id"]), version_id, "content_changed",
                      categories_for(paths), paths, changes, origin, observed_ts)
        event_created = True
    if previous and previous["canonical_path"] != canonical_path:
        changes = [{"path": "/canonical_path", "before": previous["canonical_path"], "after": canonical_path}]
        _insert_event(conn, observation_id, code, int(previous["version_id"]), version_id, "route_changed",
                      ["route/category"], ["/canonical_path"], changes, origin, observed_ts)
        event_created = True
    if previous and observed_ts - float(previous["observed_ts"]) >= REAPPEAR_AFTER_SECONDS:
        _insert_event(conn, observation_id, code, int(previous["version_id"]), version_id, "reappeared",
                      [], [], [], origin, observed_ts)
        event_created = True
    return version_created, event_created


def seed_legacy_baseline(
    conn: sqlite3.Connection,
    code: str,
    payload: dict[str, Any],
    observed_ts: float,
    canonical_path: str,
) -> bool:
    """Create a first history version for ads that existed before history.db."""
    if conn.execute("SELECT 1 FROM ad_versions WHERE code=? LIMIT 1", (code,)).fetchone():
        return False

    raw_hash, semantic_hash = payload_hashes(payload)
    cur = conn.execute("""INSERT OR IGNORE INTO ad_versions
        (code,semantic_hash,raw_hash,payload_zlib,origin,first_observed_ts)
        VALUES(?,?,?,?,?,?)""", (
        code,
        semantic_hash,
        raw_hash,
        zlib.compress(canonical_json(payload), 9),
        "legacy_baseline",
        observed_ts,
    ))
    if cur.rowcount == 0:
        return False

    version_id = int(cur.lastrowid)
    run = conn.execute("SELECT id FROM fetch_runs WHERE origin='legacy_baseline' ORDER BY id LIMIT 1").fetchone()
    if run is None:
        run_id = start_run(conn, "legacy_baseline")
        finish_run(conn, run_id, "completed")
    else:
        run_id = int(run["id"])
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    conn.execute("""INSERT OR IGNORE INTO ad_observations
        (run_id,code,version_id,observed_ts,publish_phrase,rank,canonical_path,raw_hash)
        VALUES(?,?,?,?,?,?,?,?)""", (
        run_id,
        code,
        version_id,
        observed_ts,
        str(detail.get("time") or ""),
        str(detail.get("rank") or ""),
        canonical_path,
        raw_hash,
    ))
    return True


def record_repair_event(
    conn: sqlite3.Connection,
    code: str,
    event_type: str,
    categories: list[str],
    paths: list[str],
    changes: list[dict[str, Any]],
    created_ts: float,
) -> bool:
    """Attach audit repairs to the latest known observation for the code."""
    latest = conn.execute("""SELECT o.id observation_id,o.version_id FROM ad_observations o
        WHERE o.code=? ORDER BY o.observed_ts DESC,o.id DESC LIMIT 1""", (code,)).fetchone()
    if latest is None:
        return False
    before = json.dumps(changes, ensure_ascii=False)
    exists = conn.execute("""SELECT 1 FROM change_events
        WHERE code=? AND event_type=? AND changes_json=? LIMIT 1""", (code, event_type, before)).fetchone()
    if exists:
        return False
    _insert_event(
        conn,
        int(latest["observation_id"]),
        code,
        int(latest["version_id"]),
        int(latest["version_id"]),
        event_type,
        categories,
        paths,
        changes,
        "audit_repair",
        created_ts,
    )
    return True


def _insert_event(conn: sqlite3.Connection, observation_id: int, code: str, previous_id: int | None,
                  new_id: int, event_type: str, categories: list[str], paths: list[str], changes: list[dict[str, Any]],
                  origin: str, created_ts: float) -> None:
    conn.execute("""INSERT OR IGNORE INTO change_events
        (observation_id,code,previous_version_id,new_version_id,event_type,categories_json,changed_paths_json,changes_json,origin,created_ts)
        VALUES(?,?,?,?,?,?,?,?,?,?)""", (observation_id, code, previous_id, new_id, event_type,
        json.dumps(categories, ensure_ascii=False), json.dumps(paths, ensure_ascii=False),
        json.dumps(changes, ensure_ascii=False), origin, created_ts))


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

def history_health(conn: sqlite3.Connection, expected_codes: set[str]) -> dict[str, int]:
    version_codes = {row[0] for row in conn.execute("SELECT DISTINCT code FROM ad_versions")}
    return {
        "codes_without_history": len(expected_codes - version_codes),
        "orphan_observations": int(conn.execute("""SELECT count(*) FROM ad_observations o LEFT JOIN ad_versions v ON v.id=o.version_id WHERE v.id IS NULL""").fetchone()[0]),
        "orphan_events": int(conn.execute("""SELECT count(*) FROM change_events e LEFT JOIN ad_observations o ON o.id=e.observation_id WHERE o.id IS NULL""").fetchone()[0]),
        "unfinished_runs": int(conn.execute("SELECT count(*) FROM fetch_runs WHERE status='running'").fetchone()[0]),
        "latest_version_mismatches": int(conn.execute("""SELECT count(*) FROM (
            SELECT o.code,o.version_id FROM ad_observations o
            JOIN (SELECT code,max(observed_ts) observed_ts FROM ad_observations GROUP BY code) latest
              ON latest.code=o.code AND latest.observed_ts=o.observed_ts
            JOIN ad_versions v ON v.id=o.version_id
            WHERE v.code<>o.code
        ) x""").fetchone()[0]),
    }
