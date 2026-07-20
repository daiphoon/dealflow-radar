"""add CloudBase identity mapping and authentication audit

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def _create_postgresql_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE authentication_audit_logs ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY authentication_audit_logs_insert
        ON authentication_audit_logs
        FOR INSERT
        WITH CHECK (
            user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            AND tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY authentication_audit_logs_read
        ON authentication_audit_logs
        FOR SELECT
        USING (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
            AND (
                user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
                OR EXISTS (
                    SELECT 1
                    FROM user_role_assignments AS assignment
                    JOIN roles ON roles.id = assignment.role_id
                    JOIN users ON users.id = assignment.user_id
                    WHERE assignment.user_id =
                        NULLIF(current_setting('app.current_user_id', true), '')::uuid
                      AND users.tenant_id = authentication_audit_logs.tenant_id
                      AND users.status = 'active'
                      AND roles.code = 'platform_admin'
                      AND (
                          assignment.valid_until IS NULL
                          OR assignment.valid_until > CURRENT_TIMESTAMP
                      )
                )
            )
        )
        """
    )


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("auth_provider", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("auth_subject", sa.String(255), nullable=True))
        batch_op.create_check_constraint(
            "ck_user_auth_identity_pair",
            "(auth_provider IS NULL AND auth_subject IS NULL) OR "
            "(auth_provider = 'cloudbase' AND auth_subject IS NOT NULL)",
        )
    op.create_index(
        "uq_users_auth_identity",
        "users",
        ["auth_provider", "auth_subject"],
        unique=True,
        postgresql_where=sa.text("auth_provider IS NOT NULL AND auth_subject IS NOT NULL"),
        sqlite_where=sa.text("auth_provider IS NOT NULL AND auth_subject IS NOT NULL"),
    )

    op.create_table(
        "authentication_audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("subject_hash", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('identity_linked', 'session_started', "
            "'session_refreshed', 'session_ended')",
            name="ck_authentication_audit_event_type",
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'failed')",
            name="ck_authentication_audit_outcome",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_authentication_audit_tenant_created",
        "authentication_audit_logs",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_authentication_audit_user_created",
        "authentication_audit_logs",
        ["user_id", "created_at"],
    )
    _create_postgresql_rls()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY authentication_audit_logs_read ON authentication_audit_logs")
        op.execute("DROP POLICY authentication_audit_logs_insert ON authentication_audit_logs")
        op.execute("ALTER TABLE authentication_audit_logs DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "ix_authentication_audit_user_created",
        table_name="authentication_audit_logs",
    )
    op.drop_index(
        "ix_authentication_audit_tenant_created",
        table_name="authentication_audit_logs",
    )
    op.drop_table("authentication_audit_logs")
    op.drop_index("uq_users_auth_identity", table_name="users")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_user_auth_identity_pair", type_="check")
        batch_op.drop_column("auth_subject")
        batch_op.drop_column("auth_provider")
