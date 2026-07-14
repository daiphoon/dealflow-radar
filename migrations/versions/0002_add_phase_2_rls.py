"""add phase 2 row-level security policies

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICIES = {
    "fund_access_grants": """
        CREATE POLICY fund_access_grants_self ON fund_access_grants
        USING (
            user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            AND (valid_until IS NULL OR valid_until > CURRENT_TIMESTAMP)
        )
        WITH CHECK (
            user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            AND (valid_until IS NULL OR valid_until > CURRENT_TIMESTAMP)
        )
    """,
    "funds": """
        CREATE POLICY funds_authorized ON funds
        USING (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
            AND EXISTS (
                SELECT 1
                FROM fund_access_grants AS grant_row
                WHERE grant_row.fund_id = funds.id
                  AND grant_row.user_id =
                      NULLIF(current_setting('app.current_user_id', true), '')::uuid
                  AND (
                      grant_row.valid_until IS NULL
                      OR grant_row.valid_until > CURRENT_TIMESTAMP
                  )
            )
        )
    """,
    "investments": """
        CREATE POLICY investments_authorized ON investments
        USING (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
            AND EXISTS (
                SELECT 1
                FROM fund_access_grants AS grant_row
                WHERE grant_row.fund_id = investments.fund_id
                  AND grant_row.user_id =
                      NULLIF(current_setting('app.current_user_id', true), '')::uuid
                  AND (
                      grant_row.valid_until IS NULL
                      OR grant_row.valid_until > CURRENT_TIMESTAMP
                  )
            )
        )
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
            AND EXISTS (
                SELECT 1
                FROM fund_access_grants AS grant_row
                WHERE grant_row.fund_id = investments.fund_id
                  AND grant_row.user_id =
                      NULLIF(current_setting('app.current_user_id', true), '')::uuid
                  AND (
                      grant_row.valid_until IS NULL
                      OR grant_row.valid_until > CURRENT_TIMESTAMP
                  )
            )
        )
    """,
    "review_queue": """
        CREATE POLICY review_queue_reviewer ON review_queue
        USING (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
            AND EXISTS (
                SELECT 1
                FROM user_role_assignments AS assignment
                JOIN roles ON roles.id = assignment.role_id
                WHERE assignment.user_id =
                      NULLIF(current_setting('app.current_user_id', true), '')::uuid
                  AND roles.code = 'reviewer'
                  AND (
                      assignment.valid_until IS NULL
                      OR assignment.valid_until > CURRENT_TIMESTAMP
                  )
            )
        )
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
    """,
    "refresh_jobs": """
        CREATE POLICY refresh_jobs_tenant ON refresh_jobs
        USING (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
    """,
    "usage_ledger": """
        CREATE POLICY usage_ledger_tenant ON usage_ledger
        USING (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
    """,
}


def upgrade() -> None:
    """Enable PostgreSQL RLS for tenant and fund-private rows."""
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name, policy_sql in POLICIES.items():
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(policy_sql)


def downgrade() -> None:
    """Remove PostgreSQL RLS policies."""
    if op.get_bind().dialect.name != "postgresql":
        return
    policy_names = {
        "fund_access_grants": "fund_access_grants_self",
        "funds": "funds_authorized",
        "investments": "investments_authorized",
        "review_queue": "review_queue_reviewer",
        "refresh_jobs": "refresh_jobs_tenant",
        "usage_ledger": "usage_ledger_tenant",
    }
    for table_name, policy_name in reversed(policy_names.items()):
        op.execute(f"DROP POLICY {policy_name} ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
