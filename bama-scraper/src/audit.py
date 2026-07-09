#!/usr/bin/env python3
"""Audit and optionally repair the BAMA ADS tree and ``code_map.db``."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from history import history_health, open_history, project_lock, record_repair_event, seed_legacy_baseline
from fetch import (
    FORBIDDEN_AD_KEYS,
    SCHEMA,
    clean_name,
    intended_path_for_ad,
    load_brand_aliases,
    open_db,
    parse_absolute_jalali_date,
    pure_ad,
    to_jalali_string,
)
from paths import ADS_ROOT, BRAND_ALIASES_PATH, CODE_MAP_PATH, HISTORY_DB_PATH, OUTPUT_DIR, PROJECT_ROOT

try:
    from rich.console import Console
    from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
except ImportError:  # pragma: no cover
    Console = None


REPORT_PATH = OUTPUT_DIR / "audit_report.txt"
console = Console() if Console else None
_PAREN_RE = re.compile(r"\([^)]*\)")


# ---------------------------------------------------------------------------
# Audit data shapes
# ---------------------------------------------------------------------------

@dataclass
class Occurrence:
    code: str
    rel_path: str
    index: int
    modified_date: str = ""


@dataclass
class AuditResult:
    ads_files: list[Path] = field(default_factory=list)
    load_errors: list[str] = field(default_factory=list)
    shape_issues: list[str] = field(default_factory=list)
    occurrences: dict[str, list[Occurrence]] = field(default_factory=lambda: defaultdict(list))
    missing_code: int = 0
    forbidden_metadata: int = 0
    total_items: int = 0
    map_rows: int = 0
    stale_map_rows: int = 0
    path_mismatches: int = 0
    missing_publish_dates: int = 0
    map_only_codes: int = 0
    history_checks: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Small file and map helpers
# ---------------------------------------------------------------------------

def log(message: str) -> None:
    if console:
        console.print(message)
    else:
        print(message)


def rel_project(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def ad_code(ad: Any) -> str | None:
    if not isinstance(ad, dict):
        return None
    detail = ad.get("detail")
    if not isinstance(detail, dict):
        return None
    code = detail.get("code")
    return str(code).strip() if code else None


def load_ads(path: Path) -> tuple[list[Any] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return None, f"{rel_project(path)}: JSON decode error: {error}"
    except OSError as error:
        return None, f"{rel_project(path)}: read error: {error}"
    if not isinstance(data, list):
        return None, f"{rel_project(path)}: expected top-level list, got {type(data).__name__}"
    return data, None


def write_ads(path: Path, ads: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ads, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def read_map(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {row["code"]: row for row in conn.execute("SELECT * FROM ad_index;")}


def relocate_map(conn: sqlite3.Connection, code: str, rel_path: str) -> None:
    conn.execute("UPDATE ad_index SET file_path = ? WHERE code = ?;", (rel_path, str(code)))


def fill_publish_date(conn: sqlite3.Connection, code: str, publish_date: str) -> bool:
    cur = conn.execute(
        "UPDATE ad_index SET publish_date_jalali = ? WHERE code = ? AND publish_date_jalali IS NULL;",
        (publish_date, str(code)),
    )
    return cur.rowcount > 0


def seed_missing_map_row(conn: sqlite3.Connection, code: str, rel_path: str, publish_date: str | None) -> bool:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO ad_index
            (code, file_path, first_seen_ts, last_seen_ts, publish_date_jalali, fetch_time_ts)
        VALUES (?, ?, NULL, NULL, ?, NULL);
        """,
        (str(code), rel_path, publish_date),
    )
    return cur.rowcount > 0


def publish_date_for(ad: dict[str, Any]) -> str | None:
    detail = ad.get("detail") if isinstance(ad.get("detail"), dict) else {}
    raw_time = detail.get("time")
    if not raw_time:
        return None
    return to_jalali_string(parse_absolute_jalali_date(str(raw_time)))


# ---------------------------------------------------------------------------
# Read-only scan and report
# ---------------------------------------------------------------------------

def duplicate_groups(result: AuditResult) -> dict[str, list[Occurrence]]:
    return {code: items for code, items in result.occurrences.items() if len(items) > 1}


