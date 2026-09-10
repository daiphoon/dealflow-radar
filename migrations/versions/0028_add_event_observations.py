"""Append event observations without changing existing events or evidence."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("raw_document_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("fact_version", sa.String(64), nullable=False),
        sa.Column("observation_kind", sa.String(32), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=True),
        sa.Column("date_precision", sa.String(16), nullable=False),
        sa.Column("candidate_payload", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(["raw_document_id"], ["raw_documents.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.UniqueConstraint(
            "event_id", "raw_document_id", "schema_version", name="uq_event_observation_document"
        ),
        sa.CheckConstraint(
            "observation_kind IN ('initial', 'same_facts', 'correction_candidate', "
            "'conflicting', 'incomplete')",
            name="ck_event_observation_kind",
        ),
        sa.CheckConstraint(
            "(occurred_on IS NULL AND date_precision = 'unknown') OR "
            "(occurred_on IS NOT NULL AND date_precision = 'day')",
            name="ck_event_observation_date_precision",
        ),
    )
    op.create_index("ix_event_observations_event_id", "event_observations", ["event_id"])
    op.create_index(
        "ix_event_observations_raw_document_id", "event_observations", ["raw_document_id"]
    )
    if op.get_bind().dialect.name == "postgresql":
        ledger = importlib.import_module(
            "migrations.versions.0024_add_evidence_fact_support_ledger"
        )
        parents = """
            EXISTS (
                SELECT 1 FROM events AS parent_event
                JOIN raw_documents AS parent_document
                  ON parent_document.id = event_observations.raw_document_id
                WHERE parent_event.id = event_observations.event_id
                  AND parent_event.visibility_scope = parent_document.visibility_scope
                  AND parent_event.owner_user_id
                      IS NOT DISTINCT FROM parent_document.owner_user_id
                  AND parent_event.owner_tenant_id
                      IS NOT DISTINCT FROM parent_document.owner_tenant_id
            )
        """
        write = ledger._ledger_write_clause("event_observations", require_evidence=False)
        actor = f"event_observations.created_by = {ledger._current_user()}"
        op.execute("ALTER TABLE event_observations ENABLE ROW LEVEL SECURITY")
        op.execute(
            "CREATE POLICY event_observations_scope_read ON event_observations "
            f"FOR SELECT USING ({parents})"
        )
        op.execute(
            "CREATE POLICY event_observations_scope_insert ON event_observations "
            f"FOR INSERT WITH CHECK (({parents}) AND ({write}) AND ({actor}))"
        )
        # 没有 UPDATE/DELETE 策略；应用角色只能追加，不能改写观测历史。


def downgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("Inspect event observations before preparing a downgrade")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM event_observations")):
        raise RuntimeError("Event observations exist; retain schema and evidence history")
    op.drop_index("ix_event_observations_raw_document_id", table_name="event_observations")
    op.drop_index("ix_event_observations_event_id", table_name="event_observations")
    op.drop_table("event_observations")
