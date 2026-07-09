import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import get_settings
from app.services.jobs import recover_abandoned_runs


@asynccontextmanager
async def lifespan(_: FastAPI):
    recover_abandoned_runs()
    yield


settings = get_settings()
logging.basicConfig(level=settings.log_level)
app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Live Bama market ingestion and query API. Monetary values use Bama's displayed unit.",
    lifespan=lifespan,
)
app.include_router(router)
