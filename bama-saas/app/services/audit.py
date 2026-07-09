from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.schema import Ad, UnknownTimePhrase


# ---------------------------------------------------------------------------
# Database health checks
# ---------------------------------------------------------------------------

def run_audit(db: Session, stale_after_days: int) -> tuple[dict[str, int], dict[str, Any]]:
    """Return counts for the invariants the API depends on."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_after_days)
    checks = {
        "duplicate_codes": int(db.scalar(text("SELECT count(*) FROM (SELECT code FROM ads GROUP BY code HAVING count(*) > 1) x")) or 0),
        "missing_required_fields": int(db.scalar(select(func.count()).select_from(Ad).where(
            (Ad.raw_payload["detail"]["code"].astext.is_(None)) | (Ad.title.is_(None)))) or 0),
        "stale_ads": int(db.scalar(select(func.count()).select_from(Ad).where(Ad.last_seen_at < cutoff)) or 0),
        "unknown_time_phrases": int(db.scalar(select(func.count()).select_from(UnknownTimePhrase)) or 0),
        "null_publish_dates": int(db.scalar(select(func.count()).select_from(Ad).where(Ad.publish_at.is_(None))) or 0),
        "invalid_categories": int(db.scalar(select(func.count()).select_from(Ad).where(
            (Ad.category.is_(None)) | (~Ad.category.in_(["car", "motorcycle"])))) or 0),
        "orphan_observations": int(db.scalar(text("SELECT count(*) FROM ad_observations o LEFT JOIN ads a ON a.code=o.ad_code WHERE a.code IS NULL")) or 0),
        "orphan_prices": int(db.scalar(text("SELECT count(*) FROM price_observations p LEFT JOIN ads a ON a.code=p.ad_code WHERE a.code IS NULL")) or 0),
        "orphan_media": int(db.scalar(text("SELECT count(*) FROM ad_media m LEFT JOIN ads a ON a.code=m.ad_code WHERE a.code IS NULL")) or 0),
        "orphan_metadata": int(db.scalar(text("SELECT count(*) FROM ad_metadata m LEFT JOIN ads a ON a.code=m.ad_code WHERE a.code IS NULL")) or 0),
        "orphan_versions": int(db.scalar(text("SELECT count(*) FROM ad_versions v LEFT JOIN ads a ON a.code=v.ad_code WHERE a.code IS NULL")) or 0),
        "orphan_version_observations": int(db.scalar(text("SELECT count(*) FROM ad_observations o LEFT JOIN ad_versions v ON v.id=o.version_id WHERE v.id IS NULL")) or 0),
        "orphan_change_events": int(db.scalar(text("SELECT count(*) FROM ad_change_events e LEFT JOIN ad_observations o ON o.id=e.observation_id WHERE o.id IS NULL")) or 0),
        "duplicate_sightings": int(db.scalar(text("SELECT count(*) FROM (SELECT fetch_run_id,ad_code FROM ad_observations GROUP BY fetch_run_id,ad_code HAVING count(*) > 1) x")) or 0),
        "current_state_drift": int(db.scalar(text("""
            SELECT count(*) FROM ads a JOIN LATERAL (
              SELECT v.payload FROM ad_observations o JOIN ad_versions v ON v.id=o.version_id
              WHERE o.ad_code=a.code ORDER BY o.observed_at DESC,o.id DESC LIMIT 1
            ) latest ON true WHERE a.raw_payload IS DISTINCT FROM latest.payload
        """)) or 0),
        "broken_event_versions": int(db.scalar(text("""
            SELECT count(*) FROM ad_change_events e
            LEFT JOIN ad_versions nv ON nv.id=e.new_version_id
            LEFT JOIN ad_versions pv ON pv.id=e.previous_version_id
            WHERE nv.id IS NULL OR (e.previous_version_id IS NOT NULL AND pv.id IS NULL)
        """)) or 0),
        "unfinished_fetch_runs": int(db.scalar(text("SELECT count(*) FROM fetch_runs WHERE status IN ('queued','running')")) or 0),
        "price_cache_mismatches": int(db.scalar(text("""
            SELECT count(*) FROM ads a JOIN LATERAL (
              SELECT price, payment, prepayment, installments, price_type FROM price_observations
              WHERE ad_code=a.code ORDER BY observed_at DESC, id DESC LIMIT 1
            ) p ON true WHERE (a.current_price,a.current_payment,a.current_prepayment,a.current_installments,a.price_type)
              IS DISTINCT FROM (p.price,p.payment,p.prepayment,p.installments,p.price_type)
        """)) or 0),
    }
    return checks, {"generated_at": datetime.now(timezone.utc).isoformat(), "stale_cutoff": cutoff.isoformat(), "checks": checks}
