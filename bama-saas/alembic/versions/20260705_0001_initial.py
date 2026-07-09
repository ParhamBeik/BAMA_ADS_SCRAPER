"""Initial live-ingestion schema."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260705_0001"
down_revision = None
branch_labels = None
depends_on = None

run_status = postgresql.ENUM("queued", "running", "succeeded", "failed", name="run_status", create_type=False)


def upgrade() -> None:
    run_status.create(op.get_bind(), checkfirst=True)
    op.create_table("fetch_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("status", run_status, nullable=False),
        sa.Column("max_ads", sa.Integer(), nullable=False), sa.Column("page_pause", sa.Float(), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("fetched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price_change_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("error", sa.Text()))
    op.create_index("ix_fetch_runs_status", "fetch_runs", ["status"])
    op.create_table("audit_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("status", run_status, nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("report", postgresql.JSONB(), nullable=False, server_default="{}"), sa.Column("error", sa.Text()))
    op.create_index("ix_audit_runs_status", "audit_runs", ["status"])
    op.create_table("ads",
        sa.Column("code", sa.String(64), primary_key=True), sa.Column("title", sa.Text()),
        sa.Column("brand", sa.String(128)), sa.Column("model", sa.String(128)), sa.Column("trim", sa.String(128)),
        sa.Column("year", sa.Integer()), sa.Column("mileage", sa.BigInteger()), sa.Column("location", sa.String(128)),
        sa.Column("body_type", sa.String(64)), sa.Column("body_color", sa.String(64)), sa.Column("body_status", sa.String(64)),
        sa.Column("fuel", sa.String(64)), sa.Column("transmission", sa.String(64)), sa.Column("category", sa.String(64)),
        sa.Column("url", sa.Text()), sa.Column("publish_phrase", sa.String(128)), sa.Column("publish_at", sa.DateTime(timezone=True)),
        sa.Column("current_price", sa.BigInteger()), sa.Column("current_payment", sa.BigInteger()),
        sa.Column("current_prepayment", sa.BigInteger()), sa.Column("current_installments", sa.Integer()),
        sa.Column("price_type", sa.String(32)), sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    for name in ("brand", "model", "trim", "year", "mileage", "location", "category", "publish_at", "current_price", "first_seen_at", "last_seen_at"):
        op.create_index(f"ix_ads_{name}", "ads", [name])
    op.create_index("ix_ads_market", "ads", ["brand", "model", "trim", "year"])
    op.create_index("ix_ads_market_price", "ads", ["brand", "model", "current_price"])
    op.create_index("ix_ads_raw_payload_gin", "ads", ["raw_payload"], postgresql_using="gin")
    op.create_table("ad_observations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ad_code", sa.String(64), sa.ForeignKey("ads.code", ondelete="CASCADE"), nullable=False),
        sa.Column("fetch_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fetch_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False), sa.UniqueConstraint("fetch_run_id", "ad_code", name="uq_observation_run_ad"))
    op.create_index("ix_ad_observations_ad_code", "ad_observations", ["ad_code"])
    op.create_index("ix_ad_observations_fetch_run_id", "ad_observations", ["fetch_run_id"])
    op.create_index("ix_ad_observations_observed_at", "ad_observations", ["observed_at"])
    op.create_table("price_observations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ad_code", sa.String(64), sa.ForeignKey("ads.code", ondelete="CASCADE"), nullable=False),
        sa.Column("fetch_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fetch_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("price", sa.BigInteger()),
        sa.Column("payment", sa.BigInteger()), sa.Column("prepayment", sa.BigInteger()), sa.Column("installments", sa.Integer()),
        sa.Column("price_type", sa.String(32)), sa.Column("fingerprint", sa.String(64), nullable=False))
    op.create_index("ix_price_observations_ad_code", "price_observations", ["ad_code"])
    op.create_index("ix_price_observations_fetch_run_id", "price_observations", ["fetch_run_id"])
    op.create_index("ix_price_observations_observed_at", "price_observations", ["observed_at"])
    op.create_index("ix_price_ad_observed", "price_observations", ["ad_code", "observed_at"])
    op.create_table("ad_media", sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ad_code", sa.String(64), sa.ForeignKey("ads.code", ondelete="CASCADE"), nullable=False),
        sa.Column("media_type", sa.String(16), nullable=False), sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False), sa.Column("variants", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("ad_code", "media_type", "position", name="uq_media_position"))
    op.create_index("ix_ad_media_ad_code", "ad_media", ["ad_code"])
    op.create_table("ad_metadata", sa.Column("ad_code", sa.String(64), sa.ForeignKey("ads.code", ondelete="CASCADE"), primary_key=True),
        sa.Column("canonical_url", sa.Text()), sa.Column("title_tag", sa.Text()), sa.Column("description", sa.Text()),
        sa.Column("keywords", sa.Text()), sa.Column("raw_metadata", postgresql.JSONB(), nullable=False, server_default="{}"))
    op.create_table("unknown_time_phrases", sa.Column("phrase", sa.String(256), primary_key=True),
        sa.Column("occurrence_count", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_fetch_run_id", postgresql.UUID(as_uuid=True)), sa.Column("last_fetch_run_id", postgresql.UUID(as_uuid=True)))
    op.execute("""CREATE FUNCTION set_updated_at() RETURNS trigger AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql""")
    op.execute("CREATE TRIGGER ads_updated_at BEFORE UPDATE ON ads FOR EACH ROW EXECUTE FUNCTION set_updated_at()")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS set_updated_at() CASCADE")
    for table in ("unknown_time_phrases", "ad_metadata", "ad_media", "price_observations", "ad_observations", "ads", "audit_runs", "fetch_runs"):
        op.drop_table(table)
    run_status.drop(op.get_bind(), checkfirst=True)
