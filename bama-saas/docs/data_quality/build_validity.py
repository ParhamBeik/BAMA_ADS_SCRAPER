#!/usr/bin/env python3
"""Read-only PostgreSQL validity / outlier / identity / drift profile (DQ Phase 2).

Complements the Codex completeness script. Queries the live Compose Postgres
container and writes JSON + Markdown summaries next to this file.
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

SUMMARY_SQL = """
SELECT COUNT(*) AS ads,
       COUNT(*) FILTER (WHERE status='active') AS active_ads,
       COUNT(*) FILTER (WHERE status='removed') AS removed_ads,
       COUNT(*) FILTER (WHERE status NOT IN ('active','removed')) AS invalid_status,
       COUNT(*) FILTER (WHERE b.slug IS NULL) AS orphan_brand,
       COUNT(*) FILTER (WHERE m.id IS NULL) AS orphan_model,
       COUNT(*) FILTER (WHERE v.id IS NULL) AS orphan_variant,
       COUNT(*) FILTER (WHERE c.id IS NULL) AS orphan_city,
       MAX(a.last_seen_at) AS latest_last_seen,
       ROUND(EXTRACT(EPOCH FROM (now()-MAX(a.last_seen_at)))/3600.0,2) AS freshness_lag_hours,
       now() AS snapshot_at
FROM catalog_ad a
LEFT JOIN catalog_brand b ON b.slug=a.brand_id
LEFT JOIN catalog_model m ON m.id=a.model_id
LEFT JOIN catalog_variant v ON v.id=a.variant_id
LEFT JOIN catalog_city c ON c.id=a.city_id
"""

SENTINEL_SQL = """
SELECT
  COUNT(*) FILTER (WHERE btrim(body_color) IN ('-','—','N/A','n/a','')) AS placeholder_body_color,
  COUNT(*) FILTER (WHERE btrim(trim) IN ('-','—','N/A','n/a','default')) AS placeholder_trim,
  COUNT(*) FILTER (WHERE year = 0) AS year_zero,
  COUNT(*) FILTER (WHERE year_jalali IS NULL AND year IS NOT NULL) AS year_unnormalized,
  COUNT(*) FILTER (WHERE mileage IS NOT NULL AND mileage < 0) AS negative_mileage,
  COUNT(*) FILTER (WHERE current_price IS NOT NULL AND current_price <= 0) AS nonpositive_price,
  COUNT(*) FILTER (WHERE current_price IS NOT NULL AND current_price < 10000000) AS price_below_band,
  COUNT(*) FILTER (WHERE current_price IS NOT NULL AND current_price > 100000000000) AS price_above_band,
  COUNT(*) FILTER (WHERE mileage IS NOT NULL AND mileage > 2000000) AS mileage_above_band
FROM catalog_ad
"""

QUALITY_FLAGS_SQL = """
SELECT COALESCE(flag, '(none)') AS flag, COUNT(*) AS ads
FROM catalog_ad a
LEFT JOIN LATERAL jsonb_array_elements_text(
  CASE WHEN jsonb_typeof(a.quality_flags)='array' THEN a.quality_flags ELSE '[]'::jsonb END
) AS flag ON TRUE
GROUP BY 1
ORDER BY ads DESC, flag
"""

COHORT_FLAGS_SQL = """
SELECT COALESCE(flag, '(none)') AS flag, COUNT(*) AS ads
FROM catalog_ad a
LEFT JOIN LATERAL jsonb_array_elements_text(
  CASE WHEN jsonb_typeof(a.cohort_flags)='array' THEN a.cohort_flags ELSE '[]'::jsonb END
) AS flag ON TRUE
GROUP BY 1
ORDER BY ads DESC, flag
"""

IDENTITY_SQL = """
SELECT
  (SELECT COUNT(*) FROM history_vehicleidentity) AS identities,
  (SELECT COUNT(*) FROM history_listingepisode) AS episodes,
  (SELECT COUNT(*) FROM (
      SELECT identity_id FROM history_listingepisode
      WHERE identity_id IS NOT NULL
      GROUP BY identity_id HAVING COUNT(DISTINCT ad_id) > 1
  ) t) AS multi_code_identities,
  (SELECT COALESCE(SUM(cnt),0) FROM (
      SELECT identity_id, COUNT(DISTINCT ad_id) AS cnt FROM history_listingepisode
      WHERE identity_id IS NOT NULL
      GROUP BY identity_id HAVING COUNT(DISTINCT ad_id) > 1
  ) u) AS codes_in_multi_identities
"""

REJECTS_SQL = """
SELECT rule, COUNT(*) AS rejects
FROM history_ingestreject
GROUP BY 1
ORDER BY rejects DESC, rule
LIMIT 20
"""

DRIFT_SQL = """
SELECT date AS snapshot_date,
       active_ads,
       rejects_today,
       unconfirmed_brands,
       unconfirmed_models,
       price_median,
       null_rates->>'year_jalali' AS null_rate_year_jalali,
       null_rates->>'mileage' AS null_rate_mileage,
       null_rates->>'current_price' AS null_rate_current_price,
       null_rates->>'city_id' AS null_rate_city_id,
       distinct_counts->>'body_color' AS distinct_body_color,
       distinct_counts->>'transmission' AS distinct_transmission,
       distinct_counts->>'fuel' AS distinct_fuel,
       jsonb_array_length(COALESCE(alarms, '[]'::jsonb)) AS alarm_count
