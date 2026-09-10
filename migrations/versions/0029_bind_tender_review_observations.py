"""Bind repeated tender reviews to their immutable source observations."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def _tender_analysis_policies(*, remove: bool = False) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    previous = importlib.import_module(
        "migrations.versions.0025_expand_analysis_rls_for_research_candidates"
    )
    event_clause = """
        investor_change_analyses.schema_version = 'investor-change-analysis-v1'
        AND EXISTS (
            SELECT 1 FROM events
            WHERE events.id = investor_change_analyses.event_id
              AND events.visibility_scope = 'platform_shared'
              AND events.owner_user_id IS NULL AND events.owner_tenant_id IS NULL
              AND events.status = 'published'
              AND events.publication_route = 'human_promoted'
              AND events.fingerprint_version = 'tender-v1'
        )
    """
    for name, action, clause in (
        ("read", "SELECT", f"({previous._active_user_clause()}) AND status = 'completed'"),
        ("admin_read", "SELECT", previous._platform_admin_clause()),
        ("insert", "INSERT", previous._platform_admin_clause()),
        ("update", "UPDATE", previous._platform_admin_clause()),
    ):
        policy = f"investor_change_analyses_tender_{name}"
        if remove:
            op.execute(f"DROP POLICY IF EXISTS {policy} ON investor_change_analyses")
            continue
        allowed = f"({event_clause}) AND ({clause})"
        predicate = f"WITH CHECK ({allowed})" if action == "INSERT" else f"USING ({allowed})"
        if action == "UPDATE":
            predicate += f" WITH CHECK ({allowed})"
        op.execute(f"CREATE POLICY {policy} ON investor_change_analyses FOR {action} {predicate}")


def upgrade() -> None:
    with op.batch_alter_table("event_observations") as batch:
        batch.create_unique_constraint("uq_event_observation_id_event", ["id", "event_id"])
    with op.batch_alter_table("event_sharing_decisions") as batch:
        batch.add_column(sa.Column("source_observation_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_sharing_decision_observation_event",
            "event_observations",
            ["source_observation_id", "source_event_id"],
            ["id", "event_id"],
        )
        batch.drop_index("uq_event_sharing_source_outcome")
        batch.create_index(
            "uq_event_sharing_source_outcome",
            ["source_event_id"],
            unique=True,
            postgresql_where=sa.text(
                "action IN ('promote', 'reject') AND source_observation_id IS NULL"
            ),
            sqlite_where=sa.text(
                "action IN ('promote', 'reject') AND source_observation_id IS NULL"
            ),
        )
        batch.create_index("uq_event_sharing_observation", ["source_observation_id"], unique=True)
    _tender_analysis_policies()


def downgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("Inspect observation reviews before preparing a downgrade")
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM event_sharing_decisions WHERE source_observation_id IS NOT NULL"
        )
    ):
        raise RuntimeError("Observation reviews exist; retain the review history")
    _tender_analysis_policies(remove=True)
    with op.batch_alter_table("event_sharing_decisions") as batch:
        batch.drop_index("uq_event_sharing_observation")
        batch.drop_index("uq_event_sharing_source_outcome")
        batch.drop_constraint("fk_sharing_decision_observation_event", type_="foreignkey")
        batch.drop_column("source_observation_id")
        batch.create_index(
            "uq_event_sharing_source_outcome",
            ["source_event_id"],
            unique=True,
            postgresql_where=sa.text("action IN ('promote', 'reject')"),
            sqlite_where=sa.text("action IN ('promote', 'reject')"),
        )
    with op.batch_alter_table("event_observations") as batch:
        batch.drop_constraint("uq_event_observation_id_event", type_="unique")
