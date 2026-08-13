#!/usr/bin/env python3
"""Measure analysis-ready rate against the field contract in the DQ plan.

Read-only PostgreSQL via Compose. Writes ANALYSIS_READY_METRICS.json next to
this file (markdown report is assembled separately as ANALYSIS_READY.md).
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]
DOCKER = "/Applications/Docker.app/Contents/Resources/bin/docker"
PSQL = 'PGOPTIONS="-c default_transaction_read_only=on" psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" --csv -q'

# Hard rule ids from apps/jobs/services/verify.py HARD_RULE_IDS
HARD = ("code_missing", "price_missing_for_lumpsum", "price_too_low", "brand_missing")

ANALYSIS_SQL = f"""
WITH hard AS (
  SELECT a.*,
    EXISTS (
      SELECT 1 FROM jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(a.quality_flags)='array' THEN a.quality_flags ELSE '[]'::jsonb END
      ) f(flag) WHERE f.flag IN {HARD!r}
    ) AS has_hard_flag,
    (a.brand_id IS NOT NULL AND a.model_id IS NOT NULL AND a.variant_id IS NOT NULL
      AND a.city_id IS NOT NULL) AS has_dims,
    (a.year_jalali IS NOT NULL) AS has_year_j,
    (a.mileage IS NOT NULL) AS has_mileage,
    (a.status IN ('active','removed')) AS status_ok,
    (a.publish_at IS NOT NULL OR a.first_seen_at IS NOT NULL) AS has_time,
    CASE
      WHEN a.price_type = 'lumpsum' THEN (a.current_price IS NOT NULL AND a.current_price > 0)
      WHEN a.price_type = 'installment' THEN (
        a.current_payment IS NOT NULL AND a.current_prepayment IS NOT NULL
        AND a.current_installments IS NOT NULL)
      WHEN a.price_type = 'negotiable' THEN TRUE
      WHEN btrim(COALESCE(a.price_type,'')) = '' THEN (a.current_price IS NOT NULL AND a.current_price > 0)
      ELSE TRUE
    END AS price_ok
  FROM catalog_ad a
),
scored AS (
  SELECT *,
    (NOT has_hard_flag AND has_dims AND has_year_j AND has_mileage
      AND status_ok AND has_time AND price_ok) AS analysis_ready
  FROM hard
)
SELECT
  COUNT(*) AS ads,
  COUNT(*) FILTER (WHERE status='active') AS active_ads,
  COUNT(*) FILTER (WHERE status='removed') AS removed_ads,
  COUNT(*) FILTER (WHERE NOT has_hard_flag) AS verified_count,
  COUNT(*) FILTER (WHERE analysis_ready) AS analysis_ready_count,
  COUNT(*) FILTER (WHERE status='active' AND analysis_ready) AS active_analysis_ready,
  COUNT(*) FILTER (WHERE status='active' AND price_type='lumpsum'
    AND current_price IS NOT NULL AND current_price > 0) AS active_lumpsum_priced,
  COUNT(*) FILTER (WHERE status='active' AND price_type='lumpsum'
    AND current_price IS NOT NULL AND current_price > 0 AND analysis_ready) AS active_lumpsum_priced_ready,
  COUNT(*) FILTER (WHERE has_hard_flag) AS fail_hard_flag,
  COUNT(*) FILTER (WHERE NOT has_dims) AS fail_dims,
  COUNT(*) FILTER (WHERE NOT has_year_j) AS fail_year_jalali,
  COUNT(*) FILTER (WHERE NOT has_mileage) AS fail_mileage,
  COUNT(*) FILTER (WHERE NOT status_ok) AS fail_status,
  COUNT(*) FILTER (WHERE NOT has_time) AS fail_time,
  COUNT(*) FILTER (WHERE NOT price_ok) AS fail_price,
  COUNT(*) FILTER (WHERE status='active' AND NOT analysis_ready) AS active_not_ready,
  ROUND(100.0*COUNT(*) FILTER (WHERE analysis_ready)/NULLIF(COUNT(*),0), 4) AS ready_pct_all,
  ROUND(100.0*COUNT(*) FILTER (WHERE status='active' AND analysis_ready)
    /NULLIF(COUNT(*) FILTER (WHERE status='active'),0), 4) AS ready_pct_active,
  ROUND(100.0*COUNT(*) FILTER (WHERE status='active' AND price_type='lumpsum'
      AND current_price IS NOT NULL AND current_price > 0 AND analysis_ready)
    /NULLIF(COUNT(*) FILTER (WHERE status='active' AND price_type='lumpsum'
      AND current_price IS NOT NULL AND current_price > 0),0), 4) AS ready_pct_active_lumpsum_priced,
  now() AS snapshot_at
