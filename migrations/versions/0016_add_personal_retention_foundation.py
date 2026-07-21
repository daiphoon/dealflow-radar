"""add personal watchlist requests and test usage limits

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
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


def _enable_postgresql_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    own_record = f"{_active_user_clause()} AND owner_user_id = {_current_user()}"
    for table_name in ("personal_watchlist_items", "personal_usage_records"):
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table_name}_owner_read ON {table_name} FOR SELECT USING ({own_record})"
        )
        op.execute(
            f"CREATE POLICY {table_name}_owner_insert ON {table_name} "
            f"FOR INSERT WITH CHECK ({own_record})"
        )
    op.execute(
        "CREATE POLICY personal_watchlist_items_owner_delete "
        "ON personal_watchlist_items FOR DELETE "
        f"USING ({own_record})"
    )

    op.execute("ALTER TABLE personal_company_requests ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY personal_company_requests_owner_read "
        "ON personal_company_requests FOR SELECT "
        f"USING ({own_record})"
    )
    op.execute(
        "CREATE POLICY personal_company_requests_owner_insert "
        "ON personal_company_requests FOR INSERT "
        f"WITH CHECK ({own_record} AND status = 'pending' AND reviewed_by_id IS NULL)"
    )
    op.execute(
        "CREATE POLICY personal_company_requests_platform_admin_read "
        "ON personal_company_requests FOR SELECT "
        f"USING ({_active_user_clause()} AND ({_platform_admin_clause()}))"
    )
    op.execute(
        "CREATE POLICY personal_company_requests_platform_admin_update "
        "ON personal_company_requests FOR UPDATE "
        f"USING ({_active_user_clause()} AND ({_platform_admin_clause()})) "
        f"WITH CHECK ({_active_user_clause()} AND ({_platform_admin_clause()}))"
    )


def upgrade() -> None:
    op.create_table(
        "personal_watchlist_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "company_id",
            name="uq_personal_watchlist_owner_company",
        ),
    )
    op.create_index(
        "ix_personal_watchlist_owner_created",
        "personal_watchlist_items",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "ix_personal_watchlist_items_company_id",
        "personal_watchlist_items",
        ["company_id"],
    )
    op.create_index(
        "ix_personal_watchlist_items_owner_user_id",
        "personal_watchlist_items",
        ["owner_user_id"],
    )

    op.create_table(
        "personal_company_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("request_type", sa.String(16), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("requested_name", sa.String(240), nullable=True),
        sa.Column("requested_credit_code", sa.String(32), nullable=True),
        sa.Column("target_key", sa.String(280), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reviewed_by_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "request_type IN ('inclusion', 'refresh')",
            name="ck_personal_company_request_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'in_review', 'completed', 'rejected')",
            name="ck_personal_company_request_status",
        ),
        sa.CheckConstraint(
            "(request_type = 'inclusion' AND company_id IS NULL "
            "AND (requested_name IS NOT NULL OR requested_credit_code IS NOT NULL)) OR "
            "(request_type = 'refresh' AND "
            "(company_id IS NOT NULL OR requested_name IS NOT NULL "
            "OR requested_credit_code IS NOT NULL))",
            name="ck_personal_company_request_target",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_personal_company_requests_company_id",
        "personal_company_requests",
        ["company_id"],
    )
    op.create_index(
        "ix_personal_company_request_owner_created",
        "personal_company_requests",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "ix_personal_company_requests_owner_user_id",
        "personal_company_requests",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_personal_company_requests_status",
        "personal_company_requests",
        ["status"],
    )
    op.create_index(
        "ix_personal_company_request_status_created",
        "personal_company_requests",
        ["status", "created_at"],
    )
    op.create_index(
        "uq_personal_company_request_active_target",
        "personal_company_requests",
        ["owner_user_id", "target_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'in_review')"),
        sqlite_where=sa.text("status IN ('pending', 'in_review')"),
    )

    op.create_table(
        "personal_usage_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("period_key", sa.String(7), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "operation IN ('company_search', 'company_request', 'company_report')",
            name="ck_personal_usage_operation",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_personal_usage_owner_period_operation",
        "personal_usage_records",
        ["owner_user_id", "period_key", "operation"],
    )
    op.create_index(
        "ix_personal_usage_records_owner_user_id",
        "personal_usage_records",
        ["owner_user_id"],
    )
    _enable_postgresql_rls()


def downgrade() -> None:
    op.drop_index(
        "ix_personal_usage_records_owner_user_id",
        table_name="personal_usage_records",
    )
    op.drop_index(
        "ix_personal_usage_owner_period_operation",
        table_name="personal_usage_records",
    )
    op.drop_table("personal_usage_records")

    op.drop_index(
        "uq_personal_company_request_active_target",
        table_name="personal_company_requests",
    )
    op.drop_index(
        "ix_personal_company_request_status_created",
        table_name="personal_company_requests",
    )
    op.drop_index(
        "ix_personal_company_requests_status",
        table_name="personal_company_requests",
    )
    op.drop_index(
        "ix_personal_company_requests_owner_user_id",
        table_name="personal_company_requests",
    )
    op.drop_index(
        "ix_personal_company_request_owner_created",
        table_name="personal_company_requests",
    )
    op.drop_index(
        "ix_personal_company_requests_company_id",
        table_name="personal_company_requests",
    )
    op.drop_table("personal_company_requests")

    op.drop_index(
        "ix_personal_watchlist_items_owner_user_id",
        table_name="personal_watchlist_items",
    )
    op.drop_index(
        "ix_personal_watchlist_items_company_id",
        table_name="personal_watchlist_items",
    )
    op.drop_index(
        "ix_personal_watchlist_owner_created",
        table_name="personal_watchlist_items",
    )
    op.drop_table("personal_watchlist_items")
