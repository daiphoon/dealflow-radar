"""add on-demand company research request and queue foundation

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ACTIVE_REQUEST_STATUSES = (
    "pending",
    "in_review",
    "identity_queued",
    "identity_checking",
    "awaiting_confirmation",
    "research_queued",
    "researching",
    "partial",
    "budget_deferred",
    "cancel_requested",
)


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


def _active_request_sql() -> str:
    return ", ".join(f"'{status}'" for status in ACTIVE_REQUEST_STATUSES)


def _create_tables() -> None:
    op.create_table(
        "company_research_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_stage", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("coverage", sa.JSON(), nullable=False),
        sa.Column("external_calls", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'partial', 'budget_deferred', "
            "'completed', 'cancelled', 'failed')",
            name="ck_company_research_job_status",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_company_research_jobs_company_id",
        "company_research_jobs",
        ["company_id"],
    )
    op.create_index(
        "ix_company_research_jobs_created_by_user_id",
        "company_research_jobs",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_company_research_jobs_status",
        "company_research_jobs",
        ["status"],
    )
    op.create_index(
        "ix_company_research_job_status_created",
        "company_research_jobs",
        ["status", "created_at"],
    )
    op.create_index(
        "uq_company_research_job_active",
        "company_research_jobs",
        ["company_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'partial', 'budget_deferred')"),
        sqlite_where=sa.text("status IN ('queued', 'running', 'partial', 'budget_deferred')"),
    )

    op.create_table(
        "personal_quota_increase_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("requested_daily_extra", sa.Integer(), nullable=False),
        sa.Column("requested_monthly_extra", sa.Integer(), nullable=False),
        sa.Column("request_reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("approved_daily_extra", sa.Integer(), nullable=False),
        sa.Column("approved_monthly_extra", sa.Integer(), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired')",
            name="ck_personal_quota_increase_status",
        ),
        sa.CheckConstraint(
            "requested_daily_extra > 0 OR requested_monthly_extra > 0",
            name="ck_personal_quota_increase_requested_positive",
        ),
        sa.CheckConstraint(
            "approved_daily_extra >= 0 AND approved_monthly_extra >= 0",
            name="ck_personal_quota_increase_approved_non_negative",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_personal_quota_increase_requests_owner_user_id",
        "personal_quota_increase_requests",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_personal_quota_increase_requests_status",
        "personal_quota_increase_requests",
        ["status"],
    )
    op.create_index(
        "ix_personal_quota_increase_status_created",
        "personal_quota_increase_requests",
        ["status", "created_at"],
    )
    op.create_index(
        "uq_personal_quota_increase_owner_pending",
        "personal_quota_increase_requests",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )


def _alter_existing_tables() -> None:
    with op.batch_alter_table("personal_usage_records") as batch_op:
        batch_op.add_column(sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("void_reason", sa.String(80), nullable=True))

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP POLICY personal_company_requests_owner_insert ON personal_company_requests"
        )
    op.drop_index(
        "uq_personal_company_request_active_target",
        table_name="personal_company_requests",
    )
    with op.batch_alter_table("personal_company_requests") as batch_op:
        batch_op.drop_constraint("ck_personal_company_request_status", type_="check")
        batch_op.drop_constraint("ck_personal_company_request_target", type_="check")
        batch_op.alter_column(
            "status",
            existing_type=sa.String(16),
            type_=sa.String(32),
            existing_nullable=False,
        )
        batch_op.add_column(sa.Column("research_job_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("resolved_legal_name", sa.String(240), nullable=True))
        batch_op.add_column(sa.Column("resolved_credit_code", sa.String(18), nullable=True))
        batch_op.add_column(sa.Column("resolved_registered_region", sa.String(120), nullable=True))
        batch_op.add_column(sa.Column("resolved_registration_status", sa.String(64), nullable=True))
        batch_op.add_column(
            sa.Column("resolved_registration_authority", sa.String(240), nullable=True)
        )
        batch_op.add_column(sa.Column("provider_company_id", sa.String(80), nullable=True))
        batch_op.add_column(sa.Column("identity_response_hash", sa.String(64), nullable=True))
        batch_op.add_column(
            sa.Column("identity_checked_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("confirmation_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("external_calls", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("cache_hits", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("cancellation_stage", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("cancellation_reason", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("last_error_code", sa.String(80), nullable=True))
        batch_op.create_foreign_key(
            "fk_personal_company_requests_research_job_id",
            "company_research_jobs",
            ["research_job_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            "ck_personal_company_request_status",
            "status IN ('pending', 'in_review', 'identity_queued', "
            "'identity_checking', 'awaiting_confirmation', 'needs_input', "
            "'research_queued', 'researching', 'partial', 'budget_deferred', "
            "'cancel_requested', 'cancelled', 'completed', 'rejected', 'failed')",
        )
        batch_op.create_check_constraint(
            "ck_personal_company_request_target",
            "(request_type = 'inclusion' "
            "AND (requested_name IS NOT NULL OR requested_credit_code IS NOT NULL)) OR "
            "(request_type = 'refresh' AND "
            "(company_id IS NOT NULL OR requested_name IS NOT NULL "
            "OR requested_credit_code IS NOT NULL))",
        )
    op.create_index(
        "ix_personal_company_requests_research_job_id",
        "personal_company_requests",
        ["research_job_id"],
    )
    op.create_index(
        "uq_personal_company_request_active_target",
        "personal_company_requests",
        ["owner_user_id", "target_key"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({_active_request_sql()})"),
        sqlite_where=sa.text(f"status IN ({_active_request_sql()})"),
    )


def _enable_postgresql_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    active_user = _active_user_clause()
    platform_admin = _platform_admin_clause()
    owner_record = f"({active_user}) AND owner_user_id = {_current_user()}"

    op.execute(
        "CREATE POLICY personal_company_requests_owner_insert "
        "ON personal_company_requests FOR INSERT "
        f"WITH CHECK ({owner_record} AND status IN ('pending', 'identity_queued') "
        "AND reviewed_by_id IS NULL)"
    )
    op.execute(
        "CREATE POLICY personal_company_requests_owner_update "
        "ON personal_company_requests FOR UPDATE "
        f"USING ({owner_record}) WITH CHECK ({owner_record})"
    )
    op.execute(
        "CREATE POLICY personal_usage_records_owner_update "
        "ON personal_usage_records FOR UPDATE "
        f"USING ({owner_record}) WITH CHECK ({owner_record})"
    )
    on_demand_usage = """
        operation = 'company_request'
        AND resource_id IS NOT NULL
        AND EXISTS (
            SELECT 1 FROM personal_company_requests AS linked_request
            WHERE linked_request.id = personal_usage_records.resource_id
        )
    """
    op.execute(
        "CREATE POLICY personal_usage_records_on_demand_admin_read "
        "ON personal_usage_records FOR SELECT "
        f"USING (({active_user}) AND ({platform_admin}) AND ({on_demand_usage}))"
    )
    op.execute(
        "CREATE POLICY personal_usage_records_on_demand_admin_update "
        "ON personal_usage_records FOR UPDATE "
        f"USING (({active_user}) AND ({platform_admin}) AND ({on_demand_usage})) "
        f"WITH CHECK (({active_user}) AND ({platform_admin}) AND ({on_demand_usage}))"
    )
    op.execute(
        "CREATE POLICY usage_ledger_tianyancha_platform_admin_read "
        "ON usage_ledger FOR SELECT "
        f"USING (({active_user}) AND ({platform_admin}) "
        "AND provider LIKE 'tianyancha%')"
    )

    op.execute("ALTER TABLE personal_quota_increase_requests ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY personal_quota_increase_owner_read "
        "ON personal_quota_increase_requests FOR SELECT "
        f"USING ({owner_record})"
    )
    op.execute(
        "CREATE POLICY personal_quota_increase_owner_insert "
        "ON personal_quota_increase_requests FOR INSERT "
        f"WITH CHECK ({owner_record} AND status = 'pending' AND reviewed_by_id IS NULL)"
    )
    op.execute(
        "CREATE POLICY personal_quota_increase_admin_read "
        "ON personal_quota_increase_requests FOR SELECT "
        f"USING (({active_user}) AND ({platform_admin}))"
    )
    op.execute(
        "CREATE POLICY personal_quota_increase_admin_update "
        "ON personal_quota_increase_requests FOR UPDATE "
        f"USING (({active_user}) AND ({platform_admin})) "
        f"WITH CHECK (({active_user}) AND ({platform_admin}))"
    )

    op.execute("ALTER TABLE company_research_jobs ENABLE ROW LEVEL SECURITY")
    requester_read = f"""
        ({active_user}) AND (
            created_by_user_id = {_current_user()}
            OR EXISTS (
                SELECT 1 FROM personal_company_requests AS requester_link
                WHERE requester_link.research_job_id = company_research_jobs.id
                  AND requester_link.owner_user_id = {_current_user()}
            )
        )
    """
    op.execute(
        "CREATE POLICY company_research_jobs_requester_read "
        f"ON company_research_jobs FOR SELECT USING ({requester_read})"
    )
    op.execute(
        "CREATE POLICY company_research_jobs_platform_admin_read "
        "ON company_research_jobs FOR SELECT "
        f"USING (({active_user}) AND ({platform_admin}))"
    )
    op.execute(
        "CREATE POLICY company_research_jobs_platform_admin_insert "
        "ON company_research_jobs FOR INSERT "
        f"WITH CHECK (({active_user}) AND ({platform_admin}) AND EXISTS ("
        "SELECT 1 FROM companies WHERE companies.id = company_research_jobs.company_id "
        "AND companies.tenant_id IS NULL AND companies.visibility_scope = 'public'))"
    )
    op.execute(
        "CREATE POLICY company_research_jobs_platform_admin_update "
        "ON company_research_jobs FOR UPDATE "
        f"USING (({active_user}) AND ({platform_admin})) "
        f"WITH CHECK (({active_user}) AND ({platform_admin}))"
    )

    tianyancha_system_document = (
        "raw_documents.visibility_scope = 'system_restricted' "
        "AND raw_documents.owner_user_id IS NULL "
        "AND raw_documents.owner_tenant_id IS NULL "
        "AND EXISTS (SELECT 1 FROM sources "
        "WHERE sources.id = raw_documents.source_id "
        "AND sources.code = 'tianyancha_licensed_business_data')"
    )
    op.execute(
        "CREATE POLICY raw_documents_tianyancha_admin_read ON raw_documents FOR SELECT "
        f"USING (({active_user}) AND ({platform_admin}) AND ({tianyancha_system_document}))"
    )
    op.execute(
        "CREATE POLICY raw_documents_tianyancha_admin_insert ON raw_documents FOR INSERT "
        f"WITH CHECK (({active_user}) AND ({platform_admin}) "
        f"AND ({tianyancha_system_document}))"
    )
    op.execute(
        "CREATE POLICY official_identity_verifications_platform_admin_read "
        "ON official_identity_verifications FOR SELECT "
        f"USING (({active_user}) AND ({platform_admin}) "
        "AND verification_basis = 'licensed_business_data')"
    )
    op.execute(
        "CREATE POLICY official_identity_verifications_platform_admin_insert "
        "ON official_identity_verifications FOR INSERT "
        f"WITH CHECK (({active_user}) AND ({platform_admin}) "
        f"AND tenant_id = {_current_tenant()} "
        "AND verification_basis = 'licensed_business_data')"
    )
    op.execute(
        "CREATE POLICY official_identity_verifications_platform_admin_update "
        "ON official_identity_verifications FOR UPDATE "
        f"USING (({active_user}) AND ({platform_admin}) "
        f"AND tenant_id = {_current_tenant()} "
        "AND verification_basis = 'licensed_business_data') "
        f"WITH CHECK (({active_user}) AND ({platform_admin}) "
        f"AND tenant_id = {_current_tenant()} "
        "AND verification_basis = 'licensed_business_data')"
    )


def upgrade() -> None:
    _create_tables()
    _alter_existing_tables()
    _enable_postgresql_rls()


def _disable_postgresql_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for statement in (
        "DROP POLICY official_identity_verifications_platform_admin_update "
        "ON official_identity_verifications",
        "DROP POLICY official_identity_verifications_platform_admin_insert "
        "ON official_identity_verifications",
        "DROP POLICY official_identity_verifications_platform_admin_read "
        "ON official_identity_verifications",
        "DROP POLICY raw_documents_tianyancha_admin_insert ON raw_documents",
        "DROP POLICY raw_documents_tianyancha_admin_read ON raw_documents",
        "DROP POLICY personal_usage_records_owner_update ON personal_usage_records",
        "DROP POLICY personal_usage_records_on_demand_admin_update ON personal_usage_records",
        "DROP POLICY personal_usage_records_on_demand_admin_read ON personal_usage_records",
        "DROP POLICY usage_ledger_tianyancha_platform_admin_read ON usage_ledger",
        "DROP POLICY personal_company_requests_owner_update ON personal_company_requests",
    ):
        op.execute(statement)
    op.execute("DROP POLICY personal_company_requests_owner_insert ON personal_company_requests")
    for policy in (
        "company_research_jobs_platform_admin_update",
        "company_research_jobs_platform_admin_insert",
        "company_research_jobs_platform_admin_read",
        "company_research_jobs_requester_read",
    ):
        op.execute(f"DROP POLICY {policy} ON company_research_jobs")
    op.execute("ALTER TABLE company_research_jobs DISABLE ROW LEVEL SECURITY")
    for policy in (
        "personal_quota_increase_admin_update",
        "personal_quota_increase_admin_read",
        "personal_quota_increase_owner_insert",
        "personal_quota_increase_owner_read",
    ):
        op.execute(f"DROP POLICY {policy} ON personal_quota_increase_requests")
    op.execute("ALTER TABLE personal_quota_increase_requests DISABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    _disable_postgresql_rls()
    op.execute(
        "UPDATE personal_company_requests SET status = CASE "
        "WHEN status = 'completed' THEN 'completed' "
        "WHEN status IN ('rejected', 'cancelled', 'failed', 'needs_input') THEN 'rejected' "
        "ELSE 'pending' END"
    )
    op.execute(
        "UPDATE personal_company_requests SET company_id = NULL WHERE request_type = 'inclusion'"
    )
    op.drop_index(
        "uq_personal_company_request_active_target",
        table_name="personal_company_requests",
    )
    op.drop_index(
        "ix_personal_company_requests_research_job_id",
        table_name="personal_company_requests",
    )
    with op.batch_alter_table("personal_company_requests") as batch_op:
        batch_op.drop_constraint("ck_personal_company_request_status", type_="check")
        batch_op.drop_constraint("ck_personal_company_request_target", type_="check")
        batch_op.drop_constraint(
            "fk_personal_company_requests_research_job_id",
            type_="foreignkey",
        )
        for column_name in (
            "last_error_code",
            "heartbeat_at",
            "leased_until",
            "cancellation_stage",
            "cancellation_reason",
            "cancelled_at",
            "cancel_requested_at",
            "cache_hits",
            "external_calls",
            "confirmed_at",
            "confirmation_expires_at",
            "identity_checked_at",
            "identity_response_hash",
            "provider_company_id",
            "resolved_registration_authority",
            "resolved_registration_status",
            "resolved_registered_region",
            "resolved_credit_code",
            "resolved_legal_name",
            "research_job_id",
        ):
            batch_op.drop_column(column_name)
        batch_op.alter_column(
            "status",
            existing_type=sa.String(32),
            type_=sa.String(16),
            existing_nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_personal_company_request_status",
            "status IN ('pending', 'in_review', 'completed', 'rejected')",
        )
        batch_op.create_check_constraint(
            "ck_personal_company_request_target",
            "(request_type = 'inclusion' AND company_id IS NULL "
            "AND (requested_name IS NOT NULL OR requested_credit_code IS NOT NULL)) OR "
            "(request_type = 'refresh' AND "
            "(company_id IS NOT NULL OR requested_name IS NOT NULL "
            "OR requested_credit_code IS NOT NULL))",
        )
    op.create_index(
        "uq_personal_company_request_active_target",
        "personal_company_requests",
        ["owner_user_id", "target_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'in_review')"),
        sqlite_where=sa.text("status IN ('pending', 'in_review')"),
    )
    if op.get_bind().dialect.name == "postgresql":
        active_user = _active_user_clause()
        owner_record = f"({active_user}) AND owner_user_id = {_current_user()}"
        op.execute(
            "CREATE POLICY personal_company_requests_owner_insert "
            "ON personal_company_requests FOR INSERT "
            f"WITH CHECK ({owner_record} AND status = 'pending' AND reviewed_by_id IS NULL)"
        )

    op.drop_index(
        "uq_personal_quota_increase_owner_pending",
        table_name="personal_quota_increase_requests",
    )
    op.drop_index(
        "ix_personal_quota_increase_status_created",
        table_name="personal_quota_increase_requests",
    )
    op.drop_index(
        "ix_personal_quota_increase_requests_status",
        table_name="personal_quota_increase_requests",
    )
    op.drop_index(
        "ix_personal_quota_increase_requests_owner_user_id",
        table_name="personal_quota_increase_requests",
    )
    op.drop_table("personal_quota_increase_requests")

    op.drop_index("uq_company_research_job_active", table_name="company_research_jobs")
    op.drop_index(
        "ix_company_research_job_status_created",
        table_name="company_research_jobs",
    )
    op.drop_index("ix_company_research_jobs_status", table_name="company_research_jobs")
    op.drop_index(
        "ix_company_research_jobs_created_by_user_id",
        table_name="company_research_jobs",
    )
    op.drop_index(
        "ix_company_research_jobs_company_id",
        table_name="company_research_jobs",
    )
    op.drop_table("company_research_jobs")

    with op.batch_alter_table("personal_usage_records") as batch_op:
        batch_op.drop_column("void_reason")
        batch_op.drop_column("voided_at")