FROM scored
"""

# Fix HARD tuple formatting for SQL IN clause
ANALYSIS_SQL = ANALYSIS_SQL.replace(repr(HARD), "(" + ", ".join(f"'{h}'" for h in HARD) + ")")

ACTIVE_FAIL_BREAKDOWN_SQL = f"""
WITH hard AS (
  SELECT a.*,
    EXISTS (
      SELECT 1 FROM jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(a.quality_flags)='array' THEN a.quality_flags ELSE '[]'::jsonb END
      ) f(flag) WHERE f.flag IN ({", ".join(f"'{h}'" for h in HARD)})
    ) AS has_hard_flag,
    (a.brand_id IS NOT NULL AND a.model_id IS NOT NULL AND a.variant_id IS NOT NULL
      AND a.city_id IS NOT NULL) AS has_dims,
    (a.year_jalali IS NOT NULL) AS has_year_j,
    (a.mileage IS NOT NULL) AS has_mileage,
    (a.status IN ('active','removed')) AS status_ok,
    (a.publish_at IS NOT NULL OR a.first_seen_at IS NOT NULL) AS has_time,
    CASE
      WHEN a.price_type = 'lumpsum' THEN (a.current_price IS NOT NULL AND a.current_price > 0)
      WHEN a.price_type = 'installment' THEN (
        a.current_payment IS NOT NULL AND a.current_prepayment IS NOT NULL
        AND a.current_installments IS NOT NULL)
      WHEN a.price_type = 'negotiable' THEN TRUE
      WHEN btrim(COALESCE(a.price_type,'')) = '' THEN (a.current_price IS NOT NULL AND a.current_price > 0)
      ELSE TRUE
    END AS price_ok
  FROM catalog_ad a
  WHERE a.status='active'
)
SELECT reason, COUNT(*) AS ads FROM (
  SELECT code,
    CASE
      WHEN has_hard_flag THEN 'hard_quality_flag'
      WHEN NOT has_dims THEN 'missing_dimensions'
      WHEN NOT has_year_j THEN 'missing_year_jalali'
      WHEN NOT has_mileage THEN 'missing_mileage'
      WHEN NOT status_ok THEN 'invalid_status'
      WHEN NOT has_time THEN 'missing_publish_or_first_seen'
      WHEN NOT price_ok THEN 'price_incomplete_for_type'
      ELSE NULL
    END AS reason
  FROM hard
) t
WHERE reason IS NOT NULL
GROUP BY 1
ORDER BY ads DESC, reason
"""


def _query(sql: str) -> list[dict[str, str]]:
    cmd = [
        DOCKER, "compose", "exec", "-T", "postgres", "sh", "-lc",
        f"{PSQL} <<'SQL'\n{sql}\nSQL",
    ]
    proc = subprocess.run(cmd, cwd=PROJECT, check=True, capture_output=True, text=True)
    return list(csv.DictReader(io.StringIO(proc.stdout)))


def _number(value: str):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _typed(rows):
    return [{k: _number(v) for k, v in row.items()} for row in rows]


def main() -> None:
    summary = _typed(_query(ANALYSIS_SQL))[0]
    breakdown = _typed(_query(ACTIVE_FAIL_BREAKDOWN_SQL))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    payload = {
        "generated_at": stamp,
        "phase": "analysis_ready",
        "hard_rule_ids": list(HARD),
        "success_bar": {
            "metric": "ready_pct_active_lumpsum_priced",
            "threshold_pct": 95.0,
            "value": summary["ready_pct_active_lumpsum_priced"],
            "pass": (summary["ready_pct_active_lumpsum_priced"] or 0) >= 95.0,
        },
        "summary": summary,
        "active_failure_reasons": breakdown,
    }
    out = ROOT / "ANALYSIS_READY_METRICS.json"
    out.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(json.dumps({
        "wrote": str(out),
        "ready_pct_active_lumpsum_priced": summary["ready_pct_active_lumpsum_priced"],
        "ready_pct_active": summary["ready_pct_active"],
        "pass_bar": payload["success_bar"]["pass"],
        "active_not_ready": summary["active_not_ready"],
    }, indent=2))


if __name__ == "__main__":
    main()
