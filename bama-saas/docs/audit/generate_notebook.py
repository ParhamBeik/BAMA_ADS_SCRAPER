#!/usr/bin/env python3
"""Generate the Bama SaaS Readiness Audit Notebook.

Run: python3 bama-saas/docs/audit/generate_notebook.py
Produces: bama-saas/docs/audit/bama_saas_readiness_audit.ipynb
"""
import json
import sys
from pathlib import Path

def text_cell(source: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": source.splitlines(True)}

def code_cell(source: str) -> dict:
    return {
        "cell_type": "code", "metadata": {},
        "source": source.splitlines(True),
        "execution_count": None, "outputs": [],
    }

cells = [
    text_cell("# Bama SaaS Rollout-Readiness Audit Notebook\n\n"
              "**Date**: 2026-08-01  \n"
              "**Commit**: c5d1868 (main)  \n"
              "**DB snapshot**: pre-audit backup 43 MB  \n"
              "**Constraint**: read-only audit — no code/schema/migration changes\n"),

    text_cell("## Setup\n"),
    code_cell(
        "import psycopg2\n"
        "import pandas as pd\n"
        "import numpy as np\n"
        "from IPython.display import display, Markdown\n\n"
        "conn = psycopg2.connect(\n"
        "    host='localhost', port=5433, dbname='bama_saas',\n"
        "    user='postgres', password='postgres'\n"
        ")\n"
        "def q(sql): return pd.read_sql(sql, conn)\n"
        "print('Connected:', conn.info.dbname)\n"
    ),

    text_cell("## 1. Table Inventory & Row Counts\n"),
    code_cell(
        "counts = q(\"\"\"\n"
        "SELECT table_name,\n"
        "  (xpath('/r/c/text()', query_to_xml(\n"
        "    format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name),\n"
        "    false, true, '')))[1]::text::bigint AS rows\n"
        "FROM information_schema.tables\n"
        "WHERE table_schema='public' AND table_type='BASE TABLE'\n"
        "ORDER BY rows DESC\n"
        "\"\"\")\n"
        "display(counts)\n"
        "print(f'Total tables: {len(counts)}')\n"
        "print(f'Total rows: {counts[\"rows\"].sum():,}')\n"
    ),

    text_cell("## 2. Null Rates per Key Column\n"),
    code_cell(
        "nulls = q(\"\"\"\n"
        "SELECT count(*) AS total,\n"
        "  count(*) FILTER (WHERE brand_id IS NULL) AS null_brand,\n"
        "  count(*) FILTER (WHERE model_id IS NULL) AS null_model,\n"
        "  count(*) FILTER (WHERE variant_id IS NULL) AS null_variant,\n"
        "  count(*) FILTER (WHERE city_id IS NULL) AS null_city,\n"
        "  count(*) FILTER (WHERE dealer_id IS NULL) AS null_dealer,\n"
        "  count(*) FILTER (WHERE year IS NULL) AS null_year,\n"
        "  count(*) FILTER (WHERE year_jalali IS NULL) AS null_year_j,\n"
        "  count(*) FILTER (WHERE mileage IS NULL) AS null_mileage,\n"
        "  count(*) FILTER (WHERE current_price IS NULL) AS null_price,\n"
        "  count(*) FILTER (WHERE publish_at IS NULL) AS null_publish,\n"
        "  count(*) FILTER (WHERE first_seen_at IS NULL) AS null_first_seen,\n"
        "  count(*) FILTER (WHERE last_seen_at IS NULL) AS null_last_seen\n"
        "FROM catalog_ad\n"
        "\"\"\")\n"
        "total = nulls['total'].iloc[0]\n"
        "rates = nulls.iloc[0, 1:].apply(lambda v: f'{v:,} ({v/total*100:.1f}%)')\n"
        "display(pd.DataFrame({'column': rates.index, 'null_count (pct)': rates.values}))\n"
    ),

    text_cell("## 3. Price Type Distribution\n"),
    code_cell(
        "prices = q(\"\"\"\n"
        "SELECT price_type, count(*) AS cnt,\n"
        "  round(count(*)::numeric/(SELECT count(*) FROM catalog_ad)*100,2) AS pct,\n"
        "  round(avg(current_price)::numeric,0) AS avg_price,\n"
        "  min(current_price) AS min_price, max(current_price) AS max_price\n"
        "FROM catalog_ad GROUP BY price_type ORDER BY cnt DESC\n"
        "\"\"\")\n"
        "display(prices)\n"
    ),

    text_cell("## 4. Year Calendar & Distribution\n"),
    code_cell(
        "years = q(\"\"\"\n"
        "SELECT year_jalali, year_calendar, count(*) AS cnt\n"
        "FROM catalog_ad WHERE year_jalali IS NOT NULL\n"
        "GROUP BY year_jalali, year_calendar ORDER BY cnt DESC LIMIT 20\n"
        "\"\"\")\n"
        "display(years)\n"
    ),

    text_cell("## 5. Quality Flags & Ingest Rejects\n"),
    code_cell(
        "flags = q(\"\"\"\n"
        "SELECT\n"
        "  count(*) FILTER (WHERE quality_flags = '[]'::jsonb) AS clean,\n"
        "  count(*) FILTER (WHERE quality_flags != '[]'::jsonb) AS flagged,\n"
        "  count(*) FILTER (WHERE quality_flags @> '[\"mileage_implausible\"]') AS mileage_implausible,\n"
        "  count(*) FILTER (WHERE quality_flags @> '[\"price_too_high\"]') AS price_too_high,\n"
        "  count(*) FILTER (WHERE quality_flags @> '[\"price_sentinel\"]') AS price_sentinel\n"
        "FROM catalog_ad\n"
        "\"\"\")\n"
        "display(flags)\n\n"
        "rejects = q('SELECT rule, count(*) AS cnt FROM history_ingestreject GROUP BY rule ORDER BY cnt DESC')\n"
        "display(rejects)\n"
    ),

    text_cell("## 6. FetchRun Reconciliation\n"),
    code_cell(
        "runs = q(\"\"\"\n"
        "SELECT source, mode, status, stop_reason, reached_end,\n"
        "  count(*) AS runs, sum(fetched_count) AS fetched,\n"
        "  sum(created_count) AS created, sum(updated_count) AS updated,\n"
        "  sum(price_change_count) AS price_changes, sum(pages_fetched) AS pages,\n"
        "  max(deepest_rank) AS deepest\n"
        "FROM history_fetchrun\n"
        "GROUP BY source, mode, status, stop_reason, reached_end\n"
        "ORDER BY runs DESC\n"
        "\"\"\")\n"
        "display(runs)\n\n"
        "recon = q(\"\"\"\n"
        "SELECT\n"
        "  (SELECT sum(fetched_count) FROM history_fetchrun) AS run_total,\n"
        "  (SELECT count(*) FROM history_adobservation) AS obs_count,\n"
        "  (SELECT count(DISTINCT ad_id) FROM history_adobservation) AS distinct_ads\n"
        "\"\"\")\n"
        "display(recon)\n"
    ),

    text_cell("## 7. Referential Integrity\n"),
    code_cell(
        "integrity = q(\"\"\"\n"
        "SELECT 'orphan_obs' AS chk, count(*) AS cnt FROM history_adobservation o LEFT JOIN catalog_ad a ON o.ad_id=a.code WHERE a.code IS NULL\n"
        "UNION ALL SELECT 'orphan_version', count(*) FROM history_adversion v LEFT JOIN catalog_ad a ON v.ad_id=a.code WHERE a.code IS NULL\n"
        "UNION ALL SELECT 'orphan_price', count(*) FROM market_priceobservation p LEFT JOIN catalog_ad a ON p.ad_id=a.code WHERE a.code IS NULL\n"
        "UNION ALL SELECT 'orphan_drop', count(*) FROM market_pricedropevent d LEFT JOIN catalog_ad a ON d.ad_id=a.code WHERE a.code IS NULL\n"
        "UNION ALL SELECT 'orphan_change', count(*) FROM history_adchangeevent c LEFT JOIN catalog_ad a ON c.ad_id=a.code WHERE a.code IS NULL\n"
        "UNION ALL SELECT 'orphan_score', count(*) FROM analytics_dealscorecache s LEFT JOIN catalog_ad a ON s.ad_id=a.code WHERE a.code IS NULL\n"
        "\"\"\")\n"
        "display(integrity)\n"
        "assert integrity['cnt'].sum() == 0, 'Orphans found!'\n"
        "print('All referential integrity checks PASSED')\n"
    ),

    text_cell("## 8. Analytical Risk Tests\n"),
    code_cell(
        "# RISK: first_seen_at vs publish_at lag\n"
        "lag = q(\"\"\"\n"
        "SELECT count(*) AS total,\n"
        "  round(avg(EXTRACT(EPOCH FROM (first_seen_at-publish_at))/3600)::numeric,1) AS avg_lag_hours,\n"
        "  round(percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (first_seen_at-publish_at))/3600)::numeric,1) AS median_lag_hours,\n"
        "  max(EXTRACT(EPOCH FROM (first_seen_at-publish_at))/86400)::int AS max_lag_days\n"
        "FROM catalog_ad WHERE publish_at IS NOT NULL AND first_seen_at IS NOT NULL\n"
        "\"\"\")\n"
        "display(Markdown('### first_seen_at vs publish_at lag'))\n"
        "display(lag)\n"
        "display(Markdown('> **Risk**: avg lag 344h (14 days), median 203h (8.4 days). '\n"
        "                  '\"New\" means first-observed, NOT newly-published.'))\n"
    ),
    code_cell(
        "# RISK: Thin peer groups\n"
        "peers = q(\"\"\"\n"
        "SELECT\n"
        "  count(*) AS total_models,\n"
        "  count(*) FILTER (WHERE c < 3) AS below_3,\n"
        "  count(*) FILTER (WHERE c BETWEEN 3 AND 9) AS k3_9,\n"
        "  count(*) FILTER (WHERE c BETWEEN 10 AND 49) AS k10_49,\n"
        "  count(*) FILTER (WHERE c >= 50) AS k50_plus\n"
        "FROM (SELECT model_id, count(*) AS c FROM catalog_ad\n"
        "      WHERE status='active' AND current_price>0 AND model_id IS NOT NULL\n"
        "      GROUP BY model_id) t\n"
        "\"\"\")\n"
        "display(Markdown('### Peer group sizes for deal scoring'))\n"
        "display(peers)\n"
    ),
    code_cell(
        "# RISK: Snapshot depth\n"
        "snaps = q(\"\"\"\n"
        "SELECT date, count(*) AS slices, sum(ad_count) AS total_ads\n"
        "FROM analytics_dailyinventorysnapshot GROUP BY date ORDER BY date\n"
        "\"\"\")\n"
        "display(Markdown('### Daily snapshot coverage'))\n"
        "display(snaps)\n"
        "display(Markdown(f'> **Risk**: Only {len(snaps)} date(s). Trend charts will be empty/flat.'))\n"
    ),

    text_cell("## 9. E2E Ad Trace\n"),
    code_cell(
        "# Pick an ad with price history\n"
        "sample = q(\"\"\"\n"
        "WITH s AS (SELECT ad_id FROM market_priceobservation GROUP BY ad_id HAVING count(*)>=2 LIMIT 1)\n"
        "SELECT a.code, a.title, a.brand_id, a.model_id, a.year_jalali, a.mileage,\n"
        "  a.current_price, a.price_type, a.status, a.quality_flags::text\n"
        "FROM catalog_ad a JOIN s ON a.code=s.ad_id\n"
        "\"\"\")\n"
        "display(Markdown('### Ad snapshot'))\n"
        "display(sample)\n\n"
        "code = sample['code'].iloc[0]\n"
        "versions = q(f\"SELECT id,ad_id,semantic_hash,origin,first_observed_at FROM history_adversion WHERE ad_id='{code}' ORDER BY first_observed_at\")\n"
        "display(Markdown('### Versions')); display(versions)\n\n"
        "prices = q(f\"SELECT ad_id,price,price_type,observed_at FROM market_priceobservation WHERE ad_id='{code}' ORDER BY observed_at\")\n"
        "display(Markdown('### Price history')); display(prices)\n\n"
        "changes = q(f\"SELECT ad_id,event_type,categories::text,changed_paths::text,created_at FROM history_adchangeevent WHERE ad_id='{code}' ORDER BY created_at\")\n"
        "display(Markdown('### Change events')); display(changes)\n"
    ),

    text_cell("## 10. Summary\n\n"
              "See the full audit report artifact for findings, risk assessment, and rollout recommendation.\n"),
]

nb = {
    "nbformat": 4, "nbformat_minor": 5,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.12.0"},
    },
    "cells": cells,
}

out = Path(__file__).parent / "bama_saas_readiness_audit.ipynb"
out.write_text(json.dumps(nb, indent=1, ensure_ascii=False))
print(f"Wrote {out} ({out.stat().st_size:,} bytes)")
