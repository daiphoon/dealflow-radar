"""Record curator admission and import selections without rewriting historical evidence."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def _basis(include_curator: bool) -> None:
    choices = (
        "'official_government', 'licensed_business_data', "
        "'exchange_disclosure', 'public_crosscheck'"
    )
    if include_curator:
        choices += ", 'curator_confirmed'"
    with op.batch_alter_table("companies") as batch:
        batch.drop_constraint("ck_company_identity_verification_basis", type_="check")
        batch.create_check_constraint(
            "ck_company_identity_verification_basis",
            f"identity_verification_basis IS NULL OR identity_verification_basis IN ({choices})",
        )


def upgrade() -> None:
    _basis(True)
    with op.batch_alter_table("research_imports") as batch:
        batch.add_column(
            sa.Column("selection_key", sa.String(64), nullable=False, server_default="")
        )
        batch.drop_constraint("uq_research_import_file_parser", type_="unique")
        batch.create_unique_constraint(
            "uq_research_import_file_parser",
            ["tenant_id", "file_hash", "parser_version", "selection_key"],
        )
    with op.batch_alter_table("company_snapshots") as batch:
        batch.alter_column(
            "last_checked_at", existing_type=sa.DateTime(timezone=True), nullable=True
        )
    if op.get_bind().dialect.name != "postgresql":
        return
    previous = importlib.import_module(
        "migrations.versions.0010_add_controlled_shared_fact_promotion"
    )
    admin = (
        f"({previous._active_user_clause()}) AND "
        f"({previous._active_role_clause(('platform_admin',))})"
    )
    public_company = "tenant_id IS NULL AND visibility_scope='public'"
    op.execute(
        "CREATE POLICY companies_curator_insert ON companies FOR INSERT WITH CHECK ("
        f"({admin}) AND {public_company} AND identity_status='verified' "
        "AND identity_verification_basis='curator_confirmed')"
    )
    op.execute(
        "CREATE POLICY companies_curator_update ON companies FOR UPDATE "
        f"USING (({admin}) AND {public_company}) "
        f"WITH CHECK (({admin}) AND {public_company})"
    )
    guard = f"identity_verification_basis IS DISTINCT FROM 'curator_confirmed' OR ({admin})"
    op.execute(
        "CREATE POLICY companies_curator_guard_insert ON companies AS RESTRICTIVE "
        f"FOR INSERT WITH CHECK ({guard})"
    )
    op.execute(
        "CREATE POLICY companies_curator_guard_update ON companies AS RESTRICTIVE "
        f"FOR UPDATE USING ({guard}) WITH CHECK ({guard})"
    )
    op.execute(
        "CREATE POLICY company_aliases_curator_insert ON company_aliases FOR INSERT "
        f"WITH CHECK (({admin}) AND visibility_scope='platform_shared' "
        "AND owner_user_id IS NULL AND owner_tenant_id IS NULL "
        "AND verification_status='verified')"
    )
    own_import = (
        f"({admin}) AND tenant_id={previous._current_tenant()} AND parser_version='curated-xlsx-v1'"
    )
    op.execute(
        "CREATE POLICY research_imports_curator ON research_imports "
        f"USING ({own_import}) WITH CHECK ({own_import} "
        f"AND imported_by={previous._current_user()})"
    )
    op.execute(
        "CREATE POLICY research_imports_curator_guard ON research_imports AS RESTRICTIVE "
        f"USING (parser_version <> 'curated-xlsx-v1' OR ({admin})) "
        f"WITH CHECK (parser_version <> 'curated-xlsx-v1' OR ({admin}))"
    )


def downgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("Inspect curated import evidence before downgrade")
    connection = op.get_bind()
    if any(
        connection.scalar(sa.text(query))
        for query in (
            "SELECT count(*) FROM companies WHERE identity_verification_basis='curator_confirmed'",
            "SELECT count(*) FROM research_imports WHERE selection_key <> ''",
            "SELECT count(*) FROM company_snapshots WHERE last_checked_at IS NULL",
        )
    ):
        raise RuntimeError("Cannot downgrade 0032 with curated evidence or unchecked snapshots")
    if connection.dialect.name == "postgresql":
        for table, names in {
            "companies": (
                "curator_insert",
                "curator_update",
                "curator_guard_insert",
                "curator_guard_update",
            ),
            "company_aliases": ("curator_insert",),
            "research_imports": ("curator", "curator_guard"),
        }.items():
            for name in names:
                op.execute(f"DROP POLICY {table}_{name} ON {table}")
    with op.batch_alter_table("company_snapshots") as batch:
        batch.alter_column(
            "last_checked_at", existing_type=sa.DateTime(timezone=True), nullable=False
        )
    with op.batch_alter_table("research_imports") as batch:
        batch.drop_constraint("uq_research_import_file_parser", type_="unique")
        batch.drop_column("selection_key")
        batch.create_unique_constraint(
            "uq_research_import_file_parser", ["tenant_id", "file_hash", "parser_version"]
        )
    _basis(False)