def scan(conn: sqlite3.Connection, brand_aliases: dict[str, str]) -> AuditResult:
    """Read every ads.json and compare file contents with map/path invariants."""
    result = AuditResult(ads_files=sorted(ADS_ROOT.rglob("ads.json")) if ADS_ROOT.exists() else [])
    map_rows = read_map(conn)
    result.map_rows = len(map_rows)
    seen_codes: set[str] = set()

    progress_context = (
        Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        )
        if console
        else None
    )
    manager = progress_context or _NullProgress()
    with manager as progress:
        task = progress.add_task("Auditing ads", total=len(result.ads_files))
        for file_path in result.ads_files:
            rel_path = file_path.relative_to(ADS_ROOT).as_posix()
            data, error = load_ads(file_path)
            if error:
                if "expected top-level" in error:
                    result.shape_issues.append(error)
                else:
                    result.load_errors.append(error)
                progress.advance(task)
                continue

            assert data is not None
            for index, item in enumerate(data):
                result.total_items += 1
                if not isinstance(item, dict):
                    result.shape_issues.append(f"{rel_project(file_path)}[{index}]: expected object")
                    continue
                forbidden = [key for key in FORBIDDEN_AD_KEYS if key in item]
                if forbidden:
                    result.forbidden_metadata += 1
                    result.shape_issues.append(
                        f"{rel_project(file_path)}[{index}]: forbidden metadata keys: {', '.join(forbidden)}"
                    )
                code = ad_code(item)
                if not code:
                    result.missing_code += 1
                    result.shape_issues.append(f"{rel_project(file_path)}[{index}]: missing detail.code")
                    continue

                seen_codes.add(code)
                modified = str((item.get("detail") or {}).get("modified_date") or "")
                result.occurrences[code].append(Occurrence(code, rel_path, index, modified))

                row = map_rows.get(code)
                if row is None:
                    result.stale_map_rows += 1
                elif row["file_path"] != rel_path:
                    result.stale_map_rows += 1

                # Canonicality check: the payload route should match its file location.
                intended = intended_path_for_ad(item, ADS_ROOT, brand_aliases).relative_to(ADS_ROOT).as_posix()
                if intended != rel_path:
                    result.path_mismatches += 1

            progress.advance(task)

    result.missing_publish_dates = int(conn.execute(
        "SELECT COUNT(*) AS c FROM ad_index WHERE publish_date_jalali IS NULL;"
    ).fetchone()["c"])
    result.map_only_codes = len(set(map_rows) - seen_codes)
    return result


