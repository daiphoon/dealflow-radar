"""add trusted source monitoring

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _current_user() -> str:
    return "NULLIF(current_setting('app.current_user_id', true), '')::uuid"


def _current_tenant() -> str:
    return "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


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


def _active_user_clause() -> str:
    return f"""
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = {_current_user()}
              AND users.tenant_id = {_current_tenant()}
              AND users.status = 'active'
        )
    """


def _create_tables() -> None:
    op.create_table(
        "trusted_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("root_domain", sa.String(253), nullable=False),
        sa.Column("start_url", sa.String(1000), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("access_basis", sa.Text(), nullable=False),
        sa.Column("license_status", sa.String(32), nullable=False),
        sa.Column("check_frequency_minutes", sa.Integer(), server_default="10080", nullable=False),
        sa.Column(
            "content_retention_policy",
            sa.String(32),
            server_default="metadata_only",
            nullable=False,
        ),
        sa.Column(
            "visibility_scope",
            sa.String(32),
            server_default="organization_private",
            nullable=False,
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_failure_code", sa.String(64)),
        sa.Column("last_http_status", sa.Integer()),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_etag", sa.String(500)),
        sa.Column("last_modified", sa.String(200)),
        sa.Column("last_content_hash", sa.String(64)),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('single_page', 'list_page', 'rss', 'sitemap')",
            name="ck_trusted_source_type",
        ),
        sa.CheckConstraint(
            "content_retention_policy IN ('metadata_only', 'minimal_excerpt')",
            name="ck_trusted_source_retention",
        ),
        sa.CheckConstraint(
            "license_status IN ('public_access', 'permission_confirmed', 'unclear', 'restricted')",
            name="ck_trusted_source_license_status",
        ),
        sa.CheckConstraint(
            "visibility_scope = 'organization_private'",
            name="ck_trusted_source_scope",
        ),
        sa.CheckConstraint(
            "check_frequency_minutes > 0 AND consecutive_failures >= 0",
            name="ck_trusted_source_counters",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "start_url", name="uq_trusted_source_company_url"
        ),
        sa.UniqueConstraint(
            "id", "tenant_id", "company_id", name="uq_trusted_source_scope_lineage"
        ),
    )
    op.create_index(
        "ix_trusted_source_tenant_company", "trusted_sources", ["tenant_id", "company_id"]
    )
    op.create_index("ix_trusted_source_tenant_enabled", "trusted_sources", ["tenant_id", "enabled"])

    op.create_table(
        "source_check_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("trusted_source_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), server_default="queued", nullable=False),
        sa.Column("dry_run", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "visibility_scope",
            sa.String(32),
            server_default="organization_private",
            nullable=False,
        ),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("max_requests", sa.Integer(), nullable=False),
        sa.Column("max_download_bytes", sa.Integer(), nullable=False),
        sa.Column("max_response_bytes", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("retry_limit", sa.Integer(), nullable=False),
        sa.Column("max_redirects", sa.Integer(), nullable=False),
        sa.Column("request_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("downloaded_bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("new_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("changed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unchanged_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duplicate_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("external_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("paid_api_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("estimated_cost", sa.Numeric(12, 4), server_default="0", nullable=False),
        sa.Column("robots_status", sa.String(32)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("request_log", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("leased_until", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'partial', 'failed', "
            "'dry_run_completed')",
            name="ck_source_check_run_status",
        ),
        sa.CheckConstraint(
            "visibility_scope = 'organization_private'",
            name="ck_source_check_run_scope",
        ),
        sa.CheckConstraint(
            "max_requests > 0 AND max_download_bytes > 0 AND max_response_bytes > 0 "
            "AND timeout_seconds > 0 AND retry_limit >= 0 AND max_redirects > 0",
            name="ck_source_check_run_limits",
        ),
        sa.CheckConstraint(
            "request_count >= 0 AND downloaded_bytes >= 0 AND new_count >= 0 "
            "AND changed_count >= 0 AND unchanged_count >= 0 "
            "AND duplicate_count >= 0 AND failure_count >= 0",
            name="ck_source_check_run_counts",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["trusted_source_id", "tenant_id", "company_id"],
            ["trusted_sources.id", "trusted_sources.tenant_id", "trusted_sources.company_id"],
            name="fk_source_check_run_source_lineage",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "company_id",
            "trusted_source_id",
            name="uq_source_check_run_scope_lineage",
        ),
    )
    op.create_index(
        "uq_source_check_run_active",
        "source_check_runs",
        ["trusted_source_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
        sqlite_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_source_check_run_tenant_status", "source_check_runs", ["tenant_id", "status"]
    )
    op.create_index(
        "ix_source_check_run_company_created",
        "source_check_runs",
        ["company_id", "created_at"],
    )

    op.create_table(
        "candidate_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("trusted_source_id", sa.Uuid(), nullable=False),
        sa.Column("discovery_run_id", sa.Uuid(), nullable=False),
        sa.Column("previous_candidate_id", sa.Uuid()),
        sa.Column("canonical_url", sa.String(1000), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("first_discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("change_type", sa.String(16), nullable=False),
        sa.Column("link_health_status", sa.String(32), server_default="unchecked", nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("etag", sa.String(500)),
        sa.Column("last_modified", sa.String(200)),
        sa.Column("excerpt", sa.Text()),
        sa.Column("license_status", sa.String(32), nullable=False),
        sa.Column("processing_status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("identity_status_at_discovery", sa.String(32), nullable=False),
        sa.Column(
            "visibility_scope",
            sa.String(32),
            server_default="organization_private",
            nullable=False,
        ),
        sa.Column("document_metadata", sa.JSON(), nullable=False),
        sa.Column("handoff_payload", sa.JSON(), nullable=False),
        sa.Column("processed_by", sa.Uuid()),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("decision_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "change_type IN ('new', 'changed')", name="ck_candidate_document_change_type"
        ),
        sa.CheckConstraint(
            "processing_status IN ('pending', 'worth_research', 'irrelevant', "
            "'duplicate', 'source_unavailable')",
            name="ck_candidate_document_processing_status",
        ),
        sa.CheckConstraint(
            "link_health_status IN ('healthy', 'unchecked', 'broken')",
            name="ck_candidate_document_link_health",
        ),
        sa.CheckConstraint(
            "license_status IN ('public_access', 'permission_confirmed', 'unclear', 'restricted')",
            name="ck_candidate_document_license_status",
        ),
        sa.CheckConstraint(
            "visibility_scope = 'organization_private'",
            name="ck_candidate_document_scope",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["trusted_source_id", "tenant_id", "company_id"],
            ["trusted_sources.id", "trusted_sources.tenant_id", "trusted_sources.company_id"],
            name="fk_candidate_document_source_lineage",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["discovery_run_id", "tenant_id", "company_id", "trusted_source_id"],
            [
                "source_check_runs.id",
                "source_check_runs.tenant_id",
                "source_check_runs.company_id",
                "source_check_runs.trusted_source_id",
            ],
            name="fk_candidate_document_run_lineage",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["previous_candidate_id"], ["candidate_documents.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["processed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trusted_source_id",
            "canonical_url",
            "content_hash",
            name="uq_candidate_document_source_url_hash",
        ),
    )
    op.create_index(
        "ix_candidate_document_tenant_status",
        "candidate_documents",
        ["tenant_id", "processing_status"],
    )
    op.create_index(
        "ix_candidate_document_company_seen",
        "candidate_documents",
        ["company_id", "first_discovered_at"],
    )
    op.create_index(
        "ix_candidate_document_source_hash",
        "candidate_documents",
        ["trusted_source_id", "content_hash"],
    )


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    common = (
        f"{_active_user_clause()} AND ({_platform_admin_clause()}) "
        f"AND {{table}}.tenant_id = {_current_tenant()} "
        "AND {table}.visibility_scope = 'organization_private'"
    )
    company_access = f"""
        EXISTS (
            SELECT 1 FROM companies
            WHERE companies.id = trusted_sources.company_id
              AND (
                  companies.tenant_id = {_current_tenant()}
                  OR (
                      companies.tenant_id IS NULL
                      AND companies.visibility_scope = 'public'
                      AND companies.identity_status = 'verified'
                  )
              )
        )
    """
    clauses = {
        "trusted_sources": f"({common.format(table='trusted_sources')}) AND ({company_access})",
        "source_check_runs": (
            f"({common.format(table='source_check_runs')}) AND EXISTS ("
            "SELECT 1 FROM trusted_sources "
            "WHERE trusted_sources.id = source_check_runs.trusted_source_id "
            "AND trusted_sources.tenant_id = source_check_runs.tenant_id "
            "AND trusted_sources.company_id = source_check_runs.company_id)"
        ),
        "candidate_documents": (
            f"({common.format(table='candidate_documents')}) AND EXISTS ("
            "SELECT 1 FROM trusted_sources "
            "WHERE trusted_sources.id = candidate_documents.trusted_source_id "
            "AND trusted_sources.tenant_id = candidate_documents.tenant_id "
            "AND trusted_sources.company_id = candidate_documents.company_id) AND EXISTS ("
            "SELECT 1 FROM source_check_runs "
            "WHERE source_check_runs.id = candidate_documents.discovery_run_id "
            "AND source_check_runs.tenant_id = candidate_documents.tenant_id "
            "AND source_check_runs.company_id = candidate_documents.company_id "
            "AND source_check_runs.trusted_source_id = candidate_documents.trusted_source_id)"
        ),
    }
    for table_name, clause in clauses.items():
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table_name}_platform_admin_read ON {table_name} "
            f"FOR SELECT USING ({clause})"
        )
        op.execute(
            f"CREATE POLICY {table_name}_platform_admin_insert ON {table_name} "
            f"FOR INSERT WITH CHECK ({clause})"
        )
        op.execute(
            f"CREATE POLICY {table_name}_platform_admin_update ON {table_name} "
            f"FOR UPDATE USING ({clause}) WITH CHECK ({clause})"
        )


def upgrade() -> None:
    _create_tables()
    _enable_rls()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table_name in reversed(("trusted_sources", "source_check_runs", "candidate_documents")):
            for operation in ("update", "insert", "read"):
                op.execute(f"DROP POLICY {table_name}_platform_admin_{operation} ON {table_name}")
            op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
    op.drop_table("candidate_documents")
    op.drop_table("source_check_runs")
    op.drop_table("trusted_sources")
