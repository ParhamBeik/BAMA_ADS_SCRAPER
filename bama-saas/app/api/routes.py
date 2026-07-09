import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, status
from sqlalchemy import asc, desc, func, select, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models.schema import Ad, AdChangeEvent, AdObservation, AdVersion, AuditRun, FetchRun, PriceObservation
from app.schemas import AdPage, AdRead, AuditRunRead, FetchRequest, FetchRunRead, SortField, SortOrder
from app.services.jobs import execute_audit, execute_fetch, has_active_run

router = APIRouter()
Db = Annotated[Session, Depends(get_db)]


# ---------------------------------------------------------------------------
# Admin guard and operations endpoints
# ---------------------------------------------------------------------------

def require_admin(x_admin_key: Annotated[str | None, Header()] = None, settings: Settings = Depends(get_settings)) -> None:
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Key")


@router.get("/health", tags=["operations"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/db/health", tags=["operations"])
def db_health(db: Db) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/summary", tags=["operations"])
def summary(db: Db, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.stale_after_days)
    return {
        "ads": db.scalar(select(func.count()).select_from(Ad)) or 0,
        "priced_ads": db.scalar(select(func.count()).select_from(Ad).where(Ad.current_price.is_not(None))) or 0,
        "stale_ads": db.scalar(select(func.count()).select_from(Ad).where(Ad.last_seen_at < cutoff)) or 0,
        "price_unit": "Bama displayed unit",
    }


# ---------------------------------------------------------------------------
# Run tracking and admin job starts
# ---------------------------------------------------------------------------

@router.get("/fetch-runs", response_model=list[FetchRunRead], tags=["operations"])
def fetch_runs(db: Db, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)) -> list[FetchRun]:
    return list(db.scalars(select(FetchRun).order_by(FetchRun.queued_at.desc()).limit(limit).offset(offset)))


@router.get("/fetch-runs/{run_id}", response_model=FetchRunRead, tags=["operations"])
def fetch_run(run_id: uuid.UUID, db: Db) -> FetchRun:
    if not (run := db.get(FetchRun, run_id)):
        raise HTTPException(404, "Fetch run not found")
    return run


@router.get("/audit-runs/{run_id}", response_model=AuditRunRead, tags=["operations"])
def audit_run(run_id: uuid.UUID, db: Db) -> AuditRun:
    if not (run := db.get(AuditRun, run_id)):
        raise HTTPException(404, "Audit run not found")
    return run


@router.post("/admin/fetch/run", response_model=FetchRunRead, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(require_admin)], tags=["admin"])
def start_fetch(body: FetchRequest, background: BackgroundTasks, db: Db,
                settings: Settings = Depends(get_settings)) -> FetchRun:
    """Queue one live fetch job; the background task does the slow work."""
    if has_active_run(db, FetchRun):
        raise HTTPException(409, "A fetch run is already queued or running")
    max_ads = body.max_ads or settings.bama_max_ads
    if max_ads > settings.bama_max_ads:
        raise HTTPException(422, f"max_ads cannot exceed {settings.bama_max_ads}")
    run = FetchRun(max_ads=max_ads, page_pause=body.page_pause if body.page_pause is not None else settings.bama_page_pause)
    db.add(run)
    db.commit()
    db.refresh(run)
    background.add_task(execute_fetch, run.id)
    return run


@router.post("/admin/audit/run", response_model=AuditRunRead, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(require_admin)], tags=["admin"])
def start_audit(background: BackgroundTasks, db: Db) -> AuditRun:
    """Queue one DB audit job."""
    if has_active_run(db, AuditRun):
        raise HTTPException(409, "An audit run is already queued or running")
    run = AuditRun()
    db.add(run)
    db.commit()
    db.refresh(run)
    background.add_task(execute_audit, run.id)
    return run


# ---------------------------------------------------------------------------
# Catalog endpoints
# ---------------------------------------------------------------------------

