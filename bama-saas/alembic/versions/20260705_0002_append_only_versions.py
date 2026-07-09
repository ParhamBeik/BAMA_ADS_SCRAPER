"""Add append-only ad versions and change events."""
from __future__ import annotations

import hashlib
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260705_0002"
down_revision = "20260705_0001"
branch_labels = None
depends_on = None

VOLATILE_DETAIL_KEYS = {"time", "rank"}


def _fingerprint(value: object) -> str:
    packed = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(packed.encode()).hexdigest()


def _semantic_payload(payload: dict) -> dict:
    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    detail = normalized.get("detail")
    if isinstance(detail, dict):
        for key in VOLATILE_DETAIL_KEYS:
            detail.pop(key, None)
    return normalized


def upgrade() -> None:
    op.create_table(
        "ad_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ad_code", sa.String(64), sa.ForeignKey("ads.code", ondelete="CASCADE"), nullable=False),
        sa.Column("semantic_hash", sa.String(64), nullable=False),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ad_code", "semantic_hash", name="uq_ad_version_semantic"),
    )
    op.create_index("ix_ad_versions_ad_code", "ad_versions", ["ad_code"])
    op.create_index("ix_ad_versions_origin", "ad_versions", ["origin"])
    op.create_index("ix_ad_versions_first_observed_at", "ad_versions", ["first_observed_at"])
    op.create_index("ix_ad_versions_ad_seen", "ad_versions", ["ad_code", "first_observed_at"])

    op.add_column("ad_observations", sa.Column("version_id", sa.BigInteger(), nullable=True))
    op.add_column("ad_observations", sa.Column("raw_hash", sa.String(64), nullable=True))
    op.add_column("ad_observations", sa.Column("publish_phrase", sa.String(128), nullable=True))
    op.add_column("ad_observations", sa.Column("rank", sa.String(64), nullable=True))

    bind = op.get_bind()
    observations = bind.execute(sa.text("""
        SELECT id,ad_code,observed_at,payload_hash,raw_payload
        FROM ad_observations ORDER BY ad_code,observed_at,id
    """)).mappings().all()
    version_ids: dict[tuple[str, str], int] = {}
    for row in observations:
        payload = row["raw_payload"] or {}
        raw_hash = _fingerprint(payload)
        semantic_hash = _fingerprint(_semantic_payload(payload))
        key = (row["ad_code"], semantic_hash)
        version_id = version_ids.get(key)
        if version_id is None:
            existing = bind.execute(sa.text("""
                SELECT id FROM ad_versions WHERE ad_code=:code AND semantic_hash=:hash
            """), {"code": row["ad_code"], "hash": semantic_hash}).scalar()
            if existing is None:
                result = bind.execute(sa.text("""
                    INSERT INTO ad_versions(ad_code,semantic_hash,raw_hash,payload,origin,first_observed_at)
                    VALUES(:code,:semantic_hash,:raw_hash,CAST(:payload AS jsonb),'legacy_migration',:observed_at)
                    RETURNING id
                """), {
                    "code": row["ad_code"],
                    "semantic_hash": semantic_hash,
                    "raw_hash": raw_hash,
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "observed_at": row["observed_at"],
                })
                version_id = int(result.scalar_one())
            else:
                version_id = int(existing)
            version_ids[key] = version_id
        detail = payload.get("detail") if isinstance(payload, dict) else {}
        bind.execute(sa.text("""
            UPDATE ad_observations
               SET version_id=:version_id, raw_hash=:raw_hash,
                   publish_phrase=:publish_phrase, rank=:rank
             WHERE id=:id
        """), {
            "version_id": version_id,
            "raw_hash": raw_hash,
            "publish_phrase": str(detail.get("time") or "") if isinstance(detail, dict) else "",
            "rank": str(detail.get("rank") or "") if isinstance(detail, dict) else "",
            "id": row["id"],
        })

    op.alter_column("ad_observations", "version_id", nullable=False)
    op.alter_column("ad_observations", "raw_hash", nullable=False)
    op.create_foreign_key(
        "fk_ad_observations_version_id_ad_versions",
        "ad_observations",
        "ad_versions",
        ["version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_ad_observations_version_id", "ad_observations", ["version_id"])

    op.create_table(
        "ad_change_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ad_code", sa.String(64), sa.ForeignKey("ads.code", ondelete="CASCADE"), nullable=False),
        sa.Column("observation_id", sa.BigInteger(), sa.ForeignKey("ad_observations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("previous_version_id", sa.BigInteger(), sa.ForeignKey("ad_versions.id", ondelete="RESTRICT")),
        sa.Column("new_version_id", sa.BigInteger(), sa.ForeignKey("ad_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("categories", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("changed_paths", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("changes", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("observation_id", "event_type", name="uq_change_event_observation_type"),
    )
    op.create_index("ix_ad_change_events_ad_code", "ad_change_events", ["ad_code"])
    op.create_index("ix_ad_change_events_observation_id", "ad_change_events", ["observation_id"])
    op.create_index("ix_ad_change_events_event_type", "ad_change_events", ["event_type"])
    op.create_index("ix_ad_change_events_origin", "ad_change_events", ["origin"])
    op.create_index("ix_ad_change_events_created_at", "ad_change_events", ["created_at"])
    op.create_index("ix_change_events_category", "ad_change_events", ["categories"], postgresql_using="gin")
    op.create_index("ix_change_events_paths", "ad_change_events", ["changed_paths"], postgresql_using="gin")

    rows = bind.execute(sa.text("""
        SELECT id,ad_code,version_id,observed_at
        FROM ad_observations ORDER BY ad_code,observed_at,id
    """)).mappings()
    previous_by_code: dict[str, int] = {}
    for row in rows:
        previous = previous_by_code.get(row["ad_code"])
        event_type = "legacy_baseline" if previous is None else "content_changed"
        if previous is None or previous != row["version_id"]:
            bind.execute(sa.text("""
                INSERT INTO ad_change_events(
                    ad_code,observation_id,previous_version_id,new_version_id,event_type,
                    categories,changed_paths,changes,origin,created_at
                ) VALUES(
                    :code,:observation_id,:previous_version_id,:new_version_id,:event_type,
                    '[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'legacy_migration',:created_at
                )
            """), {
                "code": row["ad_code"],
                "observation_id": row["id"],
                "previous_version_id": previous,
                "new_version_id": row["version_id"],
                "event_type": event_type,
                "created_at": row["observed_at"],
            })
        previous_by_code[row["ad_code"]] = row["version_id"]

    op.drop_column("ad_observations", "raw_payload")
    op.drop_column("ad_observations", "payload_hash")


def downgrade() -> None:
    op.add_column("ad_observations", sa.Column("payload_hash", sa.String(64), nullable=True))
    op.add_column("ad_observations", sa.Column("raw_payload", postgresql.JSONB(), nullable=True))
    op.execute("""
        UPDATE ad_observations o
           SET payload_hash=o.raw_hash, raw_payload=v.payload
          FROM ad_versions v
         WHERE v.id=o.version_id
    """)
    op.alter_column("ad_observations", "payload_hash", nullable=False)
    op.alter_column("ad_observations", "raw_payload", nullable=False)
    op.drop_table("ad_change_events")
    op.drop_index("ix_ad_observations_version_id", table_name="ad_observations")
    op.drop_constraint("fk_ad_observations_version_id_ad_versions", "ad_observations", type_="foreignkey")
    op.drop_column("ad_observations", "rank")
    op.drop_column("ad_observations", "publish_phrase")
    op.drop_column("ad_observations", "raw_hash")
    op.drop_column("ad_observations", "version_id")
    op.drop_table("ad_versions")