class _NullProgress:
    def __enter__(self) -> "_NullProgress":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def add_task(self, *_args: Any, **_kwargs: Any) -> int:
        return 0

    def advance(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def report_lines(result: AuditResult, repairs: dict[str, int] | None = None) -> list[str]:
    duplicates = duplicate_groups(result)
    duplicate_occurrences = sum(len(items) for items in duplicates.values())
    lines = [
        "BAMA ADS HEALTH REPORT",
        "=" * 22,
        f"Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"Ads root: {rel_project(ADS_ROOT)}",
        "",
        "SUMMARY",
        "-" * 7,
        f"ads.json files: {len(result.ads_files):,}",
        f"total ads/items: {result.total_items:,}",
        f"unique ad codes: {len(result.occurrences):,}",
        f"map rows: {result.map_rows:,}",
        f"duplicate codes: {len(duplicates):,}",
        f"duplicate occurrences: {duplicate_occurrences:,}",
        f"schema/basic-shape issues: {len(result.shape_issues):,}",
        f"load/parse errors: {len(result.load_errors):,}",
        f"stale or missing map rows: {result.stale_map_rows:,}",
        f"path mismatches: {result.path_mismatches:,}",
        f"missing publish dates: {result.missing_publish_dates:,}",
        f"map-only codes: {result.map_only_codes:,}",
    ]
    if result.history_checks:
        lines += [
            "",
            "HISTORY",
            "-" * 7,
            *(f"{key}: {value:,}" for key, value in sorted(result.history_checks.items())),
        ]
    if repairs:
        lines += [
            "",
            "REPAIRS",
            "-" * 7,
            *(f"{key}: {value:,}" for key, value in sorted(repairs.items())),
        ]
    if duplicates:
        lines += ["", "DUPLICATES", "-" * 10]
        for code, items in list(duplicates.items())[:200]:
            paths = ", ".join(f"{item.rel_path}[{item.index}]" for item in items)
            lines.append(f"{code}: {paths}")
        if len(duplicates) > 200:
            lines.append(f"... {len(duplicates) - 200:,} more duplicate code(s)")
    problems = result.load_errors + result.shape_issues
    if problems:
        lines += ["", "ISSUES", "-" * 6]
        lines.extend(problems[:500])
        if len(problems) > 500:
            lines.append(f"... {len(problems) - 500:,} more issue(s)")
    return lines


def write_report(result: AuditResult, output: Path, repairs: dict[str, int] | None = None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(report_lines(result, repairs)) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Repair steps used by --fix
# ---------------------------------------------------------------------------

def choose_duplicate_keeper(items: list[Occurrence], map_rows: dict[str, sqlite3.Row]) -> Occurrence | None:
    """Prefer the map-canonical copy; otherwise keep the newest modified ad."""
    row = map_rows.get(items[0].code)
    if row is not None:
        matches = [item for item in items if item.rel_path == row["file_path"]]
        if len(matches) == 1:
            return matches[0]
    ranked = sorted(items, key=lambda item: (item.modified_date, item.rel_path), reverse=True)
    if len(ranked) > 1 and ranked[0].modified_date == ranked[1].modified_date and row is None:
        return None
    return ranked[0]


def remove_duplicates(conn: sqlite3.Connection, result: AuditResult) -> int:
    """Remove extra copies only when there is a safe keeper."""
    map_rows = read_map(conn)
    removals_by_file: dict[str, list[Occurrence]] = defaultdict(list)
    keepers: dict[str, Occurrence] = {}
    for code, items in duplicate_groups(result).items():
        keeper = choose_duplicate_keeper(items, map_rows)
        if keeper is None:
            continue
        keepers[code] = keeper
        for item in items:
            if item is not keeper:
                removals_by_file[item.rel_path].append(item)

    removed = 0
    for rel_path, removals in removals_by_file.items():
        path = ADS_ROOT / rel_path
        data, error = load_ads(path)
        if error or data is None:
            continue
        indices = []
        for item in removals:
            if 0 <= item.index < len(data) and ad_code(data[item.index]) == item.code:
                indices.append(item.index)
        for index in sorted(indices, reverse=True):
            del data[index]
            removed += 1
        write_ads(path, data)

    for code, keeper in keepers.items():
        relocate_map(conn, code, keeper.rel_path)
    return removed


def normalize_legacy_metadata(history_conn: sqlite3.Connection) -> int:
    """Strip old scraper-only keys from ads.json and record repair events."""
    changed = 0
    for path in sorted(ADS_ROOT.rglob("ads.json")):
        data, error = load_ads(path)
        if error or data is None:
            continue
        rel_path = path.relative_to(ADS_ROOT).as_posix()
        normalized = []
        for item in data:
            if not isinstance(item, dict):
                normalized.append(item)
                continue
            forbidden = [key for key in FORBIDDEN_AD_KEYS if key in item]
            code = ad_code(item)
            if code and forbidden:
                record_repair_event(
                    history_conn,
                    code,
                    "payload_repaired",
                    ["other"],
                    [f"/{key}" for key in forbidden],
                    [{"path": f"/{key}", "before": "legacy scraper metadata", "after": None} for key in forbidden],
                    datetime.now().timestamp(),
                )
            normalized.append(pure_ad(item))
        if normalized != data:
            write_ads(path, normalized)
            changed += 1
    return changed


def backfill_dates(conn: sqlite3.Connection) -> int:
    """Fill NULL publish dates when saved detail.time is an absolute Jalali date."""
    filled = 0
    for path in sorted(ADS_ROOT.rglob("ads.json")):
        data, error = load_ads(path)
        if error or data is None:
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            code = ad_code(item)
            publish = publish_date_for(item)
            if code and publish and fill_publish_date(conn, code, publish):
                filled += 1
    return filled


def relocate_tree(
    conn: sqlite3.Connection,
    history_conn: sqlite3.Connection,
    brand_aliases: dict[str, str],
) -> dict[str, int]:
    """Move ads to the current intended path and update map/history repair events."""
    files = sorted(ADS_ROOT.rglob("ads.json")) if ADS_ROOT.exists() else []
    cache: dict[str, list[Any]] = {}
    dirty: set[str] = set()
    moved = 0

    def data_for(rel_path: str) -> list[Any]:
        if rel_path not in cache:
            data, error = load_ads(ADS_ROOT / rel_path)
            cache[rel_path] = [] if error or data is None else data
        return cache[rel_path]

    for file_path in files:
        rel_path = file_path.relative_to(ADS_ROOT).as_posix()
        data = list(data_for(rel_path))
        for item in data:
            if not isinstance(item, dict):
                continue
            code = ad_code(item)
            if not code:
                continue
            intended_rel = intended_path_for_ad(item, ADS_ROOT, brand_aliases).relative_to(ADS_ROOT).as_posix()
            if intended_rel == rel_path:
                continue
            src = data_for(rel_path)
            src[:] = [ad for ad in src if not (isinstance(ad, dict) and ad_code(ad) == code)]
            dst = data_for(intended_rel)
            replaced = False
            for index, existing in enumerate(dst):
                if isinstance(existing, dict) and ad_code(existing) == code:
                    dst[index] = item
                    replaced = True
                    break
            if not replaced:
                dst.append(item)
            relocate_map(conn, code, intended_rel)
            if record_repair_event(
                history_conn,
                code,
                "route_changed",
                ["route/category"],
                ["/canonical_path"],
                [{"path": "/canonical_path", "before": rel_path, "after": intended_rel}],
                datetime.now().timestamp(),
            ):
                moved += 0
            dirty.update({rel_path, intended_rel})
            moved += 1

    emptied = 0
    for rel_path in dirty:
        path = ADS_ROOT / rel_path
        data = cache.get(rel_path, [])
        if data:
            write_ads(path, data)
        elif path.exists():
            path.unlink()
            emptied += 1

    pruned = prune_empty_dirs()
    return {"relocated": moved, "emptied_files": emptied, "pruned_dirs": pruned}


def seed_missing_map_rows(conn: sqlite3.Connection) -> int:
    """Add map rows for valid ads that are present on disk but missing in code_map."""
    inserted = 0
    for path in sorted(ADS_ROOT.rglob("ads.json")):
        data, error = load_ads(path)
        if error or data is None:
            continue
        rel_path = path.relative_to(ADS_ROOT).as_posix()
        for item in data:
            if not isinstance(item, dict):
                continue
            code = ad_code(item)
            if code and seed_missing_map_row(conn, code, rel_path, publish_date_for(item)):
                inserted += 1
    return inserted


def seed_missing_history(conn: sqlite3.Connection, history_conn: sqlite3.Connection) -> int:
    """Seed append-only history for old ads that predate history.db."""
    map_rows = read_map(conn)
    seeded = 0
    for path in sorted(ADS_ROOT.rglob("ads.json")):
        data, error = load_ads(path)
        if error or data is None:
            continue
        rel_path = path.relative_to(ADS_ROOT).as_posix()
        for item in data:
            if not isinstance(item, dict):
                continue
            code = ad_code(item)
            if not code:
                continue
            row = map_rows.get(code)
            observed_ts = None
            if row is not None:
                observed_ts = row["last_seen_ts"] or row["fetch_time_ts"] or row["first_seen_ts"]
            if observed_ts is None:
                observed_ts = path.stat().st_mtime
            if seed_legacy_baseline(history_conn, code, pure_ad(item), float(observed_ts), rel_path):
                seeded += 1
    history_conn.commit()
    return seeded


def prune_empty_dirs() -> int:
    pruned = 0
    for directory in sorted((p for p in ADS_ROOT.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            directory.rmdir()
            pruned += 1
        except OSError:
            pass
    return pruned


# ---------------------------------------------------------------------------
# brand_aliases.json bootstrap
# ---------------------------------------------------------------------------

def brand_group_key(brand_dir_name: str) -> str:
    return " ".join(_PAREN_RE.sub("", clean_name(brand_dir_name)).split())


def count_ads_under(directory: Path) -> int:
    total = 0
    for path in directory.rglob("ads.json"):
        data, error = load_ads(path)
        if not error and data is not None:
            total += len(data)
    return total


def generate_brand_map(force: bool) -> int:
    """Create review-only alias candidates from similar brand folder names."""
    if BRAND_ALIASES_PATH.exists() and not force:
        log(f"brand_aliases.json exists at {BRAND_ALIASES_PATH}; pass --force to overwrite.")
        return 1
    groups: dict[str, dict[str, int]] = {}
    if ADS_ROOT.exists():
        for category in (p for p in ADS_ROOT.iterdir() if p.is_dir()):
            for brand in (p for p in category.iterdir() if p.is_dir()):
                spelling = clean_name(brand.name)
                groups.setdefault(brand_group_key(brand.name), {})[spelling] = count_ads_under(brand)

    aliases: dict[str, str] = {}
    for spellings in groups.values():
        if len(spellings) <= 1:
            continue
        canonical = sorted(spellings, key=lambda item: (-spellings[item], len(item), item))[0]
        for spelling in spellings:
            if spelling != canonical:
                aliases[spelling] = canonical

    BRAND_ALIASES_PATH.parent.mkdir(parents=True, exist_ok=True)
    BRAND_ALIASES_PATH.write_text(
        json.dumps(dict(sorted(aliases.items())), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    log(f"Wrote {len(aliases):,} alias candidate(s) to {BRAND_ALIASES_PATH}. Review before relying on it.")
    return 0


# ---------------------------------------------------------------------------
# CLI workflow
# ---------------------------------------------------------------------------

def fix(conn: sqlite3.Connection, history_conn: sqlite3.Connection, brand_aliases: dict[str, str]) -> dict[str, int]:
    """Run the safe repair sequence, then rescan to remove remaining duplicates."""
    repairs: dict[str, int] = {}
    repairs["map_rows_seeded"] = seed_missing_map_rows(conn)
    repairs["history_baselines_seeded"] = seed_missing_history(conn, history_conn)
    repairs["legacy_files_normalized"] = normalize_legacy_metadata(history_conn)
    repairs["publish_dates_filled"] = backfill_dates(conn)
    relocation = relocate_tree(conn, history_conn, brand_aliases)
    repairs.update(relocation)
    conn.commit()
    history_conn.commit()
    after_relocate = scan(conn, brand_aliases)
    repairs["duplicates_removed"] = remove_duplicates(conn, after_relocate)
    conn.commit()
    repairs["pruned_dirs_after_duplicates"] = prune_empty_dirs()
    return repairs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit BAMA ads and optionally repair safe invariants.")
    parser.add_argument("--fix", action="store_true", help="Normalize metadata, seed/fix map rows, backfill dates, relocate paths, and remove duplicates.")
    parser.add_argument("--brand-map", action="store_true", help="Generate candidate brand_aliases.json from on-disk brand twins.")
    parser.add_argument("--force", action="store_true", help="Allow --brand-map to overwrite an existing file.")
    parser.add_argument("--output", type=Path, default=REPORT_PATH, help="Health report path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.brand_map:
        return generate_brand_map(args.force)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with project_lock(exclusive=args.fix):
        conn = open_db(CODE_MAP_PATH)
        history_conn = open_history(HISTORY_DB_PATH)
        try:
            brand_aliases = load_brand_aliases()
            repairs = fix(conn, history_conn, brand_aliases) if args.fix else None
            result = scan(conn, brand_aliases)
            result.history_checks = history_health(history_conn, set(result.occurrences))
            write_report(result, args.output, repairs)
        finally:
            conn.commit()
            history_conn.commit()
            conn.close()
            history_conn.close()

    duplicates = duplicate_groups(result)
    log(f"Report written: {rel_project(args.output)}")
    log(
        f"Health: total_ads={result.total_items:,} map_rows={result.map_rows:,} "
        f"duplicates={len(duplicates):,} issues={len(result.shape_issues):,} "
        f"stale_map_rows={result.stale_map_rows:,} path_mismatches={result.path_mismatches:,} "
        f"missing_publish_dates={result.missing_publish_dates:,}"
    )
    if repairs:
        log("Repairs: " + ", ".join(f"{key}={value:,}" for key, value in sorted(repairs.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
