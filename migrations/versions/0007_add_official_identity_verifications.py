"""add official identity verifications

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _active_role_clause(role_codes: tuple[str, ...]) -> str:
    quoted_roles = ", ".join(f"'{role}'" for role in role_codes)
    return f"""
        tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        AND EXISTS (
            SELECT 1
            FROM user_role_assignments AS assignment
            JOIN roles ON roles.id = assignment.role_id
            JOIN users ON users.id = assignment.user_id
            WHERE assignment.user_id =
                  NULLIF(current_setting('app.current_user_id', true), '')::uuid
              AND roles.code IN ({quoted_roles})
              AND users.tenant_id = official_identity_verifications.tenant_id
              AND users.status = 'active'
              AND (
                  assignment.valid_until IS NULL
                  OR assignment.valid_until > CURRENT_TIMESTAMP
              )
        )
    """


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("last_identity_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "official_identity_verifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("raw_document_id", sa.Uuid(), nullable=False),
        sa.Column("query_text", sa.String(length=240), nullable=False),
        sa.Column("legal_name", sa.String(length=240), nullable=False),
        sa.Column("credit_code", sa.String(length=18), nullable=False),
        sa.Column("registered_region", sa.String(length=120), nullable=True),
        sa.Column("registration_status", sa.String(length=64), nullable=False),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("match_rule", sa.String(length=80), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "verification_status IN ('verified', 'conflict', 'unmatched')",
            name="ck_official_identity_status",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["raw_document_id"], ["raw_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("raw_document_id", name="uq_official_identity_document"),
    )
    op.create_index(
        op.f("ix_official_identity_verifications_company_id"),
        "official_identity_verifications",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_official_identity_verifications_credit_code"),
        "official_identity_verifications",
        ["credit_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_official_identity_verifications_query_text"),
        "official_identity_verifications",
        ["query_text"],
        unique=False,
    )
    op.create_index(
        "ix_official_identity_tenant_status_checked",
        "official_identity_verifications",
        ["tenant_id", "verification_status", "checked_at"],
        unique=False,
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE official_identity_verifications ENABLE ROW LEVEL SECURITY")
        op.execute(
            "CREATE POLICY official_identity_verifications_read "
            "ON official_identity_verifications FOR SELECT USING ("
            f"{_active_role_clause(('institution_admin', 'reviewer'))})"
        )
        admin_clause = _active_role_clause(("institution_admin",))
        op.execute(
            "CREATE POLICY official_identity_verifications_insert "
            "ON official_identity_verifications FOR INSERT WITH CHECK ("
            f"{admin_clause})"
        )
        op.execute(
            "CREATE POLICY official_identity_verifications_update "
            "ON official_identity_verifications FOR UPDATE USING ("
            f"{admin_clause}) WITH CHECK ({admin_clause})"
        )


def downgrade() -> None:
    op.drop_index(
        "ix_official_identity_tenant_status_checked",
        table_name="official_identity_verifications",
    )
    op.drop_index(
        op.f("ix_official_identity_verifications_query_text"),
        table_name="official_identity_verifications",
    )
    op.drop_index(
        op.f("ix_official_identity_verifications_credit_code"),
        table_name="official_identity_verifications",
    )
    op.drop_index(
        op.f("ix_official_identity_verifications_company_id"),
        table_name="official_identity_verifications",
    )
    op.drop_table("official_identity_verifications")
    op.drop_column("companies", "last_identity_checked_at")
