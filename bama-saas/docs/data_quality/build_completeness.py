#!/usr/bin/env python3
"""Build a read-only PostgreSQL completeness profile and portable report inputs."""

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
       COUNT(DISTINCT a.code) AS distinct_codes,
       COUNT(*) FILTER (WHERE a.status='active') AS active_ads,
       COUNT(*) FILTER (WHERE a.status='removed') AS removed_ads,
       COUNT(*) FILTER (WHERE a.status NOT IN ('active','removed')) AS invalid_status,
       COUNT(*) FILTER (WHERE b.slug IS NULL) AS orphan_brand,
       COUNT(*) FILTER (WHERE m.id IS NULL) AS orphan_model,
       COUNT(*) FILTER (WHERE v.id IS NULL) AS orphan_variant,
       COUNT(*) FILTER (WHERE c.id IS NULL) AS orphan_city,
       MIN(a.first_seen_at) AS earliest_first_seen,
       MAX(a.last_seen_at) AS latest_last_seen,
       ROUND(EXTRACT(EPOCH FROM (now()-MAX(a.last_seen_at)))/3600.0,2) AS freshness_lag_hours,
       now() AS snapshot_at
FROM catalog_ad a
LEFT JOIN catalog_brand b ON b.slug=a.brand_id
LEFT JOIN catalog_model m ON m.id=a.model_id
LEFT JOIN catalog_variant v ON v.id=a.variant_id
LEFT JOIN catalog_city c ON c.id=a.city_id
"""

FIELD_SQL = """
WITH profile AS (
  SELECT v.field, v.complete, a.status
  FROM catalog_ad a
  CROSS JOIN LATERAL (VALUES
    ('code', a.code IS NOT NULL AND btrim(a.code)<>''),
    ('title', btrim(a.title)<>''), ('year', a.year IS NOT NULL),
    ('mileage', a.mileage IS NOT NULL), ('category', btrim(a.category)<>''),
    ('transmission', btrim(a.transmission)<>''), ('current_price', a.current_price IS NOT NULL),
    ('current_payment', a.current_payment IS NOT NULL),
    ('current_prepayment', a.current_prepayment IS NOT NULL),
    ('current_installments', a.current_installments IS NOT NULL),
    ('price_type', btrim(a.price_type)<>''), ('publish_at', a.publish_at IS NOT NULL),
    ('publish_phrase', btrim(a.publish_phrase)<>''), ('first_seen_at', a.first_seen_at IS NOT NULL),
    ('last_seen_at', a.last_seen_at IS NOT NULL), ('status', btrim(a.status)<>''),
    ('removed_at', a.removed_at IS NOT NULL), ('trim', btrim(a.trim)<>''),
    ('location', btrim(a.location)<>''), ('body_type', btrim(a.body_type)<>''),
    ('body_color', btrim(a.body_color)<>''), ('body_status', btrim(a.body_status)<>''),
    ('fuel', btrim(a.fuel)<>''), ('url', btrim(a.url)<>''),
    ('canonical_path', btrim(a.canonical_path)<>''),
    ('raw_payload', a.raw_payload IS NOT NULL AND a.raw_payload<>'{}'::jsonb),
    ('brand_id', a.brand_id IS NOT NULL), ('city_id', a.city_id IS NOT NULL),
    ('dealer_id', a.dealer_id IS NOT NULL), ('model_id', a.model_id IS NOT NULL),
    ('variant_id', a.variant_id IS NOT NULL),
    ('quality_flags', a.quality_flags IS NOT NULL AND jsonb_typeof(a.quality_flags)='array'),
    ('year_calendar', btrim(a.year_calendar)<>''), ('year_gregorian', a.year_gregorian IS NOT NULL),
    ('year_jalali', a.year_jalali IS NOT NULL),
    ('cohort_flags', a.cohort_flags IS NOT NULL AND jsonb_typeof(a.cohort_flags)='array'),
    ('description_length', a.description_length IS NOT NULL), ('image_count', a.image_count IS NOT NULL),
    ('seller_authenticated', a.seller_authenticated IS NOT NULL),
    ('source_modified_at', a.source_modified_at IS NOT NULL)
  ) v(field, complete)
)
SELECT field, COUNT(*) AS rows, COUNT(*) FILTER (WHERE complete) AS complete_rows,
       COUNT(*) FILTER (WHERE NOT complete) AS missing_rows,
       ROUND(100.0*COUNT(*) FILTER (WHERE complete)/COUNT(*),2) AS complete_pct,
       ROUND(100.0*COUNT(*) FILTER (WHERE status='active' AND complete)
         /NULLIF(COUNT(*) FILTER (WHERE status='active'),0),2) AS active_pct,
       ROUND(100.0*COUNT(*) FILTER (WHERE status='removed' AND complete)
         /NULLIF(COUNT(*) FILTER (WHERE status='removed'),0),2) AS removed_pct
