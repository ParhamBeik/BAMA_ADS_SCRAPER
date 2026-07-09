# BAMA Ads Scraper

Flat scraper-only project for Bama.ir listings.

## What It Does

- Fetches ads into the exact Bama payload shape.
- Maintains local `code_map.db` metadata for routing and publish dates.
- Maintains append-only `history.db` sightings, payload versions, and repair/change events.
- Audits/repairs the on-disk tree.
- Generates leaf-level `analysis.png` charts.

## Structure

```text
bama-scraper/
├── src/
│   ├── fetch.py
│   ├── audit.py
│   ├── analyze.py
│   ├── history.py
│   └── paths.py
└── data/
    ├── BAMA ADS/
    ├── time_dictionary.json
    ├── brand_aliases.json
    ├── code_map.db
    ├── history.db
    ├── unknown_times.log
    ├── route_conflicts.log
    └── outputs/audit_report.txt
```

## Commands

```bash
cd bama-scraper
python src/fetch.py
python src/audit.py
python src/audit.py --fix
python src/audit.py --brand-map
python src/analyze.py
```

## Reading Order

1. `src/paths.py` defines the project/data paths.
2. `src/fetch.py` fetches Bama pages, routes ads, writes pure `ads.json`, updates `code_map.db`, and records history.
3. `src/history.py` records append-only fetch runs, observations, semantic versions, and change events.
4. `src/audit.py` checks and repairs the JSON tree, map, publish dates, and history baselines.
5. `src/analyze.py` creates one `analysis.png` beside each usable leaf `ads.json`.

## Notes

- `ads.json` stays pure; scraper metadata lives in SQLite.
- `history.db` stores one fetch run plus one sighting per code/run; repeated semantic payloads reuse versions.
- `audit.py --fix` is the safe reconciliation path after path logic changes.
- `fetch.py` and `audit.py --fix` take an exclusive project lock; read-only audit takes a shared lock.
- This project intentionally stays flat and lightweight.
