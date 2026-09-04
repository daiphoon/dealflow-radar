"""expand analysis RLS for bounded research candidates

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
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


def _analysis_event_clause() -> str:
    return """
        EXISTS (
            SELECT 1 FROM events
            WHERE events.id = investor_change_analyses.event_id
              AND events.visibility_scope = 'platform_shared'
              AND events.owner_user_id IS NULL
              AND events.owner_tenant_id IS NULL
              AND (
                  (
                      events.status = 'published'
                      AND events.publication_route = 'deterministic_change'
                      AND investor_change_analyses.schema_version =
                          'investor-change-analysis-v1'
                  )
                  OR
                  (
                      events.status = 'candidate'
                      AND events.publication_route = 'unconfirmed_lead'
                      AND events.event_subtype = 'bounded_public_web_page'
                      AND events.publication_policy_version = 'bounded-web-quality-v4'
                      AND investor_change_analyses.schema_version =
                          'research-candidate-analysis-v1'
                  )
              )
        )
    """


def _legacy_event_clause() -> str:
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


def _replace_policies(event_clause: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for policy_name in (
        "investor_change_analyses_read",
        "investor_change_analyses_platform_admin_read",
        "investor_change_analyses_insert",
        "investor_change_analyses_update",
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON investor_change_analyses")
    read_clause = (
        f"({_active_user_clause()}) AND ({event_clause}) "
        "AND investor_change_analyses.status = 'completed'"
    )
    admin_clause = f"({_platform_admin_clause()}) AND ({event_clause})"
    op.execute(
        "CREATE POLICY investor_change_analyses_read ON investor_change_analyses "
        f"FOR SELECT USING ({read_clause})"
    )
    op.execute(
        "CREATE POLICY investor_change_analyses_platform_admin_read "
        "ON investor_change_analyses "
        f"FOR SELECT USING ({admin_clause})"
    )
    op.execute(
        "CREATE POLICY investor_change_analyses_insert ON investor_change_analyses "
        f"FOR INSERT WITH CHECK ({admin_clause})"
    )
    op.execute(
        "CREATE POLICY investor_change_analyses_update ON investor_change_analyses "
        f"FOR UPDATE USING ({admin_clause}) WITH CHECK ({admin_clause})"
    )


def upgrade() -> None:
    _replace_policies(_analysis_event_clause())


def downgrade() -> None:
    _replace_policies(_legacy_event_clause())
