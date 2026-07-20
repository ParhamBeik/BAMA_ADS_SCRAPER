# BAMA Ads Scraper

A standalone scraper that turns Bama.ir's car listings into a single, queryable
SQLite database. Run it once and you get a snapshot of the market; run it
continuously and `data/bama.db` becomes a self-updating machine that tracks every
ad's appearance, price/content changes, and disappearance over time.

## What It Does

Every `fetch.py` run:

1. Fetches ads in Bama's exact payload shape, page by page.
2. Upserts each ad into the `ads` table — the current snapshot **and** the index
   (`code` is the primary key; brand/model/variant/category are columns, not folders).
3. Appends to the append-only history (`fetch_runs`, `ad_versions`,
   `ad_observations`, `change_events`): new semantic content becomes an immutable
   compressed version; price/content changes become change events.
4. After a complete run, an **auto-pipeline** fires: marks vanished ads `removed`,
   runs DB integrity checks (`audit`), and refreshes per-category market stats
   (`analyze`) into `analysis_stats`. Each step is isolated so a failure never
   loses the fetch.

There is no JSON file tree and no separate routing/history DBs — one `bama.db`
holds everything.

## Structure

```text
bama-scraper/
├── src/
│   ├── paths.py          # project/data paths (incl. BAMA_DB_PATH)
│   ├── store.py          # bama.db schema owner + open_store/upsert_ad/mark_inactive
│   ├── fetch.py          # fetch pages -> ads table + history; runs auto-pipeline
│   ├── history.py        # append-only versions/observations/change events
│   ├── audit.py          # DB integrity checks + safe repairs
│   └── analyze.py        # per-category market stats -> analysis_stats
├── tests/                # pytest, real temp SQLite, no mocking
└── data/
    ├── bama.db           # the single source of truth (57k+ ads + full history)
    ├── time_dictionary.json
    ├── brand_aliases.json
    ├── unknown_times.log
    └── outputs/audit_report.txt
```

## Setup

```bash
cd bama-scraper
pip install -e ".[dev]"
```

## Commands

```bash
# Fetch a snapshot (also marks removed ads, audits, and refreshes stats).
python src/fetch.py
BAMA_MAX_ADS=200 python src/fetch.py    # cap for a quick run

# Standalone maintenance (fetch runs these automatically).
python src/audit.py                     # write data/outputs/audit_report.txt
python src/audit.py --fix               # backfill NULL publish dates from payloads
python src/audit.py --brand-map         # propose brand_aliases.json from ads brands
python src/analyze.py                   # recompute analysis_stats
```

## Querying the data

```bash
sqlite3 data/bama.db "SELECT count(*) FROM ads WHERE status='active';"
sqlite3 data/bama.db "SELECT model, mean_price, median_price, cleaned_count
                        FROM analysis_stats ORDER BY cleaned_count DESC LIMIT 10;"
sqlite3 data/bama.db "SELECT event_type, count(*) FROM change_events GROUP BY event_type;"
```

## Reading Order

1. `src/paths.py` — where everything lives.
2. `src/store.py` — the schema and the only DB opener; start here to learn the tables.
3. `src/fetch.py` — the main loop: fetch → `upsert_ad` + `record_observation` → auto-pipeline.
4. `src/history.py` — how versions/observations/events are recorded (semantic vs raw hashing).
5. `src/audit.py` / `src/analyze.py` — the two pipeline steps that run after each fetch.

## Notes

- Stored payloads (`ads.raw_payload`) stay pure Bama JSON; scraper bookkeeping lives in columns.
- Re-sighting a `removed` ad flips it back to `active`; `mark_inactive` only touches ads not
  seen since the run started, so an interrupted run never marks anything removed.
- `fetch.py` and `audit.py --fix`/`--brand-map` take an exclusive project lock; read-only
  audit takes a shared lock.
- Tests use a fresh `open_store(tmp_path/"bama.db")` per test — no services, CI-safe.
