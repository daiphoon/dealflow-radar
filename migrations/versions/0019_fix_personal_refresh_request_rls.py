"""allow owners to queue refresh requests for shared verified companies

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
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


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    owner_record = _active_owner_clause()
    op.execute("DROP POLICY personal_company_requests_owner_insert ON personal_company_requests")
    op.execute(
        "CREATE POLICY personal_company_requests_owner_insert "
        "ON personal_company_requests FOR INSERT "
        f"WITH CHECK (({owner_record}) AND reviewed_by_id IS NULL AND ("
        "(request_type = 'inclusion' AND company_id IS NULL "
        "AND status IN ('pending', 'identity_queued')) OR "
        "(request_type = 'refresh' AND company_id IS NOT NULL "
        "AND status IN ('pending', 'research_queued') AND EXISTS ("
        "SELECT 1 FROM companies AS visible_shared_company "
        "WHERE visible_shared_company.id = personal_company_requests.company_id "
        "AND visible_shared_company.tenant_id IS NULL "
        "AND visible_shared_company.visibility_scope = 'public' "
        "AND visible_shared_company.identity_status = 'verified'"
        "))))"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    owner_record = _active_owner_clause()
    op.execute("DROP POLICY personal_company_requests_owner_insert ON personal_company_requests")
    op.execute(
        "CREATE POLICY personal_company_requests_owner_insert "
        "ON personal_company_requests FOR INSERT "
        f"WITH CHECK (({owner_record}) AND status IN ('pending', 'identity_queued') "
        "AND reviewed_by_id IS NULL)"
    )