@router.get("/ads", response_model=AdPage, tags=["catalog"])
def list_ads(db: Db, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
             brand: str | None = None, model: str | None = None, trim: str | None = None,
             year_min: int | None = None, year_max: int | None = None, location: str | None = None,
             price_min: int | None = Query(None, ge=0), price_max: int | None = Query(None, ge=0),
             mileage_max: int | None = Query(None, ge=0), publish_from: datetime | None = None,
             last_seen_from: datetime | None = None, sort: SortField = "last_seen_at", order: SortOrder = "desc") -> AdPage:
    """Search the current ad snapshot with simple filters and paging."""
    filters = []
    for column, value in ((Ad.brand, brand), (Ad.model, model), (Ad.trim, trim), (Ad.location, location)):
        if value is not None:
            filters.append(column == value)
    if year_min is not None:
        filters.append(Ad.year >= year_min)
    if year_max is not None:
        filters.append(Ad.year <= year_max)
    if price_min is not None:
        filters.append(Ad.current_price >= price_min)
    if price_max is not None:
        filters.append(Ad.current_price <= price_max)
    if mileage_max is not None:
        filters.append(Ad.mileage <= mileage_max)
    if publish_from is not None:
        filters.append(Ad.publish_at >= publish_from)
    if last_seen_from is not None:
        filters.append(Ad.last_seen_at >= last_seen_from)
    total = db.scalar(select(func.count()).select_from(Ad).where(*filters)) or 0
    sort_column = getattr(Ad, sort)
    ordering = asc(sort_column) if order == "asc" else desc(sort_column)
    rows = list(db.scalars(select(Ad).where(*filters).order_by(ordering.nullslast(), Ad.code)
                           .offset((page - 1) * page_size).limit(page_size)))
    return AdPage(items=[AdRead.model_validate(row) for row in rows], page=page, page_size=page_size, total=total)


@router.get("/ads/{code}", response_model=AdRead, tags=["catalog"])
def get_ad(code: str, db: Db) -> AdRead:
    if not (ad := db.get(Ad, code)):
        raise HTTPException(404, "Ad not found")
    result = AdRead.model_validate(ad)
    result.raw_payload = ad.raw_payload
    return result


@router.get("/brands", tags=["catalog"])
def brands(db: Db) -> list[dict[str, Any]]:
    rows = db.execute(select(Ad.brand, func.count()).where(Ad.brand.is_not(None)).group_by(Ad.brand).order_by(Ad.brand))
    return [{"brand": brand, "count": count} for brand, count in rows]


@router.get("/brands/{brand}/models", tags=["catalog"])
def models(brand: str, db: Db) -> list[dict[str, Any]]:
    rows = db.execute(select(Ad.model, func.count()).where(Ad.brand == brand, Ad.model.is_not(None)).group_by(Ad.model).order_by(Ad.model))
    return [{"model": model, "count": count} for model, count in rows]


# ---------------------------------------------------------------------------
# Market summary endpoints
# ---------------------------------------------------------------------------

def market_query():
    """Shared grouped market stats used by markets and liquidity."""
    return select(Ad.brand, Ad.model, Ad.trim, func.count().label("count"),
        func.max(Ad.last_seen_at).label("latest_seen_at"), func.min(Ad.current_price).label("min_price"),
        func.percentile_disc(0.5).within_group(Ad.current_price).label("median_price"),
        func.max(Ad.current_price).label("max_price")).where(Ad.current_price.is_not(None)).group_by(Ad.brand, Ad.model, Ad.trim)


