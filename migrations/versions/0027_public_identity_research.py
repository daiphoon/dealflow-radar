"""Add private, resumable public identity research without changing historical evidence."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def _basis(include_public: bool) -> None:
    choices = "'official_government', 'licensed_business_data', 'exchange_disclosure'"
    if include_public:
        choices += ", 'public_crosscheck'"
    with op.batch_alter_table("companies") as batch:
        batch.drop_constraint("ck_company_identity_verification_basis", type_="check")
        batch.create_check_constraint(
            "ck_company_identity_verification_basis",
            f"identity_verification_basis IS NULL OR identity_verification_basis IN ({choices})",
        )


def upgrade() -> None:
    _basis(True)
    op.create_table(
        "identity_research_states",
        sa.Column("request_id", sa.Uuid(), primary_key=True),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"], ["personal_company_requests.id"], ondelete="CASCADE"
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        previous = importlib.import_module(
            "migrations.versions.0023_add_bounded_web_research_cache"
        )
        allowed = f"({previous._active_user_clause()}) AND ({previous._platform_admin_clause()})"
        op.execute(
            "CREATE POLICY usage_identity_private ON usage_ledger AS RESTRICTIVE FOR SELECT "
            f"USING (COALESCE(metrics->>'stage', '') <> 'identity' OR ({allowed}))"
        )
        op.execute(
            "CREATE POLICY companies_public_identity_insert ON companies FOR INSERT WITH CHECK ("
            f"({allowed}) AND tenant_id IS NULL AND visibility_scope='public' "
            "AND identity_status='verified' AND identity_verification_basis='public_crosscheck')"
        )
        op.execute("ALTER TABLE identity_research_states ENABLE ROW LEVEL SECURITY")
        for action in ("SELECT", "INSERT", "UPDATE"):
            clause = f"WITH CHECK ({allowed})" if action == "INSERT" else f"USING ({allowed})"
            if action == "UPDATE":
                clause += f" WITH CHECK ({allowed})"
            op.execute(
                f"CREATE POLICY identity_research_admin_{action.lower()} "
                f"ON identity_research_states FOR {action} {clause}"
            )


def downgrade() -> None:
    connection = op.get_bind()
    usage = sa.table("usage_ledger", sa.column("metrics", sa.JSON()))
    identity_usage = connection.scalar(
        sa.select(sa.func.count())
        .select_from(usage)
        .where(usage.c.metrics["stage"].as_string() == "identity")
    )
    if (
        connection.scalar(sa.text("SELECT count(*) FROM identity_research_states"))
        or connection.scalar(
            sa.text(
                "SELECT count(*) FROM companies "
                "WHERE identity_verification_basis='public_crosscheck'"
            )
        )
        or identity_usage
    ):
        raise RuntimeError(
            "Identity research evidence exists; retain schema and assess backup first"
        )
    op.drop_table("identity_research_states")
    if connection.dialect.name == "postgresql":
        op.execute("DROP POLICY companies_public_identity_insert ON companies")
        op.execute("DROP POLICY usage_identity_private ON usage_ledger")
    _basis(False)
