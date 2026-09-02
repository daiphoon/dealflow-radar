"""add bounded web research cache

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
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


def _create_cache_table() -> None:
    op.create_table(
        "web_search_cache_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("provider_code", sa.String(32), nullable=False),
        sa.Column("query_kind", sa.String(64), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("identity_fingerprint", sa.String(64), nullable=False),
        sa.Column("response_hash", sa.String(64), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "provider_code",
            "query_hash",
            "identity_fingerprint",
            name="uq_web_search_cache_identity_query",
        ),
    )
    op.create_index(
        "ix_web_search_cache_entries_company_id",
        "web_search_cache_entries",
        ["company_id"],
    )
    op.create_index(
        "ix_web_search_cache_company_expires",
        "web_search_cache_entries",
        ["company_id", "expires_at"],
    )
    op.create_index(
        "ix_web_search_cache_provider_fetched",
        "web_search_cache_entries",
        ["provider_code", "fetched_at"],
    )


def _enable_postgresql_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    active_admin = f"({_active_user_clause()}) AND ({_platform_admin_clause()})"
    op.execute("ALTER TABLE web_search_cache_entries ENABLE ROW LEVEL SECURITY")
    for action in ("SELECT", "INSERT", "UPDATE"):
        suffix = "read" if action == "SELECT" else action.lower()
        expression = f"USING ({active_admin})"
        if action == "INSERT":
            expression = f"WITH CHECK ({active_admin})"
        elif action == "UPDATE":
            expression = f"USING ({active_admin}) WITH CHECK ({active_admin})"
        op.execute(
            f"CREATE POLICY web_search_cache_platform_admin_{suffix} "
            f"ON web_search_cache_entries FOR {action} {expression}"
        )

    bounded_document = """
        raw_documents.visibility_scope = 'system_restricted'
        AND raw_documents.owner_user_id IS NULL
        AND raw_documents.owner_tenant_id IS NULL
        AND EXISTS (
            SELECT 1 FROM sources
            WHERE sources.id = raw_documents.source_id
              AND sources.code = 'bounded_public_web'
        )
    """
    op.execute(
        "CREATE POLICY raw_documents_bounded_web_admin_read ON raw_documents FOR SELECT "
        f"USING (({active_admin}) AND ({bounded_document}))"
    )
    op.execute(
        "CREATE POLICY raw_documents_bounded_web_admin_insert ON raw_documents FOR INSERT "
        f"WITH CHECK (({active_admin}) AND ({bounded_document}))"
    )

    bounded_mention = """
        entity_mentions.visibility_scope = 'system_restricted'
        AND entity_mentions.owner_user_id IS NULL
        AND entity_mentions.owner_tenant_id IS NULL
        AND entity_mentions.match_rule = 'verified_identity_exact_or_official_domain'
        AND entity_mentions.resolution_status = 'verified'
        AND entity_mentions.candidate_company_id IS NOT NULL
        AND EXISTS (
            SELECT 1 FROM companies
            WHERE companies.id = entity_mentions.candidate_company_id
              AND companies.tenant_id IS NULL
              AND companies.visibility_scope = 'public'
              AND companies.identity_status = 'verified'
        )
    """
    op.execute(
        "CREATE POLICY entity_mentions_bounded_web_admin_read ON entity_mentions FOR SELECT "
        f"USING (({active_admin}) AND ({bounded_mention}))"
    )
    op.execute(
        "CREATE POLICY entity_mentions_bounded_web_admin_insert ON entity_mentions FOR INSERT "
        f"WITH CHECK (({active_admin}) AND ({bounded_mention}))"
    )


def upgrade() -> None:
    _create_cache_table()
    _enable_postgresql_rls()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY entity_mentions_bounded_web_admin_insert ON entity_mentions")
        op.execute("DROP POLICY entity_mentions_bounded_web_admin_read ON entity_mentions")
        op.execute("DROP POLICY raw_documents_bounded_web_admin_insert ON raw_documents")
        op.execute("DROP POLICY raw_documents_bounded_web_admin_read ON raw_documents")
        for policy in (
            "web_search_cache_platform_admin_update",
            "web_search_cache_platform_admin_insert",
            "web_search_cache_platform_admin_read",
        ):
            op.execute(f"DROP POLICY {policy} ON web_search_cache_entries")
        op.execute("ALTER TABLE web_search_cache_entries DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "ix_web_search_cache_provider_fetched",
        table_name="web_search_cache_entries",
    )
    op.drop_index(
        "ix_web_search_cache_company_expires",
        table_name="web_search_cache_entries",
    )
    op.drop_index(
        "ix_web_search_cache_entries_company_id",
        table_name="web_search_cache_entries",
    )
    op.drop_table("web_search_cache_entries")
