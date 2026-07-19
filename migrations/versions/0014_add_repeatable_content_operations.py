"""add repeatable content handoff and scheduled source run metadata

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("source_check_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "trigger_type",
                sa.String(32),
                nullable=False,
                server_default="manual",
            )
        )
        batch_op.add_column(sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_check_constraint(
            "ck_source_check_run_trigger_type",
            "trigger_type IN ('manual', 'scheduled')",
        )

    with op.batch_alter_table("candidate_documents") as batch_op:
        batch_op.create_unique_constraint(
            "uq_candidate_document_owner_lineage",
            ["id", "tenant_id"],
        )

    with op.batch_alter_table("raw_documents") as batch_op:
        batch_op.add_column(sa.Column("candidate_document_id", sa.Uuid(), nullable=True))
        batch_op.create_check_constraint(
            "ck_raw_document_candidate_scope",
            "candidate_document_id IS NULL OR ("
            "visibility_scope = 'organization_private' "
            "AND owner_user_id IS NULL AND owner_tenant_id IS NOT NULL)",
        )
        batch_op.create_foreign_key(
            "fk_raw_document_candidate_owner",
            "candidate_documents",
            ["candidate_document_id", "owner_tenant_id"],
            ["id", "tenant_id"],
        )
    op.create_index(
        "uq_raw_document_candidate_handoff",
        "raw_documents",
        ["candidate_document_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_raw_document_candidate_handoff", table_name="raw_documents")
    with op.batch_alter_table("raw_documents") as batch_op:
        batch_op.drop_constraint("fk_raw_document_candidate_owner", type_="foreignkey")
        batch_op.drop_constraint("ck_raw_document_candidate_scope", type_="check")
        batch_op.drop_column("candidate_document_id")

    with op.batch_alter_table("candidate_documents") as batch_op:
        batch_op.drop_constraint("uq_candidate_document_owner_lineage", type_="unique")

    with op.batch_alter_table("source_check_runs") as batch_op:
        batch_op.drop_constraint("ck_source_check_run_trigger_type", type_="check")
        batch_op.drop_column("scheduled_for")
        batch_op.drop_column("trigger_type")