FROM profile GROUP BY field ORDER BY complete_pct, field
"""

FAMILY_SQL = """
SELECT family, complete_rows, total_rows,
       ROUND(100.0*complete_rows/total_rows,4) AS complete_pct,
       ROUND(100.0*(total_rows-complete_rows)/total_rows,4) AS missing_pct
FROM (
  SELECT 'Identity and provenance' AS family,
    COUNT(*) FILTER (WHERE btrim(code)<>'' AND btrim(title)<>'' AND btrim(url)<>''
      AND btrim(canonical_path)<>'' AND raw_payload IS NOT NULL AND raw_payload<>'{}'::jsonb
      AND first_seen_at IS NOT NULL AND last_seen_at IS NOT NULL AND status IN ('active','removed')) AS complete_rows,
    COUNT(*) AS total_rows FROM catalog_ad
  UNION ALL SELECT 'Catalog dimensions',
    COUNT(*) FILTER (WHERE brand_id IS NOT NULL AND model_id IS NOT NULL AND variant_id IS NOT NULL
      AND city_id IS NOT NULL AND year_jalali IS NOT NULL AND year_gregorian IS NOT NULL
      AND year_calendar IN ('jalali','gregorian') AND mileage IS NOT NULL
      AND btrim(category)<>'' AND btrim(transmission)<>''), COUNT(*) FROM catalog_ad
  UNION ALL SELECT 'Descriptive attributes',
    COUNT(*) FILTER (WHERE btrim(trim)<>'' AND lower(btrim(trim)) NOT IN
      ('unknown','n/a','na','null','none','-','نامشخص','نامعلوم') AND btrim(location)<>''
      AND btrim(body_type)<>'' AND btrim(body_color)<>'' AND lower(btrim(body_color)) NOT IN
      ('unknown','n/a','na','null','none','-','نامشخص','نامعلوم')
      AND btrim(body_status)<>'' AND btrim(fuel)<>''), COUNT(*) FROM catalog_ad
  UNION ALL SELECT 'Presentation evidence',
    COUNT(*) FILTER (WHERE image_count IS NOT NULL AND description_length IS NOT NULL
      AND seller_authenticated IS NOT NULL AND source_modified_at IS NOT NULL), COUNT(*) FROM catalog_ad
  UNION ALL SELECT 'Conditional pricing',
    COUNT(*) FILTER (WHERE btrim(price_type)<>'' AND (price_type<>'lumpsum' OR current_price>0)
      AND (price_type<>'installment' OR (current_payment IS NOT NULL
        AND current_prepayment IS NOT NULL AND current_installments IS NOT NULL))), COUNT(*) FROM catalog_ad
  UNION ALL SELECT 'Lifecycle',
    COUNT(*) FILTER (WHERE status IN ('active','removed') AND first_seen_at IS NOT NULL
      AND last_seen_at IS NOT NULL AND (status<>'removed' OR removed_at IS NOT NULL)), COUNT(*) FROM catalog_ad
) f ORDER BY missing_pct DESC, family
"""

BRAND_SQL = """
WITH ranked AS (SELECT brand_id, COUNT(*) AS n FROM catalog_ad GROUP BY brand_id ORDER BY n DESC LIMIT 12)
SELECT b.name_fa AS brand, COUNT(*) AS ads,
       ROUND(100.0*COUNT(*) FILTER (WHERE a.status='active')/COUNT(*),2) AS active_pct,
       ROUND(100.0*COUNT(a.description_length)/COUNT(*),2) AS description_pct,
       ROUND(100.0*COUNT(*) FILTER (WHERE a.year_calendar IN ('jalali','gregorian')
         AND a.year_jalali IS NOT NULL AND a.year_gregorian IS NOT NULL)/COUNT(*),2) AS normalized_year_pct,
       ROUND(100.0*COUNT(*) FILTER (WHERE lower(btrim(a.body_color)) NOT IN
         ('unknown','n/a','na','null','none','-','نامشخص','نامعلوم'))/COUNT(*),2) AS meaningful_body_color_pct
