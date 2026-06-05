# BAMA Car Scraper Pipeline

## Overview
- `bama_fetch.py` scrapes ads from BAMA and writes directly to distributed files:
  - `BAMA ADS/{category}/{brand}/{model}/{variant}/ads.json`
- `generate_analysis_plots.py` reads those distributed `ads.json` files, creates `analysis.png` plots, and generates:
  - `liquidity_ranking_report.txt`

## Why this structure
- Avoids a single massive raw JSON file.
- Reduces corruption risk by using per-variant atomic writes.
- Preserves valid JSON arrays even if scraping stops unexpectedly.

## Key behavior
- Deduplication uses `detail.code` across sessions.
- Folder names are sanitized for filesystem safety.
- Incremental writes are buffered and flushed in batches.

## Run
```bash
python3 bama_fetch.py
python3 generate_analysis_plots.py
```

## Output
- Scraped data: `BAMA ADS/.../ads.json`
- Plot per leaf folder: `BAMA ADS/.../analysis.png`
- Global ranking: `liquidity_ranking_report.txt`