@router.get("/markets", tags=["markets"])
def markets(db: Db, brand: str | None = None, model: str | None = None, limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    statement = market_query()
    if brand:
        statement = statement.where(Ad.brand == brand)
    if model:
        statement = statement.where(Ad.model == model)
    return [dict(row._mapping) for row in db.execute(statement.order_by(desc("count")).limit(limit))]


# ---------------------------------------------------------------------------
# History endpoints
# ---------------------------------------------------------------------------

@router.get("/ads/{code}/price-history", tags=["history"])
def price_history(code: str, db: Db) -> list[dict[str, Any]]:
    if db.get(Ad, code) is None:
        raise HTTPException(404, "Ad not found")
    rows = db.scalars(select(PriceObservation).where(PriceObservation.ad_code == code).order_by(PriceObservation.observed_at))
    return [{"observed_at": r.observed_at, "price": r.price, "payment": r.payment, "prepayment": r.prepayment,
             "installments": r.installments, "price_type": r.price_type} for r in rows]


@router.get("/ads/{code}/versions", tags=["history"])
def versions(code: str, db: Db) -> list[dict[str, Any]]:
    if db.get(Ad, code) is None:
        raise HTTPException(404, "Ad not found")
    rows = db.scalars(select(AdVersion).where(AdVersion.ad_code == code).order_by(AdVersion.first_observed_at, AdVersion.id))
    return [{
        "id": row.id,
        "semantic_hash": row.semantic_hash,
        "raw_hash": row.raw_hash,
        "origin": row.origin,
        "first_observed_at": row.first_observed_at,
        "payload": row.payload,
    } for row in rows]


@router.get("/ads/{code}/changes", tags=["history"])
def ad_changes(
    code: str,
    db: Db,
    category: str | None = None,
    origin: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[dict[str, Any]]:
    if db.get(Ad, code) is None:
        raise HTTPException(404, "Ad not found")
    return _change_rows(db, code=code, category=category, origin=origin, date_from=date_from, date_to=date_to)


@router.get("/changes", tags=["history"])
def changes(
    db: Db,
    code: str | None = None,
    category: str | None = None,
    origin: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    return _change_rows(
        db,
        code=code,
        category=category,
        origin=origin,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


def _change_rows(
    db: Session,
    *,
    code: str | None = None,
    category: str | None = None,
    origin: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Shared change-event query for per-ad and global history endpoints."""
    filters = []
    if code is not None:
        filters.append(AdChangeEvent.ad_code == code)
    if category is not None:
        filters.append(AdChangeEvent.categories.contains([category]))
    if origin is not None:
        filters.append(AdChangeEvent.origin == origin)
    if date_from is not None:
        filters.append(AdChangeEvent.created_at >= date_from)
    if date_to is not None:
        filters.append(AdChangeEvent.created_at <= date_to)
    rows = db.scalars(select(AdChangeEvent).where(*filters).order_by(AdChangeEvent.created_at.desc(), AdChangeEvent.id.desc()).limit(limit).offset(offset))
    return [{
        "id": row.id,
        "code": row.ad_code,
        "observation_id": row.observation_id,
        "previous_version_id": row.previous_version_id,
        "new_version_id": row.new_version_id,
        "event_type": row.event_type,
        "categories": row.categories,
        "changed_paths": row.changed_paths,
        "changes": row.changes,
        "origin": row.origin,
        "created_at": row.created_at,
    } for row in rows]


@router.get("/ads/{code}/timeline", tags=["history"])
def timeline(code: str, db: Db) -> list[dict[str, Any]]:
    """Merge sightings, changes, and price changes into one chronological list."""
    if db.get(Ad, code) is None:
        raise HTTPException(404, "Ad not found")
    seen = [{"type": "sighting", "at": r.observed_at, "fetch_run_id": str(r.fetch_run_id), "raw_hash": r.raw_hash,
             "version_id": r.version_id, "publish_phrase": r.publish_phrase, "rank": r.rank}
            for r in db.scalars(select(AdObservation).where(AdObservation.ad_code == code))]
    events = [{"type": r.event_type, "at": r.created_at, "origin": r.origin, "categories": r.categories,
               "changed_paths": r.changed_paths, "changes": r.changes, "previous_version_id": r.previous_version_id,
               "new_version_id": r.new_version_id}
              for r in db.scalars(select(AdChangeEvent).where(AdChangeEvent.ad_code == code))]
    prices = [{"type": "price_change", "at": r.observed_at, "price": r.price, "payment": r.payment}
              for r in db.scalars(select(PriceObservation).where(PriceObservation.ad_code == code))]
    return sorted(seen + events + prices, key=lambda item: item["at"])


# ---------------------------------------------------------------------------
# Insight endpoints
# ---------------------------------------------------------------------------

@router.get("/markets/{brand}/{model}/price-trends", tags=["markets"])
def price_trends(brand: str, model: str, db: Db, bucket: str = Query("week", pattern="^(day|week|month)$")) -> list[dict[str, Any]]:
    period = {"day": "day", "week": "week", "month": "month"}[bucket]
    rows = db.execute(select(func.date_trunc(period, PriceObservation.observed_at).label("period"), func.count(),
        func.percentile_disc(0.5).within_group(PriceObservation.price)).join(Ad, Ad.code == PriceObservation.ad_code)
        .where(Ad.brand == brand, Ad.model == model, PriceObservation.price.is_not(None)).group_by("period").order_by("period"))
    return [{"period": period, "count": count, "median_price": median} for period, count, median in rows]


@router.get("/insights/liquidity", tags=["insights"])
def liquidity(db: Db, brand: str | None = None, model: str | None = None) -> list[dict[str, Any]]:
    """Rank markets using volume, price dispersion, and recent activity."""
    statement = market_query()
    if brand:
        statement = statement.where(Ad.brand == brand)
    if model:
        statement = statement.where(Ad.model == model)
    result = []
    for row in db.execute(statement):
        m = row._mapping
        median = float(m["median_price"] or 0)
        dispersion = ((m["max_price"] or 0) - (m["min_price"] or 0)) / median if median else 1
        age_days = max(0.0, (datetime.now(timezone.utc) - m["latest_seen_at"]).total_seconds() / 86400)
        recency = 20 / (1 + age_days / 7)
        score = round(min(100.0, 15 * math.log1p(m["count"]) + 45 / (1 + dispersion) + recency), 2)
        result.append({**dict(m), "liquidity_score": score})
    return sorted(result, key=lambda row: row["liquidity_score"], reverse=True)


@router.get("/insights/undervalued", tags=["insights"])
def undervalued(db: Db, limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
    """Find ads priced below peers in the same brand/model/trim/year bucket."""
    rows = db.execute(text("""
      WITH medians AS (
        SELECT brand,model,trim,year,count(*) peer_count,
          percentile_disc(0.5) WITHIN GROUP (ORDER BY current_price) peer_median
        FROM ads WHERE current_price IS NOT NULL GROUP BY brand,model,trim,year
      ) SELECT a.code,a.title,a.brand,a.model,a.trim,a.year,a.current_price,m.peer_count,m.peer_median,
          round((m.peer_median-a.current_price)::numeric/NULLIF(m.peer_median,0)*100,2) discount_percent
        FROM ads a JOIN medians m ON a.brand IS NOT DISTINCT FROM m.brand
          AND a.model IS NOT DISTINCT FROM m.model AND a.trim IS NOT DISTINCT FROM m.trim
          AND a.year IS NOT DISTINCT FROM m.year
        WHERE m.peer_count >= 5 AND a.current_price < m.peer_median
        ORDER BY discount_percent DESC LIMIT :limit
    """), {"limit": limit})
    return [dict(row._mapping) for row in rows]


@router.get("/insights/market-depth", tags=["insights"])
def market_depth(db: Db, limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    rows = db.execute(select(Ad.brand, Ad.model, func.count().label("listing_count"),
        func.percentile_disc(0.25).within_group(Ad.current_price).label("p25_price"),
        func.percentile_disc(0.5).within_group(Ad.current_price).label("median_price"),
        func.percentile_disc(0.75).within_group(Ad.current_price).label("p75_price"))
        .where(Ad.current_price.is_not(None)).group_by(Ad.brand, Ad.model).order_by(desc("listing_count")).limit(limit))
    return [dict(row._mapping) for row in rows]
