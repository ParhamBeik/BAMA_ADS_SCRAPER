import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, engine
from app.models.schema import AuditRun, FetchRun, RunStatus
from app.services.audit import run_audit
from app.services.bama import iter_ads
from app.services.ingestion import ingest_payload

log = logging.getLogger(__name__)
FETCH_LOCK = 4_242_001
AUDIT_LOCK = 4_242_002


# ---------------------------------------------------------------------------
# Run state helpers
# ---------------------------------------------------------------------------

def recover_abandoned_runs() -> None:
    """Mark queued/running jobs failed after an API restart."""
    now = datetime.now(timezone.utc)
    with SessionLocal.begin() as db:
        for model in (FetchRun, AuditRun):
            db.execute(update(model).where(model.status.in_([RunStatus.queued, RunStatus.running])).values(
                status=RunStatus.failed, finished_at=now, error="API restarted before job completion"))


def has_active_run(db: Session, model: type[FetchRun] | type[AuditRun]) -> bool:
    return bool(db.scalar(select(model.id).where(model.status.in_([RunStatus.queued, RunStatus.running])).limit(1)))


# ---------------------------------------------------------------------------
# Background job executors
# ---------------------------------------------------------------------------

def execute_fetch(run_id: uuid.UUID) -> None:
    """Own the fetch advisory lock and ingest live Bama payloads into PostgreSQL."""
    settings = get_settings()
    with engine.connect() as connection:
        locked = bool(connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": FETCH_LOCK}))
        if not locked:
            _fail(FetchRun, run_id, "Another fetch owns the PostgreSQL advisory lock")
            return
        # The lock is session-scoped, but SELECT implicitly starts a transaction.
        # End that transaction so the ORM session owns and commits subsequent work.
        connection.commit()
        try:
            with Session(bind=connection, expire_on_commit=False) as db:
                run = db.get(FetchRun, run_id)
                if run is None:
                    return
                run.status, run.started_at = RunStatus.running, datetime.now(timezone.utc)
                db.commit()
                for payload in iter_ads(settings, run.max_ads, run.page_pause):
                    status = ingest_payload(db, payload, run.id)
                    if status:
                        run.fetched_count += 1
                        run.created_count += int(status.startswith("created"))
                        run.updated_count += int(status.startswith("updated"))
                        run.price_change_count += int(status.endswith(":price"))
                    # Keep long jobs durable without committing every row.
                    if run.fetched_count % 200 == 0:
                        db.commit()
                run.status, run.finished_at = RunStatus.succeeded, datetime.now(timezone.utc)
                db.commit()
        except Exception as exc:
            log.exception("Fetch run %s failed", run_id)
            _fail(FetchRun, run_id, str(exc))
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": FETCH_LOCK})


def execute_audit(run_id: uuid.UUID) -> None:
    """Run database health checks under a separate advisory lock."""
    with engine.connect() as connection:
        locked = bool(connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": AUDIT_LOCK}))
        if not locked:
            _fail(AuditRun, run_id, "Another audit owns the PostgreSQL advisory lock")
            return
        connection.commit()
        try:
            with Session(bind=connection, expire_on_commit=False) as db:
                run = db.get(AuditRun, run_id)
                if run is None:
                    return
                run.status, run.started_at = RunStatus.running, datetime.now(timezone.utc)
                db.commit()
                run.summary, run.report = run_audit(db, get_settings().stale_after_days)
                run.status, run.finished_at = RunStatus.succeeded, datetime.now(timezone.utc)
                db.commit()
        except Exception as exc:
            log.exception("Audit run %s failed", run_id)
            _fail(AuditRun, run_id, str(exc))
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": AUDIT_LOCK})


def _fail(model: type[FetchRun] | type[AuditRun], run_id: uuid.UUID, error: str) -> None:
    """Store a bounded error message on a failed background run."""
    with SessionLocal.begin() as db:
        run = db.get(model, run_id)
        if run:
            run.status, run.finished_at, run.error = RunStatus.failed, datetime.now(timezone.utc), error[:4000]