FROM history_dataqualitysnapshot
ORDER BY date DESC
LIMIT 21
"""

PRICE_OUTLIER_SQL = """
WITH priced AS (
  SELECT current_price
  FROM catalog_ad
  WHERE status='active' AND current_price IS NOT NULL AND current_price > 0
),
stats AS (
  SELECT
    percentile_cont(0.5) WITHIN GROUP (ORDER BY current_price) AS median_price,
    percentile_cont(0.01) WITHIN GROUP (ORDER BY current_price) AS p01,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY current_price) AS p99,
    COUNT(*) AS priced_active
  FROM priced
)
SELECT * FROM stats
"""


def _query(sql: str) -> list[dict[str, str]]:
    cmd = [
        DOCKER, "compose", "exec", "-T", "postgres", "sh", "-lc",
        f"{PSQL} <<'SQL'\n{sql}\nSQL",
    ]
    proc = subprocess.run(
        cmd, cwd=PROJECT, check=True, capture_output=True, text=True,
    )
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


def _typed(rows: list[dict[str, str]]) -> list[dict]:
    return [{key: _number(value) for key, value in row.items()} for row in rows]


def run_profile() -> dict:
    return {
        "summary": _typed(_query(SUMMARY_SQL)),
        "sentinels": _typed(_query(SENTINEL_SQL)),
        "quality_flags": _typed(_query(QUALITY_FLAGS_SQL)),
        "cohort_flags": _typed(_query(COHORT_FLAGS_SQL)),
        "identity": _typed(_query(IDENTITY_SQL)),
        "rejects": _typed(_query(REJECTS_SQL)),
        "drift": _typed(_query(DRIFT_SQL)),
        "price_band": _typed(_query(PRICE_OUTLIER_SQL)),
    }


def build_markdown(data: dict) -> str:
    s = data["summary"][0]
    sent = data["sentinels"][0]
    ident = data["identity"][0]
    price = data["price_band"][0] if data["price_band"] else {}
    lines = [
        "# Bama Data Quality Phase 2 — Validity & Outliers",
        "",
        f"Snapshot: `{s['snapshot_at']}`",
        f"Ads: **{s['ads']:,}** (active {s['active_ads']:,} / removed {s['removed_ads']:,})",
        f"Freshness lag: **{s['freshness_lag_hours']} h**",
        f"Orphans (brand/model/variant/city): "
        f"{s['orphan_brand']}/{s['orphan_model']}/{s['orphan_variant']}/{s['orphan_city']}",
        f"Invalid status: **{s['invalid_status']}**",
        "",
        "## Semantic sentinels",
        "",
        f"- Placeholder body colors: **{sent['placeholder_body_color']:,}**",
        f"- Placeholder / default trim: **{sent['placeholder_trim']:,}**",
        f"- `year=0`: **{sent['year_zero']:,}**",
        f"- Year present but unnormalized: **{sent['year_unnormalized']:,}**",
        f"- Negative mileage: **{sent['negative_mileage']:,}**",
        f"- Non-positive price: **{sent['nonpositive_price']:,}**",
        f"- Price below verify band (<10M): **{sent['price_below_band']:,}**",
        f"- Price above verify band: **{sent['price_above_band']:,}**",
        f"- Mileage above verify band: **{sent['mileage_above_band']:,}**",
        "",
        "## Active price distribution",
        "",
        f"- Priced active ads: **{price.get('priced_active', 0):,}**",
        f"- Median / p01 / p99: "
        f"**{price.get('median_price')}** / **{price.get('p01')}** / **{price.get('p99')}**",
        "",
        "## Quality flags (soft verify)",
        "",
    ]
    for row in data["quality_flags"][:15]:
        lines.append(f"- `{row['flag']}`: {row['ads']:,}")
    lines += ["", "## Cohort flags", ""]
    for row in data["cohort_flags"][:15]:
        lines.append(f"- `{row['flag']}`: {row['ads']:,}")
    lines += [
        "",
        "## Vehicle identity / duplicate listings",
        "",
        f"- Identities: **{ident['identities']:,}**",
        f"- Listing episodes: **{ident['episodes']:,}**",
        f"- Identities spanning >1 ad code: **{ident['multi_code_identities']:,}**",
        f"- Ad codes in those identities: **{ident['codes_in_multi_identities']:,}**",
        "",
        "## Ingest rejects (hard quarantine)",
        "",
    ]
    if not data["rejects"]:
        lines.append("- (none)")
    else:
        for row in data["rejects"]:
            lines.append(f"- `{row['rule']}`: {row['rejects']:,}")
    lines += ["", "## Recent data-quality snapshots (drift inputs)", ""]
    if not data["drift"]:
        lines.append("- No `history_dataqualitysnapshot` rows yet.")
    else:
        lines.append(
            "| date | active | rejects | null year_j | null mileage | null price | "
            "median price | alarms | unconf brands | unconf models |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for row in data["drift"][:14]:
            lines.append(
                f"| {row['snapshot_date']} | {row['active_ads']} | "
                f"{row['rejects_today']} | {row['null_rate_year_jalali']} | "
                f"{row['null_rate_mileage']} | {row['null_rate_current_price']} | "
                f"{row['price_median']} | {row['alarm_count']} | "
                f"{row['unconfirmed_brands']} | {row['unconfirmed_models']} |"
            )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    data = run_profile()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    payload = {
        "generated_at": stamp,
        "phase": "validity_outliers_identity_drift",
        "data": data,
    }
    json_path = ROOT / "validity_report.json"
    md_path = ROOT / "VALIDITY_REPORT.md"
    json_path.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    md_path.write_text(build_markdown(data))
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    summary = data["summary"][0]
    print(
        f"ads={summary['ads']} freshness_lag_h={summary['freshness_lag_hours']} "
        f"orphans_city={summary['orphan_city']}"
    )


if __name__ == "__main__":
    main()
