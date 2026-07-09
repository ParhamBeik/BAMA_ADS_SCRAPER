import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.schema import RunStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorResponse(BaseModel):
    detail: str


class FetchRequest(BaseModel):
    max_ads: int | None = Field(default=None, ge=1)
    page_pause: float | None = Field(default=None, ge=0, le=60)


class FetchRunRead(ORMModel):
    id: uuid.UUID
    status: RunStatus
    max_ads: int
    page_pause: float
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    fetched_count: int
    created_count: int
    updated_count: int
    price_change_count: int
    error: str | None


class AuditRunRead(ORMModel):
    id: uuid.UUID
    status: RunStatus
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    summary: dict[str, Any]
    report: dict[str, Any]
    error: str | None


class AdRead(ORMModel):
    code: str
    title: str | None
    brand: str | None
    model: str | None
    trim: str | None
    year: int | None
    mileage: int | None
    location: str | None
    category: str | None
    url: str | None
    publish_at: datetime | None
    current_price: int | None
    first_seen_at: datetime
    last_seen_at: datetime
    raw_payload: dict[str, Any] | None = None


class AdPage(BaseModel):
    items: list[AdRead]
    page: int
    page_size: int
    total: int


SortField = Literal["last_seen_at", "publish_at", "current_price", "year", "mileage"]
SortOrder = Literal["asc", "desc"]
