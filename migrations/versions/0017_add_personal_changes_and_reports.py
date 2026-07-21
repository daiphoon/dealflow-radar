"""add personal change receipts and fixed reports

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _current_user() -> str:
    return "NULLIF(current_setting('app.current_user_id', true), '')::uuid"


def _current_tenant() -> str:
    return "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def _active_owner_clause() -> str:
    return f"""
        EXISTS (
            SELECT 1 FROM users AS current_scope_user
            WHERE current_scope_user.id = {_current_user()}
              AND current_scope_user.tenant_id = {_current_tenant()}
              AND current_scope_user.status = 'active'
        )
        AND owner_user_id = {_current_user()}
    """


def _enable_postgresql_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    owner_clause = _active_owner_clause()
    for table_name in (
        "personal_company_view_states",
        "personal_event_view_receipts",
        "personal_company_reports",
    ):
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table_name}_owner_read ON {table_name} "
            f"FOR SELECT USING ({owner_clause})"
        )
        op.execute(
            f"CREATE POLICY {table_name}_owner_insert ON {table_name} "
            f"FOR INSERT WITH CHECK ({owner_clause})"
        )
    op.execute(
        "CREATE POLICY personal_company_view_states_owner_update "
        "ON personal_company_view_states FOR UPDATE "
        f"USING ({owner_clause}) WITH CHECK ({owner_clause})"
    )


def upgrade() -> None:
    op.create_table(
        "personal_company_view_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "company_id",
            name="uq_personal_company_view_owner_company",
        ),
    )
    op.create_index(
        "ix_personal_company_view_states_company_id",
        "personal_company_view_states",
        ["company_id"],
    )
    op.create_index(
        "ix_personal_company_view_states_owner_user_id",
        "personal_company_view_states",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_personal_company_view_owner_updated",
        "personal_company_view_states",
        ["owner_user_id", "updated_at"],
    )

    op.create_table(
        "personal_event_view_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "event_id",
            name="uq_personal_event_view_owner_event",
        ),
    )
    op.create_index(
        "ix_personal_event_view_receipts_event_id",
        "personal_event_view_receipts",
        ["event_id"],
    )
    op.create_index(
        "ix_personal_event_view_receipts_owner_user_id",
        "personal_event_view_receipts",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_personal_event_view_owner_seen",
        "personal_event_view_receipts",
        ["owner_user_id", "first_seen_at"],
    )

    op.create_table(
        "personal_company_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("company_legal_name", sa.String(length=240), nullable=False),
        sa.Column("report_version", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=280), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_event_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "idempotency_key",
            name="uq_personal_company_report_owner_idempotency",
        ),
    )
    op.create_index(
        "ix_personal_company_reports_company_id",
        "personal_company_reports",
        ["company_id"],
    )
    op.create_index(
        "ix_personal_company_reports_owner_user_id",
        "personal_company_reports",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_personal_company_report_owner_created",
        "personal_company_reports",
        ["owner_user_id", "created_at"],
    )
    _enable_postgresql_rls()


def downgrade() -> None:
    op.drop_index(
        "ix_personal_company_report_owner_created",
        table_name="personal_company_reports",
    )
    op.drop_index(
        "ix_personal_company_reports_owner_user_id",
        table_name="personal_company_reports",
    )
    op.drop_index(
        "ix_personal_company_reports_company_id",
        table_name="personal_company_reports",
    )
    op.drop_table("personal_company_reports")

    op.drop_index(
        "ix_personal_event_view_owner_seen",
        table_name="personal_event_view_receipts",
    )
    op.drop_index(
        "ix_personal_event_view_receipts_owner_user_id",
        table_name="personal_event_view_receipts",
    )
    op.drop_index(
        "ix_personal_event_view_receipts_event_id",
        table_name="personal_event_view_receipts",
    )
    op.drop_table("personal_event_view_receipts")

    op.drop_index(
        "ix_personal_company_view_owner_updated",
        table_name="personal_company_view_states",
    )
    op.drop_index(
        "ix_personal_company_view_states_owner_user_id",
        table_name="personal_company_view_states",
    )
    op.drop_index(
        "ix_personal_company_view_states_company_id",
        table_name="personal_company_view_states",
    )
    op.drop_table("personal_company_view_states")
