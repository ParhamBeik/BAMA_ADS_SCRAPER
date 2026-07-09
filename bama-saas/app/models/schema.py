from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


# ---------------------------------------------------------------------------
# Shared run/status fields
# ---------------------------------------------------------------------------

class RunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Current ad snapshot
# ---------------------------------------------------------------------------

class Ad(TimestampMixin, Base):
    """Latest known state for each ad, optimized for API filters and insights."""

    __tablename__ = "ads"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(String(128), index=True)
    model: Mapped[str | None] = mapped_column(String(128), index=True)
    trim: Mapped[str | None] = mapped_column(String(128), index=True)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    mileage: Mapped[int | None] = mapped_column(BigInteger, index=True)
    location: Mapped[str | None] = mapped_column(String(128), index=True)
    body_type: Mapped[str | None] = mapped_column(String(64))
    body_color: Mapped[str | None] = mapped_column(String(64))
    body_status: Mapped[str | None] = mapped_column(String(64))
    fuel: Mapped[str | None] = mapped_column(String(64))
    transmission: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    url: Mapped[str | None] = mapped_column(Text)
    publish_phrase: Mapped[str | None] = mapped_column(String(128))
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    current_price: Mapped[int | None] = mapped_column(BigInteger, index=True)
    current_payment: Mapped[int | None] = mapped_column(BigInteger)
    current_prepayment: Mapped[int | None] = mapped_column(BigInteger)
    current_installments: Mapped[int | None] = mapped_column(Integer)
    price_type: Mapped[str | None] = mapped_column(String(32))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)

    versions: Mapped[list[AdVersion]] = relationship(back_populates="ad", cascade="all, delete-orphan")
    observations: Mapped[list[AdObservation]] = relationship(back_populates="ad", cascade="all, delete-orphan")
    change_events: Mapped[list[AdChangeEvent]] = relationship(back_populates="ad", cascade="all, delete-orphan")
    prices: Mapped[list[PriceObservation]] = relationship(back_populates="ad", cascade="all, delete-orphan")
    media: Mapped[list[AdMedia]] = relationship(back_populates="ad", cascade="all, delete-orphan")
    metadata_row: Mapped[AdMetadata | None] = relationship(back_populates="ad", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_ads_market", "brand", "model", "trim", "year"),
        Index("ix_ads_market_price", "brand", "model", "current_price"),
        Index("ix_ads_raw_payload_gin", "raw_payload", postgresql_using="gin"),
    )


# ---------------------------------------------------------------------------
# Fetch and audit run tracking
# ---------------------------------------------------------------------------

class FetchRun(Base):
    __tablename__ = "fetch_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus, name="run_status"), default=RunStatus.queued, index=True)
    max_ads: Mapped[int] = mapped_column(Integer)
    page_pause: Mapped[float] = mapped_column()
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    price_change_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Append-only ad history
# ---------------------------------------------------------------------------

class AdVersion(Base):
    """Immutable semantic payload version for an ad."""

    __tablename__ = "ad_versions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ad_code: Mapped[str] = mapped_column(ForeignKey("ads.code", ondelete="CASCADE"), index=True)
    semantic_hash: Mapped[str] = mapped_column(String(64))
    raw_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    origin: Mapped[str] = mapped_column(String(32), index=True)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ad: Mapped[Ad] = relationship(back_populates="versions")
    observations: Mapped[list[AdObservation]] = relationship(back_populates="version")
    __table_args__ = (
        UniqueConstraint("ad_code", "semantic_hash", name="uq_ad_version_semantic"),
        Index("ix_ad_versions_ad_seen", "ad_code", "first_observed_at"),
    )


class AdObservation(Base):
    """One sighting of an ad in one fetch run."""

    __tablename__ = "ad_observations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ad_code: Mapped[str] = mapped_column(ForeignKey("ads.code", ondelete="CASCADE"), index=True)
    fetch_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fetch_runs.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("ad_versions.id", ondelete="RESTRICT"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    raw_hash: Mapped[str] = mapped_column(String(64))
    publish_phrase: Mapped[str | None] = mapped_column(String(128))
    rank: Mapped[str | None] = mapped_column(String(64))
    ad: Mapped[Ad] = relationship(back_populates="observations")
    version: Mapped[AdVersion] = relationship(back_populates="observations")
    __table_args__ = (UniqueConstraint("fetch_run_id", "ad_code", name="uq_observation_run_ad"),)


class AdChangeEvent(Base):
    """Human-readable transition between two observations/versions."""

    __tablename__ = "ad_change_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ad_code: Mapped[str] = mapped_column(ForeignKey("ads.code", ondelete="CASCADE"), index=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("ad_observations.id", ondelete="CASCADE"), index=True)
    previous_version_id: Mapped[int | None] = mapped_column(ForeignKey("ad_versions.id", ondelete="RESTRICT"))
    new_version_id: Mapped[int] = mapped_column(ForeignKey("ad_versions.id", ondelete="RESTRICT"))
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    categories: Mapped[list[str]] = mapped_column(JSONB, default=list)
    changed_paths: Mapped[list[str]] = mapped_column(JSONB, default=list)
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    origin: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ad: Mapped[Ad] = relationship(back_populates="change_events")
    __table_args__ = (
        UniqueConstraint("observation_id", "event_type", name="uq_change_event_observation_type"),
        Index("ix_change_events_category", "categories", postgresql_using="gin"),
        Index("ix_change_events_paths", "changed_paths", postgresql_using="gin"),
    )


class PriceObservation(Base):
    """Change-only price/payment history."""

    __tablename__ = "price_observations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ad_code: Mapped[str] = mapped_column(ForeignKey("ads.code", ondelete="CASCADE"), index=True)
    fetch_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fetch_runs.id", ondelete="CASCADE"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    price: Mapped[int | None] = mapped_column(BigInteger)
    payment: Mapped[int | None] = mapped_column(BigInteger)
    prepayment: Mapped[int | None] = mapped_column(BigInteger)
    installments: Mapped[int | None] = mapped_column(Integer)
    price_type: Mapped[str | None] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(64))
    ad: Mapped[Ad] = relationship(back_populates="prices")
    __table_args__ = (Index("ix_price_ad_observed", "ad_code", "observed_at"),)


# ---------------------------------------------------------------------------
# Current media, metadata, audits, and parser gaps
# ---------------------------------------------------------------------------

class AdMedia(Base):
    __tablename__ = "ad_media"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ad_code: Mapped[str] = mapped_column(ForeignKey("ads.code", ondelete="CASCADE"), index=True)
    media_type: Mapped[str] = mapped_column(String(16))
    position: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(Text)
    variants: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ad: Mapped[Ad] = relationship(back_populates="media")
    __table_args__ = (UniqueConstraint("ad_code", "media_type", "position", name="uq_media_position"),)


class AdMetadata(Base):
    __tablename__ = "ad_metadata"
    ad_code: Mapped[str] = mapped_column(ForeignKey("ads.code", ondelete="CASCADE"), primary_key=True)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    title_tag: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[str | None] = mapped_column(Text)
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ad: Mapped[Ad] = relationship(back_populates="metadata_row")


class AuditRun(Base):
    __tablename__ = "audit_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus, name="run_status", create_type=False), default=RunStatus.queued, index=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class UnknownTimePhrase(Base):
    """Publish-time phrase that the parser could not convert yet."""

    __tablename__ = "unknown_time_phrases"
    phrase: Mapped[str] = mapped_column(String(256), primary_key=True)
    occurrence_count: Mapped[int] = mapped_column(BigInteger, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_fetch_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    last_fetch_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