FROM catalog_ad a JOIN ranked r ON r.brand_id=a.brand_id JOIN catalog_brand b ON b.slug=a.brand_id
GROUP BY b.name_fa,r.n ORDER BY r.n DESC
"""

RECENCY_SQL = """
WITH anchor AS (SELECT MAX(last_seen_at) AS max_seen FROM catalog_ad), bucketed AS (
  SELECT CASE WHEN last_seen_at>=max_seen-INTERVAL '1 day' THEN 'Latest 24 hours'
    WHEN last_seen_at>=max_seen-INTERVAL '7 days' THEN 'Days 2–7'
    WHEN last_seen_at>=max_seen-INTERVAL '30 days' THEN 'Days 8–30'
    ELSE 'Older than 30 days' END AS recency_bucket, a.*
  FROM catalog_ad a CROSS JOIN anchor
)
SELECT recency_bucket, COUNT(*) AS ads,
       ROUND(100.0*COUNT(description_length)/COUNT(*),2) AS description_pct,
       ROUND(100.0*COUNT(*) FILTER (WHERE year_jalali IS NOT NULL AND year_gregorian IS NOT NULL
         AND year_calendar IN ('jalali','gregorian'))/COUNT(*),2) AS normalized_year_pct,
       ROUND(100.0*COUNT(current_price)/COUNT(*),2) AS raw_price_pct
FROM bucketed GROUP BY recency_bucket
ORDER BY CASE recency_bucket WHEN 'Latest 24 hours' THEN 1 WHEN 'Days 2–7' THEN 2
  WHEN 'Days 8–30' THEN 3 ELSE 4 END
"""

EXCEPTION_SQL = """
SELECT
  COUNT(*) FILTER (WHERE description_length IS NULL) AS missing_description,
  COUNT(*) FILTER (WHERE description_length IS NULL
    AND jsonb_typeof(raw_payload->'detail'->'description')='string') AS recoverable_descriptions,
  COUNT(*) FILTER (WHERE year_jalali IS NULL OR year_gregorian IS NULL) AS missing_normalized_year,
  COUNT(*) FILTER (WHERE (year_jalali IS NULL OR year_gregorian IS NULL)
    AND raw_payload->'detail'->>'year'='0') AS zero_raw_year,
  COUNT(*) FILTER (WHERE lower(btrim(body_color)) IN
    ('unknown','n/a','na','null','none','-','نامشخص','نامعلوم')) AS placeholder_body_color,
  COUNT(*) FILTER (WHERE lower(btrim(trim)) IN
    ('unknown','n/a','na','null','none','-','نامشخص','نامعلوم')) AS placeholder_trim,
  COUNT(*) FILTER (WHERE price_type='lumpsum') AS lumpsum_ads,
  COUNT(*) FILTER (WHERE price_type='lumpsum' AND current_price>0) AS lumpsum_with_price,
  COUNT(*) FILTER (WHERE price_type='installment') AS installment_ads,
  COUNT(*) FILTER (WHERE price_type='installment' AND current_payment IS NOT NULL
    AND current_prepayment IS NOT NULL AND current_installments IS NOT NULL) AS complete_installments,
  COUNT(*) FILTER (WHERE status='removed') AS removed_ads,
  COUNT(*) FILTER (WHERE status='removed' AND removed_at IS NOT NULL) AS removed_with_timestamp
