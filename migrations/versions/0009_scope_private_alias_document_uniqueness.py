"""scope private alias and document uniqueness

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCOPES = (
    "platform_shared",
    "personal_private",
    "organization_private",
    "system_restricted",
)


def _drop_legacy_constraints() -> None:
    with op.batch_alter_table("company_aliases") as batch_op:
        batch_op.drop_constraint("uq_company_alias", type_="unique")

    dialect_name = op.get_bind().dialect.name
    naming_convention = (
        {"uq": "uq_%(table_name)s_%(column_0_name)s"} if dialect_name == "sqlite" else None
    )
    dedupe_constraint = (
        "uq_raw_documents_document_dedupe_key"
        if dialect_name == "sqlite"
        else "raw_documents_document_dedupe_key_key"
    )
    with op.batch_alter_table(
        "raw_documents",
        naming_convention=naming_convention,
    ) as batch_op:
        batch_op.drop_constraint("uq_document_source_record", type_="unique")
        batch_op.drop_constraint(dedupe_constraint, type_="unique")


def _create_scope_indexes() -> None:
    for scope in SCOPES:
        alias_columns = ["company_id", "normalized_alias", "alias_type"]
        document_source_columns = ["source_id", "external_record_id"]
        document_dedupe_columns = ["document_dedupe_key"]
        if scope == "personal_private":
            alias_columns.insert(1, "owner_user_id")
            document_source_columns.insert(1, "owner_user_id")
            document_dedupe_columns.insert(0, "owner_user_id")
        elif scope == "organization_private":
            alias_columns.insert(1, "owner_tenant_id")
            document_source_columns.insert(1, "owner_tenant_id")
            document_dedupe_columns.insert(0, "owner_tenant_id")

        op.create_index(
            f"uq_company_alias_{scope}",
            "company_aliases",
            alias_columns,
            unique=True,
            postgresql_where=sa.text(f"visibility_scope = '{scope}'"),
            sqlite_where=sa.text(f"visibility_scope = '{scope}'"),
        )
        op.create_index(
            f"uq_raw_doc_source_record_{scope}",
            "raw_documents",
            document_source_columns,
            unique=True,
            postgresql_where=sa.text(f"visibility_scope = '{scope}'"),
            sqlite_where=sa.text(f"visibility_scope = '{scope}'"),
        )
        op.create_index(
            f"uq_raw_doc_dedupe_{scope}",
            "raw_documents",
            document_dedupe_columns,
            unique=True,
            postgresql_where=sa.text(f"visibility_scope = '{scope}'"),
            sqlite_where=sa.text(f"visibility_scope = '{scope}'"),
        )


def upgrade() -> None:
    _drop_legacy_constraints()
    _create_scope_indexes()


def _make_legacy_keys_unique() -> None:
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY company_id, normalized_alias, alias_type
                       ORDER BY created_at, id
                   ) AS row_number
            FROM company_aliases
        )
        UPDATE company_aliases
        SET normalized_alias =
            substr(normalized_alias, 1, 200) || ':' ||
            replace(CAST(id AS VARCHAR), '-', '')
        WHERE id IN (SELECT id FROM ranked WHERE row_number > 1)
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY source_id, external_record_id
                       ORDER BY created_at, id
                   ) AS row_number
            FROM raw_documents
        )
        UPDATE raw_documents
        SET external_record_id =
            substr(external_record_id, 1, 120) || ':' ||
            replace(CAST(id AS VARCHAR), '-', '')
        WHERE id IN (SELECT id FROM ranked WHERE row_number > 1)
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY document_dedupe_key
                       ORDER BY created_at, id
                   ) AS row_number
            FROM raw_documents
        )
        UPDATE raw_documents
        SET document_dedupe_key =
            substr(document_dedupe_key, 1, 31) ||
            replace(CAST(id AS VARCHAR), '-', '') || '0'
        WHERE id IN (SELECT id FROM ranked WHERE row_number > 1)
        """
    )


def downgrade() -> None:
    _make_legacy_keys_unique()
    for scope in reversed(SCOPES):
        op.drop_index(f"uq_raw_doc_dedupe_{scope}", table_name="raw_documents")
        op.drop_index(f"uq_raw_doc_source_record_{scope}", table_name="raw_documents")
        op.drop_index(f"uq_company_alias_{scope}", table_name="company_aliases")

    with op.batch_alter_table("raw_documents") as batch_op:
        batch_op.create_unique_constraint(
            "uq_document_source_record",
            ["source_id", "external_record_id"],
        )
        batch_op.create_unique_constraint(
            (
                "uq_raw_documents_document_dedupe_key"
                if op.get_bind().dialect.name == "sqlite"
                else "raw_documents_document_dedupe_key_key"
            ),
            ["document_dedupe_key"],
        )
    with op.batch_alter_table("company_aliases") as batch_op:
        batch_op.create_unique_constraint(
            "uq_company_alias",
            ["company_id", "normalized_alias", "alias_type"],
        )
