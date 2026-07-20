#!/usr/bin/env python3
"""Integrity checks and safe repairs over the single ``bama.db`` store.

With no file tree, auditing is now DB-only: history health (orphans, unfinished
runs, latest-version mismatches), missing publish dates, and active/removed
counts. ``run_checks`` is invoked automatically by ``fetch.py``'s pipeline and is
also runnable standalone. ``--fix`` backfills publish dates from stored payloads;
``--brand-map`` proposes brand aliases from the ``ads`` dimensions.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from history import history_health, project_lock
from store import open_store, counts
from fetch import clean_name, parse_absolute_jalali_date, to_jalali_string
from paths import BRAND_ALIASES_PATH, OUTPUT_DIR

try:
    from rich.console import Console
except ImportError:  # pragma: no cover
    Console = None


REPORT_PATH = OUTPUT_DIR / "audit_report.txt"
console = Console() if Console else None
_PAREN_RE = re.compile(r"\([^)]*\)")


def log(message: str) -> None:
    (console.print if console else print)(message)


# ---------------------------------------------------------------------------
# Integrity checks
# ---------------------------------------------------------------------------

def run_checks(conn) -> dict[str, int]:
    """Return DB health counters. Read-only; safe to call after every fetch."""
    codes = {row[0] for row in conn.execute("SELECT code FROM ads;")}
    checks = history_health(conn, codes)
    checks.update(counts(conn))
    checks["missing_publish_dates"] = int(conn.execute(
        "SELECT COUNT(*) FROM ads WHERE publish_date_jalali IS NULL;"
    ).fetchone()[0])
    checks["priced_ads"] = int(conn.execute(
        "SELECT COUNT(*) FROM ads WHERE price IS NOT NULL;"
    ).fetchone()[0])
    return checks


def write_report(checks: dict[str, int], output: Path, repairs: dict[str, int] | None = None) -> None:
    lines = [
        "BAMA DB HEALTH REPORT",
        "=" * 21,
        f"Generated at: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "CHECKS",
        "-" * 6,
        *(f"{key}: {value:,}" for key, value in sorted(checks.items())),
    ]
    if repairs:
        lines += ["", "REPAIRS", "-" * 7, *(f"{key}: {value:,}" for key, value in sorted(repairs.items()))]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# --fix: safe DB repairs
# ---------------------------------------------------------------------------

def backfill_dates(conn) -> int:
    """Fill NULL publish dates when a stored payload's detail.time is absolute."""
    filled = 0
    rows = conn.execute(
        "SELECT code, raw_payload FROM ads WHERE publish_date_jalali IS NULL;"
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["raw_payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
        publish = to_jalali_string(parse_absolute_jalali_date(str(detail.get("time") or "")))
        if publish:
            conn.execute(
                "UPDATE ads SET publish_date_jalali = ? WHERE code = ? AND publish_date_jalali IS NULL;",
                (publish, row["code"]),
            )
            filled += 1
    conn.commit()
    return filled


# ---------------------------------------------------------------------------
# --brand-map: alias candidates from ads dimensions
# ---------------------------------------------------------------------------

def brand_group_key(brand: str) -> str:
    return " ".join(_PAREN_RE.sub("", clean_name(brand)).split())


def generate_brand_map(conn, force: bool) -> int:
    """Propose brand aliases by grouping similar brand spellings in the ads table."""
    if BRAND_ALIASES_PATH.exists() and not force:
        log(f"brand_aliases.json exists at {BRAND_ALIASES_PATH}; pass --force to overwrite.")
        return 1
    groups: dict[str, dict[str, int]] = {}
    for brand, n in conn.execute("SELECT brand, COUNT(*) FROM ads GROUP BY brand;"):
        spelling = clean_name(brand)
        groups.setdefault(brand_group_key(brand), {})[spelling] = int(n)

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
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit bama.db and optionally repair safe invariants.")
    parser.add_argument("--fix", action="store_true", help="Backfill NULL publish dates from stored payloads.")
    parser.add_argument("--brand-map", action="store_true", help="Generate candidate brand_aliases.json from ads brands.")
    parser.add_argument("--force", action="store_true", help="Allow --brand-map to overwrite an existing file.")
    parser.add_argument("--output", type=Path, default=REPORT_PATH, help="Health report path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with project_lock(exclusive=args.fix or args.brand_map):
        conn = open_store()
        try:
            if args.brand_map:
                return generate_brand_map(conn, args.force)
            repairs = {"publish_dates_filled": backfill_dates(conn)} if args.fix else None
            checks = run_checks(conn)
            write_report(checks, args.output, repairs)
        finally:
            conn.commit()
            conn.close()

    log("Health: " + ", ".join(f"{k}={v:,}" for k, v in sorted(checks.items())))
    if repairs:
        log("Repairs: " + ", ".join(f"{k}={v:,}" for k, v in sorted(repairs.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