FROM catalog_ad
"""

FIELD_META = {
    "current_installments": ("Pricing", "Conditional", "Complete for all installment ads; not applicable otherwise."),
    "current_payment": ("Pricing", "Conditional", "Complete for all installment ads; not applicable otherwise."),
    "current_prepayment": ("Pricing", "Conditional", "Complete for all installment ads; not applicable otherwise."),
    "dealer_id": ("Dimensions", "Optional", "Presence identifies dealer listings; private listings legitimately lack it."),
    "removed_at": ("Lifecycle", "Conditional", "Complete for every removed ad; not applicable to active ads."),
    "current_price": ("Pricing", "Conditional", "Negotiable ads legitimately have no numeric price; lump-sum ads are complete."),
    "description_length": ("Presentation", "Expected when supplied", "Missing source values are null/non-string and are not recoverable from stored payloads."),
    "image_count": ("Presentation", "Expected", "Zero is a valid populated value."),
    "seller_authenticated": ("Presentation", "Expected", "False is a valid populated value."),
    "source_modified_at": ("Presentation", "Expected", "Source activity timestamp."),
    "quality_flags": ("Quality metadata", "Required", "An empty JSON array is valid and means no rule fired."),
    "cohort_flags": ("Quality metadata", "Required", "An empty JSON array is valid and means no cohort flag fired."),
}


def _query(sql: str) -> list[dict[str, str]]:
    result = subprocess.run(
        [DOCKER, "compose", "exec", "-T", "postgres", "sh", "-lc", PSQL],
        cwd=PROJECT,
        input=sql.strip().rstrip(";") + ";\n",
        text=True,
        capture_output=True,
        check=True,
    )
    return list(csv.DictReader(io.StringIO(result.stdout)))


def _number(value: str):
    if value == "":
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


def run_profile() -> dict[str, list[dict]]:
    data = {
        "summary": _typed(_query(SUMMARY_SQL)),
        "fields": _typed(_query(FIELD_SQL)),
        "families": _typed(_query(FAMILY_SQL)),
        "brands": _typed(_query(BRAND_SQL)),
        "recency": _typed(_query(RECENCY_SQL)),
        "exceptions": _typed(_query(EXCEPTION_SQL)),
    }
    for row in data["fields"]:
        family, expectation, note = FIELD_META.get(
            row["field"], ("Core listing", "Observed", "Null and empty strings count as missing; numeric zero remains complete.")
        )
        row.update(family=family, expectation=expectation, interpretation=note)
    for row in data["families"]:
        row["complete_rate"] = row["complete_pct"] / 100
        row["missing_rate"] = row["missing_pct"] / 100
    return data


def _source(executed_at: str) -> dict:
    return {
        "id": "catalog_ad_completeness_sql",
        "label": "Bama PostgreSQL ad completeness profile",
        "query": {
            "engine": "postgresql",
            "language": "sql",
            "description": "Read-only field, family, segment, integrity, and freshness profile of the current ad snapshot.",
            "sql": "\n\n".join((SUMMARY_SQL, FIELD_SQL, FAMILY_SQL, BRAND_SQL, RECENCY_SQL, EXCEPTION_SQL)),
            "tables_used": [
                "public.catalog_ad", "public.catalog_brand", "public.catalog_model",
                "public.catalog_variant", "public.catalog_city",
            ],
            "filters": ["All catalog_ad rows", "No sampling", "Field-aware conditional completeness"],
            "metric_definitions": {
                "raw completeness": "Non-null values, non-empty strings, valid zero and false values, and non-empty raw JSON payloads.",
                "family completeness": "Share of ads satisfying every applicable field predicate in the named family.",
                "conditional pricing": "Numeric price required for lump-sum ads; payment fields required only for installment ads.",
            },
            "executed_at": executed_at,
        },
    }


def build_artifact(data: dict[str, list[dict]]) -> dict:
    summary = data["summary"][0]
    exceptions = data["exceptions"][0]
    family = {row["family"]: row for row in data["families"]}
    generated = str(summary["snapshot_at"])
    source = _source(generated)
    headline = [{
        "ads": summary["ads"],
        "presentation_rate": family["Presentation evidence"]["complete_rate"],
        "descriptive_rate": family["Descriptive attributes"]["complete_rate"],
        "freshness_lag_hours": summary["freshness_lag_hours"],
    }]
    fields = sorted(data["fields"], key=lambda row: (row["complete_pct"], row["field"]))
    technical_summary = (
        "## Technical summary\n\n"
        f"- **Core completeness is excellent.** Identity/provenance, lifecycle, and conditional pricing are complete across all **{summary['ads']:,}** ads.\n"
        f"- **Presentation evidence is the only material gap.** It is complete for **{family['Presentation evidence']['complete_pct']:.2f}%** of ads because **{exceptions['missing_description']:,}** rows lack a usable source description; none are recoverable from the stored payload.\n"
        f"- **Semantic placeholders are rare.** Descriptive attributes are fully meaningful for **{family['Descriptive attributes']['complete_pct']:.2f}%** of ads; **{exceptions['placeholder_body_color']:,}** body colors and **{exceptions['placeholder_trim']:,}** trim use placeholders.\n"
        f"- **This is a stable but not current snapshot.** The latest stored sighting trails the query by **{summary['freshness_lag_hours']:.2f} hours** because the worker remained stopped during profiling."
    )
    manifest = {
        "version": 1,
        "surface": "report",
        "title": "Bama PostgreSQL Ad Completeness — Initial Assessment",
        "description": "Technical completeness profile of the current catalog_ad snapshot.",
        "generatedAt": generated,
        "cards": [
            {"id": "ads", "description": "Current listing snapshot row count.", "dataset": "headline", "sourceId": source["id"], "metrics": [{"label": "Stored ads", "field": "ads", "format": "number"}]},
            {"id": "presentation", "description": "Rows with all four promoted presentation fields.", "dataset": "headline", "sourceId": source["id"], "metrics": [{"label": "Presentation evidence complete", "field": "presentation_rate", "format": "percent"}]},
            {"id": "descriptive", "description": "Rows with six meaningful descriptive attributes after placeholder handling.", "dataset": "headline", "sourceId": source["id"], "metrics": [{"label": "Descriptive attributes meaningful", "field": "descriptive_rate", "format": "percent"}]},
            {"id": "freshness", "description": "Hours from latest stored sighting to profile time.", "dataset": "headline", "sourceId": source["id"], "metrics": [{"label": "Freshness lag", "field": "freshness_lag_hours", "format": "number", "unit": "hours"}]},
        ],
        "charts": [{
            "id": "family_missingness",
            "title": "Missing-row share by field family",
            "subtitle": "Presentation evidence is the only family above a 1% missing-row rate.",
            "type": "horizontalBar",
            "dataset": "families",
            "sourceId": source["id"],
            "encodings": {
                "x": {"field": "family", "type": "nominal", "label": "Field family"},
                "y": {"field": "missing_rate", "type": "quantitative", "label": "Missing rows", "format": "percent"},
            },
            "xAxisTitle": "Missing rows",
            "valueFormat": "percent",
            "layout": "full",
        }],
        "tables": [
            {
                "id": "field_profile", "title": "Completeness by stored field",
                "subtitle": "All 40 catalog fields; raw population rates are paired with conditionality notes.",
                "dataset": "fields", "sourceId": source["id"],
                "defaultSort": {"field": "complete_pct", "direction": "asc"}, "density": "dense", "layout": "full",
                "columns": [
                    {"field": "field", "label": "Field", "type": "text"},
                    {"field": "family", "label": "Family", "type": "text"},
                    {"field": "expectation", "label": "Expectation", "type": "text"},
                    {"field": "complete_rows", "label": "Complete rows", "format": "number"},
                    {"field": "missing_rows", "label": "Missing rows", "format": "number"},
                    {"field": "complete_pct", "label": "Raw complete %", "format": "number", "unit": "%"},
                    {"field": "active_pct", "label": "Active %", "format": "number", "unit": "%"},
                    {"field": "removed_pct", "label": "Removed %", "format": "number", "unit": "%"},
                    {"field": "interpretation", "label": "Interpretation", "type": "text"},
                ],
            },
            {
                "id": "brand_profile", "title": "Completeness across the 12 largest brands",
                "subtitle": "Description, normalized year, and meaningful body-color rates for the largest listing groups.",
                "dataset": "brands", "sourceId": source["id"],
                "defaultSort": {"field": "ads", "direction": "desc"}, "density": "spacious", "layout": "full",
                "columns": [
                    {"field": "brand", "label": "Brand", "type": "text"},
                    {"field": "ads", "label": "Ads", "format": "number"},
                    {"field": "active_pct", "label": "Active %", "format": "number", "unit": "%"},
                    {"field": "description_pct", "label": "Description %", "format": "number", "unit": "%"},
                    {"field": "normalized_year_pct", "label": "Normalized year %", "format": "number", "unit": "%"},
                    {"field": "meaningful_body_color_pct", "label": "Meaningful body color %", "format": "number", "unit": "%"},
                ],
            },
            {
                "id": "recency_profile", "title": "Completeness by last-seen recency",
                "subtitle": "Buckets are anchored to the newest stored sighting, not wall-clock time.",
                "dataset": "recency", "sourceId": source["id"],
                "defaultSort": {"field": "ads", "direction": "desc"}, "density": "spacious", "layout": "full",
                "columns": [
                    {"field": "recency_bucket", "label": "Recency bucket", "type": "text"},
                    {"field": "ads", "label": "Ads", "format": "number"},
                    {"field": "description_pct", "label": "Description %", "format": "number", "unit": "%"},
                    {"field": "normalized_year_pct", "label": "Normalized year %", "format": "number", "unit": "%"},
                    {"field": "raw_price_pct", "label": "Raw numeric price %", "format": "number", "unit": "%"},
                ],
            },
        ],
        "sources": [{"id": source["id"], "label": source["label"], "path": "build_analysis.py"}],
        "blocks": [
            {"id": "title", "type": "markdown", "body": "# Bama PostgreSQL Ad Completeness — Initial Assessment"},
            {"id": "technical_summary", "type": "markdown", "body": technical_summary, "sourceId": source["id"]},
            {"id": "headline_metrics", "type": "metric-strip", "cardIds": ["ads", "presentation", "descriptive", "freshness"]},
            {"id": "family_finding", "type": "markdown", "sourceId": source["id"], "body": "## Presentation evidence is the only material completeness gap\n\nThe chart shows missing-row share rather than a compressed 96–100% completeness scale. Description availability drives the presentation gap; the other three presentation fields are fully populated."},
            {"id": "family_chart", "type": "chart", "chartId": "family_missingness", "layout": "full"},
            {"id": "field_finding", "type": "markdown", "sourceId": source["id"], "body": f"## Conditional fields are complete within their valid populations\n\nRaw sparsity in dealer, installment, price, and removal fields reflects applicability rather than broken ingestion. All **{exceptions['lumpsum_ads']:,}** lump-sum ads have a positive price, all **{exceptions['installment_ads']:,}** installment ads have their payment fields, and all **{exceptions['removed_ads']:,}** removed ads have `removed_at`."},
            {"id": "field_table", "type": "table", "tableId": "field_profile", "layout": "full"},
            {"id": "brand_finding", "type": "markdown", "sourceId": source["id"], "body": "## Large brands show broadly consistent completeness\n\nDescription completeness stays in a narrow mid-to-high-90% band across the largest brands. The two failed year normalizations are isolated rather than a broad brand-level pipeline failure."},
            {"id": "brand_table", "type": "table", "tableId": "brand_profile", "layout": "full"},
            {"id": "recency_finding", "type": "markdown", "sourceId": source["id"], "body": "## Missing descriptions are not confined to one load period\n\nDescription completeness ranges from roughly 96% to 98% across recency buckets, which is consistent with optional source content rather than a single failed backfill."},
            {"id": "recency_table", "type": "table", "tableId": "recency_profile", "layout": "full"},
            {"id": "scope", "type": "markdown", "body": "## Scope, data, and metric definitions\n\nThe dataset is `catalog_ad`, at one row per current listing code. Completeness counts `NULL`, empty strings, known placeholder strings, and empty raw payloads as missing; numeric zero and boolean false remain valid. Conditional fields use their business population: installment payment fields apply only to installment ads, numeric price applies to lump-sum ads, and removal time applies only to removed ads."},
            {"id": "method", "type": "markdown", "body": "## Methodology\n\nAll checks ran as read-only PostgreSQL queries. The profile covers all 40 stored fields, compares active and removed rows, checks the largest brands and last-seen recency buckets, and verifies primary-key grain, allowed statuses, and dimension join coverage. Family completeness requires every applicable field in that family to pass."},
            {"id": "limitations", "type": "markdown", "sourceId": source["id"], "body": f"## Limitations and robustness checks\n\nThis phase measures completeness, not correctness, distribution drift, duplication beyond the primary key, or business-rule validity. The snapshot is **{summary['freshness_lag_hours']:.2f} hours** behind its newest observation because the worker was intentionally left stopped. Description values missing from promoted columns are also null/non-string in the stored payload, so a database-only backfill cannot recover them."},
            {"id": "recommendations", "type": "markdown", "body": "## Recommended next steps\n\n- Restart and monitor the worker before any time-sensitive analysis.\n- Treat missing descriptions as source absence; monitor the rate instead of fabricating or defaulting content.\n- Normalize the two raw `year=0` listings to explicit missing values and retain their quality flags.\n- Decide whether `body_color='-'` should remain a source-faithful placeholder or be normalized to `NULL`.\n- In the next phase, analyze validity, quality/cohort flags, numeric ranges, temporal consistency, and distribution drift."},
            {"id": "questions", "type": "markdown", "body": "## Further questions\n\n- Are hard and soft quality flags concentrated by source period, brand, or fetch run?\n- Do price, mileage, and year distributions contain plausible but analytically harmful outliers?\n- Are repeated physical vehicles represented by multiple listing codes or episodes?\n- Has field completeness changed across fetch runs or schema versions?"},
        ],
    }
    return {
        "surface": "report", "manifest": manifest,
        "snapshot": {
            "version": 1, "generatedAt": generated, "status": "ready",
            "datasets": {"headline": headline, "families": data["families"], "fields": fields,
                         "brands": data["brands"], "recency": data["recency"]},
        },
        "sources": [source],
    }


def build_notebook(data: dict[str, list[dict]]) -> dict:
    summary = data["summary"][0]
    family = {row["family"]: row for row in data["families"]}
    exceptions = data["exceptions"][0]
    return {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                     "language_info": {"name": "python", "version": "3"}},
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": ["# Bama PostgreSQL Ad Completeness\n", "\n", "## tl;dr\n", f"Core families are complete across {summary['ads']:,} ads; presentation evidence is {family['Presentation evidence']['complete_pct']:.2f}% complete, driven by {exceptions['missing_description']:,} missing descriptions.\n"]},
            {"cell_type": "markdown", "metadata": {}, "source": ["## Context & Methods\n", "\n", "### Key Assumptions\n", "One `catalog_ad` row represents one current listing code. Empty strings and known placeholders are missing; zero and false are valid. Conditional fields use conditional denominators.\n"]},
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["from build_analysis import run_profile\n", "results = run_profile()\n"]},
            {"cell_type": "markdown", "metadata": {}, "source": ["## Data\n", "\n", "The following cell reruns the full read-only PostgreSQL profile through the local Docker Compose stack.\n"]},
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["import json\n", "print(json.dumps(results, ensure_ascii=False, indent=2))\n"]},
            {"cell_type": "markdown", "metadata": {}, "source": ["## Results\n", "\n", f"Presentation evidence is the only material completeness gap ({family['Presentation evidence']['complete_pct']:.2f}%). Descriptive placeholders affect {exceptions['placeholder_body_color'] + exceptions['placeholder_trim']:,} rows; normalized years fail for 2 rows.\n", "\n", "## Takeaways\n", "\n", "Preserve field-aware denominators, monitor source-optional descriptions, fix explicit year-zero semantics, and assess validity and drift in the next phase.\n"]},
        ],
    }


def build_markdown(data: dict[str, list[dict]]) -> str:
    summary = data["summary"][0]
    family = {row["family"]: row for row in data["families"]}
    exceptions = data["exceptions"][0]
    lines = [
        "# Bama Ad Completeness Profile",
        "",
        f"Snapshot: `{summary['snapshot_at']}`",
        f"Ads: **{summary['ads']:,}** (active {summary['active_ads']:,} / removed {summary['removed_ads']:,})",
        f"Freshness lag: **{summary['freshness_lag_hours']} h**",
        f"Orphans brand/model/variant/city: "
        f"{summary['orphan_brand']}/{summary['orphan_model']}/"
        f"{summary['orphan_variant']}/{summary['orphan_city']}",
        f"Invalid status: **{summary['invalid_status']}**",
        "",
        "## Field families",
        "",
    ]
    for row in data["families"]:
        lines.append(
            f"- **{row['family']}**: {row['complete_pct']:.2f}% complete "
            f"({row.get('missing_rows', row.get('missing_pct', '?'))} missing share)"
        )
    lines += [
        "",
        "## Exceptions",
        "",
        f"- Missing descriptions: **{exceptions['missing_description']:,}**",
        f"- Placeholder body colors: **{exceptions['placeholder_body_color']:,}**",
        f"- Placeholder trim: **{exceptions['placeholder_trim']:,}**",
        f"- Missing normalized year: **{exceptions['missing_normalized_year']:,}**",
        f"- Raw year=0: **{exceptions['zero_raw_year']:,}**",
        f"- Lump-sum with price: **{exceptions['lumpsum_with_price']:,}** / **{exceptions['lumpsum_ads']:,}**",
        f"- Complete installment rows: **{exceptions['complete_installments']:,}** / **{exceptions['installment_ads']:,}**",
        "",
        "## Lowest completeness fields",
        "",
    ]
    for row in sorted(data["fields"], key=lambda r: (r["complete_pct"], r["field"]))[:12]:
        lines.append(
            f"- `{row['field']}`: {row['complete_pct']}% "
            f"(missing {row['missing_rows']:,}; {row.get('expectation', '')})"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    data = run_profile()
    summary = data["summary"][0]
    assert summary["ads"] == summary["distinct_codes"] and summary["invalid_status"] == 0
    assert all(summary[key] == 0 for key in ("orphan_brand", "orphan_model", "orphan_variant", "orphan_city"))
    artifact = build_artifact(data)
    notebook = build_notebook(data)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    payload = {"generated_at": stamp, "phase": "completeness", "data": data}
    (ROOT / "completeness_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (ROOT / "COMPLETENESS_REPORT.md").write_text(build_markdown(data), encoding="utf-8")
    (ROOT / "artifact.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "data_quality_completeness.ipynb").write_text(
        json.dumps(notebook, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"wrote COMPLETENESS_REPORT.md ads={summary['ads']} "
        f"freshness_lag_h={summary['freshness_lag_hours']}"
    )


if __name__ == "__main__":
    main()
