"""add research imports

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_imports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("imported_by", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("batch_id", sa.String(length=120), nullable=False),
        sa.Column("queried_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("research_tool", sa.String(length=80), nullable=False),
        sa.Column("agent_name", sa.String(length=120), nullable=True),
        sa.Column("original_query", sa.Text(), nullable=False),
        sa.Column("target_company_hint", sa.String(length=240), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("file_format", sa.String(length=16), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=16), nullable=False),
        sa.Column("license_status", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("resolved_count", sa.Integer(), nullable=False),
        sa.Column("unresolved_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["imported_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "batch_id", name="uq_research_import_batch"),
        sa.UniqueConstraint(
            "tenant_id",
            "file_hash",
            "parser_version",
            name="uq_research_import_file_parser",
        ),
    )
    op.create_index(
        op.f("ix_research_imports_status"),
        "research_imports",
        ["status"],
        unique=False,
    )
    with op.batch_alter_table("raw_documents") as batch_op:
        batch_op.add_column(sa.Column("research_import_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_raw_documents_research_import_id",
            "research_imports",
            ["research_import_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            op.f("ix_raw_documents_research_import_id"),
            ["research_import_id"],
            unique=False,
        )
    with op.batch_alter_table("review_queue") as batch_op:
        batch_op.add_column(sa.Column("entity_mention_id", sa.Uuid(), nullable=True))
        batch_op.alter_column(
            "event_id",
            existing_type=sa.Uuid(),
            nullable=True,
        )
        batch_op.create_foreign_key(
            "fk_review_queue_entity_mention_id",
            "entity_mentions",
            ["entity_mention_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_unique_constraint(
            "uq_review_queue_entity_mention",
            ["entity_mention_id"],
        )
        batch_op.create_check_constraint(
            "ck_review_queue_subject",
            "(event_id IS NOT NULL AND entity_mention_id IS NULL) OR "
            "(event_id IS NULL AND entity_mention_id IS NOT NULL)",
        )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE research_imports ENABLE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY research_imports_admin ON research_imports
            USING (
                tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                AND EXISTS (
                    SELECT 1
                    FROM user_role_assignments AS assignment
                    JOIN roles ON roles.id = assignment.role_id
                    JOIN users ON users.id = assignment.user_id
                    WHERE assignment.user_id =
                          NULLIF(current_setting('app.current_user_id', true), '')::uuid
                      AND roles.code = 'institution_admin'
                      AND users.tenant_id = research_imports.tenant_id
                      AND users.status = 'active'
                      AND (
                          assignment.valid_until IS NULL
                          OR assignment.valid_until > CURRENT_TIMESTAMP
                      )
                )
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                AND imported_by =
                    NULLIF(current_setting('app.current_user_id', true), '')::uuid
                AND EXISTS (
                    SELECT 1
                    FROM user_role_assignments AS assignment
                    JOIN roles ON roles.id = assignment.role_id
                    JOIN users ON users.id = assignment.user_id
                    WHERE assignment.user_id =
                          NULLIF(current_setting('app.current_user_id', true), '')::uuid
                      AND roles.code = 'institution_admin'
                      AND users.tenant_id = research_imports.tenant_id
                      AND users.status = 'active'
                      AND (
                          assignment.valid_until IS NULL
                          OR assignment.valid_until > CURRENT_TIMESTAMP
                      )
                )
            )
            """
        )


def downgrade() -> None:
    op.execute("DELETE FROM review_queue WHERE event_id IS NULL")
    with op.batch_alter_table("review_queue") as batch_op:
        batch_op.drop_constraint("ck_review_queue_subject", type_="check")
        batch_op.drop_constraint("uq_review_queue_entity_mention", type_="unique")
        batch_op.drop_constraint("fk_review_queue_entity_mention_id", type_="foreignkey")
        batch_op.alter_column(
            "event_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )
        batch_op.drop_column("entity_mention_id")
    with op.batch_alter_table("raw_documents") as batch_op:
        batch_op.drop_index(op.f("ix_raw_documents_research_import_id"))
        batch_op.drop_constraint("fk_raw_documents_research_import_id", type_="foreignkey")
        batch_op.drop_column("research_import_id")
    op.drop_index(op.f("ix_research_imports_status"), table_name="research_imports")
    op.drop_table("research_imports")
