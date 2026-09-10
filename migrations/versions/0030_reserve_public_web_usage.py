"""Persist public research reservations without inventing historical prices."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("cost_status", sa.String(24), nullable=False, server_default="unknown"),
    sa.Column("usage_state", sa.String(24), nullable=False, server_default="legacy"),
    sa.Column("quota_scope", sa.String(32)),
    sa.Column("task_key", sa.String(96)),
    sa.Column("subject_key", sa.String(64)),
    sa.Column("reserved_calls", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("reserved_cost", sa.Numeric(18, 6)),
    sa.Column("quoted_unit_price", sa.Numeric(18, 6)),
    sa.Column("pricing_version", sa.String(64)),
    sa.Column("dispatched_at", sa.DateTime(timezone=True)),
    sa.Column("settled_at", sa.DateTime(timezone=True)),
)


def _policies(remove=False):
    if op.get_bind().dialect.name != "postgresql":
        return
    previous = importlib.import_module("migrations.versions.0023_add_bounded_web_research_cache")
    admin = f"({previous._active_user_clause()}) AND ({previous._platform_admin_clause()})"
    # Public research is platform expenditure, regardless of the worker's tenant.
    scope = (
        "(quota_scope = 'platform_web' OR (provider IN ('web_search_baidu', "
        "'web_search_bocha') AND operation = 'company_discovery'))"
    )
    for action in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        name = f"usage_platform_guard_{action.lower()}"
        if remove:
            op.execute(f"DROP POLICY IF EXISTS {name} ON usage_ledger")
            continue
        allowed = f"quota_scope IS NULL OR ({admin})"
        predicate = f"WITH CHECK ({allowed})" if action == "INSERT" else f"USING ({allowed})"
        if action == "UPDATE":
            predicate += f" WITH CHECK ({allowed})"
        op.execute(f"CREATE POLICY {name} ON usage_ledger AS RESTRICTIVE FOR {action} {predicate}")
    for action in ("SELECT", "UPDATE"):
        name = f"usage_platform_admin_{action.lower()}"
        if remove:
            op.execute(f"DROP POLICY IF EXISTS {name} ON usage_ledger")
            continue
        allowed = f"({scope}) AND ({admin})"
        predicate = f"USING ({allowed})"
        if action == "UPDATE":
            predicate += f" WITH CHECK ({allowed})"
        op.execute(f"CREATE POLICY {name} ON usage_ledger FOR {action} {predicate}")


def upgrade():
    with op.batch_alter_table("usage_ledger") as batch:
        batch.alter_column("external_calls", existing_type=sa.Integer(), nullable=True)
        batch.alter_column(
            "estimated_cost",
            existing_type=sa.Numeric(12, 4),
            type_=sa.Numeric(18, 6),
            nullable=True,
        )
        for column in COLUMNS:
            batch.add_column(column)
        batch.create_check_constraint(
            "ck_usage_state",
            "usage_state IN ('legacy','reserved','in_flight','settled','uncertain','released')",
        )
        batch.create_check_constraint(
            "ck_usage_cost_status",
            "cost_status IN ('unknown','estimated','actual','confirmed_free')",
        )
        batch.create_check_constraint("ck_usage_reserved_calls", "reserved_calls >= 0")
        batch.create_check_constraint(
            "ck_usage_quota_scope",
            "quota_scope IS NULL OR "
            "(quota_scope = 'platform_web' AND task_key IS NOT NULL "
            "AND subject_key IS NOT NULL)",
        )
        batch.create_index("ix_usage_quota_task", ["quota_scope", "task_key"])
        batch.create_index("ix_usage_quota_subject", ["quota_scope", "subject_key", "created_at"])
    _policies()


def downgrade():
    if op.get_context().as_sql or op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM usage_ledger WHERE usage_state <> 'legacy' "
            "OR quota_scope IS NOT NULL OR cost_status <> 'unknown' "
            "OR external_calls IS NULL OR estimated_cost IS NULL "
            "OR estimated_cost <> round(estimated_cost, 4) OR abs(estimated_cost) >= 100000000"
        )
    ):
        raise RuntimeError("Usage reservations or precise costs exist; retain accounting history")
    _policies(remove=True)
    with op.batch_alter_table("usage_ledger") as batch:
        batch.drop_index("ix_usage_quota_subject")
        batch.drop_index("ix_usage_quota_task")
        for name in (
            "ck_usage_state",
            "ck_usage_cost_status",
            "ck_usage_reserved_calls",
            "ck_usage_quota_scope",
        ):
            batch.drop_constraint(name, type_="check")
        for column in reversed(COLUMNS):
            batch.drop_column(column.name)
        batch.alter_column("external_calls", existing_type=sa.Integer(), nullable=False)
        batch.alter_column(
            "estimated_cost",
            existing_type=sa.Numeric(18, 6),
            type_=sa.Numeric(12, 4),
            nullable=False,
        )
