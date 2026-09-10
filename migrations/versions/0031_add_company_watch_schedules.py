"""Add bounded company watch scheduling without exposing personal watchlists."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("company_research_jobs") as batch:
        batch.add_column(
            sa.Column("trigger_type", sa.String(16), nullable=False, server_default="manual")
        )
        batch.create_check_constraint(
            "ck_research_job_trigger", "trigger_type IN ('manual','watchlist')"
        )
    op.create_table(
        "company_watch_schedules",
        sa.Column("company_id", sa.Uuid(), sa.ForeignKey("companies.id"), primary_key=True),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_successful_check_at", sa.DateTime(timezone=True)),
        sa.Column("last_job_id", sa.Uuid(), sa.ForeignKey("company_research_jobs.id")),
        sa.Column("last_outcome_key", sa.String(128)),
        sa.Column("last_outcome", sa.String(32), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("consecutive_no_change_runs", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "consecutive_failures >= 0 AND consecutive_no_change_runs >= 0",
            name="ck_watch_schedule_counts",
        ),
    )
    op.create_index("ix_watch_schedule_due", "company_watch_schedules", ["next_check_at"])
    if op.get_bind().dialect.name != "postgresql":
        return
    previous = importlib.import_module("migrations.versions.0023_add_bounded_web_research_cache")
    admin = previous._platform_admin_clause()
    active = previous._active_user_clause()
    # No caller parameters, no user identifiers in the result, no access to raw lists.
    # The fixed search path prevents caller-created temporary objects shadowing trusted tables.
    op.execute(f"""
        CREATE FUNCTION public.watchlist_monitor_targets()
        RETURNS TABLE(company_id uuid, followed_at timestamptz)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
          SELECT watch.company_id, min(watch.created_at)
          FROM public.personal_watchlist_items AS watch
          JOIN public.users AS follower ON follower.id = watch.owner_user_id
          JOIN public.companies AS company ON company.id = watch.company_id
          WHERE ({admin}) AND follower.status = 'active'
            AND company.tenant_id IS NULL AND company.visibility_scope = 'public'
            AND company.identity_status = 'verified'
            AND company.credit_code IS NOT NULL
          GROUP BY watch.company_id
        $$
    """)
    op.execute("ALTER TABLE company_watch_schedules ENABLE ROW LEVEL SECURITY")
    own_watch = """EXISTS (SELECT 1 FROM personal_watchlist_items AS watch
        WHERE watch.company_id = company_watch_schedules.company_id
          AND watch.owner_user_id =
              NULLIF(current_setting('app.current_user_id', true), '')::uuid)"""
    op.execute(
        f"CREATE POLICY watch_schedule_read ON company_watch_schedules FOR SELECT "
        f"USING (({active}) AND (({admin}) OR ({own_watch})))"
    )
    for action in ("INSERT", "UPDATE"):
        clause = (
            f"WITH CHECK ({admin})"
            if action == "INSERT"
            else f"USING ({admin}) WITH CHECK ({admin})"
        )
        op.execute(
            f"CREATE POLICY watch_schedule_{action.lower()} "
            f"ON company_watch_schedules FOR {action} {clause}"
        )


def downgrade():
    if op.get_context().as_sql or op.get_bind().scalar(
        sa.text(
            "SELECT (SELECT count(*) FROM company_watch_schedules) + "
            "(SELECT count(*) FROM company_research_jobs WHERE trigger_type = 'watchlist')"
        )
    ):
        raise RuntimeError("Watch scheduling history exists; retain it and disable the scheduler")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION public.watchlist_monitor_targets()")
    op.drop_table("company_watch_schedules")
    with op.batch_alter_table("company_research_jobs") as batch:
        batch.drop_constraint("ck_research_job_trigger", type_="check")
        batch.drop_column("trigger_type")
