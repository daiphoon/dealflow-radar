"""add investor change analyses

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _current_user() -> str:
    return "NULLIF(current_setting('app.current_user_id', true), '')::uuid"


def _current_tenant() -> str:
    return "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def _active_user_clause() -> str:
    return f"""
        EXISTS (
            SELECT 1 FROM users AS current_scope_user
            WHERE current_scope_user.id = {_current_user()}
              AND current_scope_user.tenant_id = {_current_tenant()}
              AND current_scope_user.status = 'active'
        )
    """


def _platform_admin_clause() -> str:
    return f"""
        EXISTS (
            SELECT 1
            FROM user_role_assignments AS assignment
            JOIN roles ON roles.id = assignment.role_id
            JOIN users ON users.id = assignment.user_id
            WHERE assignment.user_id = {_current_user()}
              AND users.tenant_id = {_current_tenant()}
              AND users.status = 'active'
              AND roles.code = 'platform_admin'
              AND (assignment.valid_until IS NULL OR assignment.valid_until > CURRENT_TIMESTAMP)
        )
    """


def _shared_change_event_clause() -> str:
    return """
        EXISTS (
            SELECT 1 FROM events
            WHERE events.id = investor_change_analyses.event_id
              AND events.visibility_scope = 'platform_shared'
              AND events.owner_user_id IS NULL
              AND events.owner_tenant_id IS NULL
              AND events.status = 'published'
              AND events.publication_route = 'deterministic_change'
        )
    """


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    read_clause = (
        f"({_active_user_clause()}) AND ({_shared_change_event_clause()}) "
        "AND investor_change_analyses.status = 'completed'"
    )
    admin_read_clause = f"({_platform_admin_clause()}) AND ({_shared_change_event_clause()})"
    write_clause = f"({_platform_admin_clause()}) AND ({_shared_change_event_clause()})"
    op.execute("ALTER TABLE investor_change_analyses ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY investor_change_analyses_read ON investor_change_analyses "
        f"FOR SELECT USING ({read_clause})"
    )
    op.execute(
        "CREATE POLICY investor_change_analyses_platform_admin_read "
        "ON investor_change_analyses "
        f"FOR SELECT USING ({admin_read_clause})"
    )
    op.execute(
        "CREATE POLICY investor_change_analyses_insert ON investor_change_analyses "
        f"FOR INSERT WITH CHECK ({write_clause})"
    )
    op.execute(
        "CREATE POLICY investor_change_analyses_update ON investor_change_analyses "
        f"FOR UPDATE USING ({write_clause}) WITH CHECK ({write_clause})"
    )


def upgrade() -> None:
    op.create_table(
        "investor_change_analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("visibility_scope", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(80), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("analysis_output", sa.JSON(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(12, 6), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("response_id", sa.String(200), nullable=True),
        sa.Column("last_error_code", sa.String(80), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'budget_deferred')",
            name="ck_investor_change_analysis_status",
        ),
        sa.CheckConstraint(
            "visibility_scope = 'platform_shared'",
            name="ck_investor_change_analysis_platform_shared",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            "prompt_version",
            "input_hash",
            name="uq_investor_change_analysis_input",
        ),
    )
    op.create_index(
        "ix_investor_change_analyses_event_id",
        "investor_change_analyses",
        ["event_id"],
    )
    op.create_index(
        "ix_investor_change_analyses_visibility_scope",
        "investor_change_analyses",
        ["visibility_scope"],
    )
    op.create_index(
        "ix_investor_change_analyses_status",
        "investor_change_analyses",
        ["status"],
    )
    op.create_index(
        "ix_investor_change_analysis_status_created",
        "investor_change_analyses",
        ["status", "created_at"],
    )
    _enable_rls()


def downgrade() -> None:
    op.drop_index(
        "ix_investor_change_analysis_status_created",
        table_name="investor_change_analyses",
    )
    op.drop_index(
        "ix_investor_change_analyses_status",
        table_name="investor_change_analyses",
    )
    op.drop_index(
        "ix_investor_change_analyses_visibility_scope",
        table_name="investor_change_analyses",
    )
    op.drop_index(
        "ix_investor_change_analyses_event_id",
        table_name="investor_change_analyses",
    )
    op.drop_table("investor_change_analyses")
