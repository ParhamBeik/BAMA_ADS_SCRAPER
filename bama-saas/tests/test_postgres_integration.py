import os
import uuid
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models.schema import Ad, AdChangeEvent, AdObservation, AdVersion, FetchRun, PriceObservation
from app.services.ingestion import ingest_payload

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set")


def test_overlapping_runs_preserve_sightings_and_change_only_prices() -> None:
    url = os.environ["TEST_DATABASE_URL"]
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    payload = {"detail": {"code": f"test-{uuid.uuid4()}", "title": "برند، مدل", "brand_fa": "برند",
                          "year": "1400", "mileage": "10,000 km", "type": "car", "time": "دیروز"},
               "price": {"price": "100,000", "type": "lumpsum"}, "images": []}
    with Session(engine) as db:
        runs = [FetchRun(max_ads=1, page_pause=0) for _ in range(4)]
        db.add_all(runs); db.flush()
        ingest_payload(db, payload, runs[0].id, datetime.now(timezone.utc))
        ingest_payload(db, payload, runs[1].id, datetime.now(timezone.utc))
        changed = {**payload, "price": {"price": "90,000", "type": "lumpsum"}}
        ingest_payload(db, changed, runs[2].id, datetime.now(timezone.utc))
        ingest_payload(db, payload, runs[3].id, datetime.now(timezone.utc))
        code = payload["detail"]["code"]
        assert db.scalar(select(func.count()).select_from(Ad).where(Ad.code == code)) == 1
        assert db.scalar(select(func.count()).select_from(AdObservation).where(AdObservation.ad_code == code)) == 4
        assert db.scalar(select(func.count()).select_from(AdVersion).where(AdVersion.ad_code == code)) == 2
        assert db.scalar(select(func.count()).select_from(AdChangeEvent).where(AdChangeEvent.ad_code == code)) == 2
        assert db.scalar(select(func.count()).select_from(PriceObservation).where(PriceObservation.ad_code == code)) == 3
        db.rollback()
