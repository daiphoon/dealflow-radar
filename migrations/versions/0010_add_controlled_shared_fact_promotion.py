"""add controlled shared fact promotion

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _current_user() -> str:
    return "NULLIF(current_setting('app.current_user_id', true), '')::uuid"


def _current_tenant() -> str:
    return "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def _active_user_clause() -> str:
    return f"""
        EXISTS (
            SELECT 1 FROM users AS current_scope_user
            WHERE current_scope_user.id = {_current_user()}
              AND current_scope_user.tenant_id = {_current_tenant()}
              AND current_scope_user.status = 'active'
        )
    """


def _active_role_clause(role_codes: tuple[str, ...]) -> str:
    roles = ", ".join(f"'{role}'" for role in role_codes)
    return f"""
        EXISTS (
            SELECT 1
            FROM user_role_assignments AS assignment
            JOIN roles ON roles.id = assignment.role_id
            JOIN users ON users.id = assignment.user_id
            WHERE assignment.user_id = {_current_user()}
              AND users.tenant_id = {_current_tenant()}
              AND users.status = 'active'
              AND roles.code IN ({roles})
              AND (assignment.valid_until IS NULL OR assignment.valid_until > CURRENT_TIMESTAMP)
        )
    """


def _authorized_company_clause(company_reference: str) -> str:
    return f"""
        EXISTS (
            SELECT 1
            FROM investments AS scoped_investment
            JOIN fund_access_grants AS scoped_grant
              ON scoped_grant.fund_id = scoped_investment.fund_id
            WHERE scoped_investment.company_id = {company_reference}
              AND scoped_investment.tenant_id = {_current_tenant()}
              AND scoped_grant.user_id = {_current_user()}
              AND (
                  scoped_grant.valid_until IS NULL
                  OR scoped_grant.valid_until > CURRENT_TIMESTAMP
              )
        )
    """


def _scoped_write_clause(table_name: str, company_reference: str) -> str:
    platform_admin = _active_role_clause(("platform_admin",))
    tenant_roles = _active_role_clause(("institution_admin", "reviewer"))
    organization_write = f"{tenant_roles} OR {_authorized_company_clause(company_reference)}"
    return f"""
        ({_active_user_clause()})
        AND (
            ({table_name}.visibility_scope = 'platform_shared'
             AND {table_name}.owner_user_id IS NULL
             AND {table_name}.owner_tenant_id IS NULL
             AND ({platform_admin}))
            OR ({table_name}.visibility_scope = 'personal_private'
                AND {table_name}.owner_user_id = {_current_user()}
                AND {table_name}.owner_tenant_id IS NULL)
            OR ({table_name}.visibility_scope = 'organization_private'
                AND {table_name}.owner_user_id IS NULL
                AND {table_name}.owner_tenant_id = {_current_tenant()}
                AND ({organization_write}))
        )
    """


def _event_evidence_parent_scope_clause() -> str:
    return """
        EXISTS (
            SELECT 1
            FROM events AS parent_event
            WHERE parent_event.id = event_evidence.event_id
              AND (
                  parent_event.visibility_scope = 'platform_shared'
                  OR (
                      parent_event.visibility_scope = event_evidence.visibility_scope
                      AND parent_event.owner_user_id
                          IS NOT DISTINCT FROM event_evidence.owner_user_id
                      AND parent_event.owner_tenant_id
                          IS NOT DISTINCT FROM event_evidence.owner_tenant_id
                  )
              )
        )
    """


def _alter_event_evidence() -> None:
    with op.batch_alter_table("event_evidence") as batch_op:
        batch_op.alter_column(
            "raw_document_id",
            existing_type=sa.Uuid(),
            nullable=True,
        )
        batch_op.add_column(sa.Column("source_event_evidence_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("display_source_name", sa.String(200), nullable=True))
        batch_op.add_column(sa.Column("display_source_quality", sa.String(1), nullable=True))
        batch_op.add_column(sa.Column("display_title", sa.String(500), nullable=True))
        batch_op.add_column(sa.Column("display_canonical_url", sa.String(1000), nullable=True))
        batch_op.add_column(sa.Column("display_published_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("display_published_on", sa.Date()))
        batch_op.add_column(sa.Column("display_observed_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("display_url_health_status", sa.String(32)))
        batch_op.add_column(sa.Column("display_url_http_status", sa.Integer()))
        batch_op.add_column(sa.Column("display_url_checked_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("display_final_url", sa.String(1000)))
        batch_op.add_column(sa.Column("display_license_status", sa.String(32)))
        batch_op.add_column(
            sa.Column(
                "display_allowed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_foreign_key(
            "fk_event_evidence_source_event_evidence_id",
            "event_evidence",
            ["source_event_evidence_id"],
            ["id"],
        )
        batch_op.create_unique_constraint(
            "uq_event_evidence_source_reference",
            ["event_id", "source_event_evidence_id"],
        )
        batch_op.create_check_constraint(
            "ck_event_evidence_single_origin",
            "(raw_document_id IS NOT NULL AND source_event_evidence_id IS NULL) OR "
            "(raw_document_id IS NULL AND source_event_evidence_id IS NOT NULL)",
        )
        batch_op.create_index(
            "ix_event_evidence_source_event_evidence_id",
            ["source_event_evidence_id"],
        )


def _create_audit_tables() -> None:
    op.create_table(
        "event_sharing_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=True),
        sa.Column("shared_event_id", sa.Uuid(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("actor_tenant_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("shared_title", sa.String(200), nullable=True),
        sa.Column("shared_summary", sa.Text(), nullable=True),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('promote', 'reject', 'retract')",
            name="ck_event_sharing_decision_action",
        ),
        sa.CheckConstraint(
            "(action = 'promote' AND source_event_id IS NOT NULL "
            "AND shared_event_id IS NOT NULL) OR "
            "(action = 'reject' AND source_event_id IS NOT NULL "
            "AND shared_event_id IS NULL) OR "
            "(action = 'retract' AND shared_event_id IS NOT NULL)",
            name="ck_event_sharing_decision_subject",
        ),
        sa.ForeignKeyConstraint(["source_event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(["shared_event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["actor_tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_event_sharing_decisions_source_event_id",
        "event_sharing_decisions",
        ["source_event_id"],
    )
    op.create_index(
        "ix_event_sharing_decisions_shared_event_id",
        "event_sharing_decisions",
        ["shared_event_id"],
    )
    op.create_index(
        "ix_event_sharing_decisions_actor_user_id",
        "event_sharing_decisions",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_event_sharing_decisions_actor_tenant_id",
        "event_sharing_decisions",
        ["actor_tenant_id"],
    )
    op.create_index(
        "ix_event_sharing_decisions_action",
        "event_sharing_decisions",
        ["action"],
    )
    op.create_index(
        "uq_event_sharing_source_outcome",
        "event_sharing_decisions",
        ["source_event_id"],
        unique=True,
        postgresql_where=sa.text("action IN ('promote', 'reject')"),
        sqlite_where=sa.text("action IN ('promote', 'reject')"),
    )
    op.create_table(
        "event_sharing_decision_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("source_event_evidence_id", sa.Uuid(), nullable=False),
        sa.Column("shared_event_evidence_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["decision_id"], ["event_sharing_decisions.id"]),
        sa.ForeignKeyConstraint(["source_event_evidence_id"], ["event_evidence.id"]),
        sa.ForeignKeyConstraint(["shared_event_evidence_id"], ["event_evidence.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "decision_id",
            "source_event_evidence_id",
            name="uq_event_sharing_decision_evidence_source",
        ),
    )


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    platform_admin = _active_role_clause(("platform_admin",))
    for table_name in ("raw_documents", "entity_mentions", "events", "event_evidence"):
        op.execute(
            f"CREATE POLICY {table_name}_platform_admin_read ON {table_name} "
            f"FOR SELECT USING ({_active_user_clause()} AND ({platform_admin}) "
            f"AND {table_name}.visibility_scope IN "
            "('platform_shared', 'personal_private', 'organization_private'))"
        )

    for table_name, company_reference in (
        ("events", "events.company_id"),
        (
            "event_evidence",
            "(SELECT events.company_id FROM events WHERE events.id = event_evidence.event_id)",
        ),
        ("company_snapshots", "company_snapshots.company_id"),
    ):
        write_clause = _scoped_write_clause(table_name, company_reference)
        if table_name == "event_evidence":
            write_clause = f"({write_clause}) AND ({_event_evidence_parent_scope_clause()})"
        op.execute(f"DROP POLICY {table_name}_scope_insert ON {table_name}")
        op.execute(f"DROP POLICY {table_name}_scope_update ON {table_name}")
        op.execute(
            f"CREATE POLICY {table_name}_scope_insert ON {table_name} "
            f"FOR INSERT WITH CHECK ({write_clause})"
        )
        op.execute(
            f"CREATE POLICY {table_name}_scope_update ON {table_name} "
            f"FOR UPDATE USING ({write_clause}) WITH CHECK ({write_clause})"
        )

    actor_check = (
        f"{_active_user_clause()} AND ({platform_admin}) "
        f"AND actor_user_id = {_current_user()} AND actor_tenant_id = {_current_tenant()}"
    )
    op.execute("ALTER TABLE event_sharing_decisions ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY event_sharing_decisions_platform_admin_read "
        "ON event_sharing_decisions FOR SELECT "
        f"USING ({_active_user_clause()} AND ({platform_admin}))"
    )
    op.execute(
        "CREATE POLICY event_sharing_decisions_platform_admin_insert "
        "ON event_sharing_decisions FOR INSERT "
        f"WITH CHECK ({actor_check})"
    )
    op.execute("ALTER TABLE event_sharing_decision_evidence ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY event_sharing_decision_evidence_platform_admin_read "
        "ON event_sharing_decision_evidence FOR SELECT "
        f"USING ({_active_user_clause()} AND ({platform_admin}))"
    )
    op.execute(
        "CREATE POLICY event_sharing_decision_evidence_platform_admin_insert "
        "ON event_sharing_decision_evidence FOR INSERT "
        f"WITH CHECK ({_active_user_clause()} AND ({platform_admin}))"
    )


def upgrade() -> None:
    _alter_event_evidence()
    _create_audit_tables()
    _enable_rls()


def _restore_legacy_scoped_write_policies() -> None:
    tenant_roles = _active_role_clause(("institution_admin", "reviewer"))
    for table_name, company_reference in (
        ("events", "events.company_id"),
        (
            "event_evidence",
            "(SELECT events.company_id FROM events WHERE events.id = event_evidence.event_id)",
        ),
        ("company_snapshots", "company_snapshots.company_id"),
    ):
        organization_write = f"{tenant_roles} OR {_authorized_company_clause(company_reference)}"
        write_clause = f"""
            ({_active_user_clause()})
            AND (
                ({table_name}.visibility_scope = 'platform_shared'
                 AND {table_name}.owner_user_id IS NULL
                 AND {table_name}.owner_tenant_id IS NULL
                 AND ({tenant_roles}))
                OR ({table_name}.visibility_scope = 'personal_private'
                    AND {table_name}.owner_user_id = {_current_user()}
                    AND {table_name}.owner_tenant_id IS NULL)
                OR ({table_name}.visibility_scope = 'organization_private'
                    AND {table_name}.owner_user_id IS NULL
                    AND {table_name}.owner_tenant_id = {_current_tenant()}
                    AND ({organization_write}))
            )
        """
        op.execute(f"DROP POLICY {table_name}_scope_insert ON {table_name}")
        op.execute(f"DROP POLICY {table_name}_scope_update ON {table_name}")
        op.execute(
            f"CREATE POLICY {table_name}_scope_insert ON {table_name} "
            f"FOR INSERT WITH CHECK ({write_clause})"
        )
        op.execute(
            f"CREATE POLICY {table_name}_scope_update ON {table_name} "
            f"FOR UPDATE USING ({write_clause}) WITH CHECK ({write_clause})"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP POLICY event_sharing_decision_evidence_platform_admin_insert "
            "ON event_sharing_decision_evidence"
        )
        op.execute(
            "DROP POLICY event_sharing_decision_evidence_platform_admin_read "
            "ON event_sharing_decision_evidence"
        )
        op.execute("ALTER TABLE event_sharing_decision_evidence DISABLE ROW LEVEL SECURITY")
        op.execute(
            "DROP POLICY event_sharing_decisions_platform_admin_insert ON event_sharing_decisions"
        )
        op.execute(
            "DROP POLICY event_sharing_decisions_platform_admin_read ON event_sharing_decisions"
        )
        op.execute("ALTER TABLE event_sharing_decisions DISABLE ROW LEVEL SECURITY")
        _restore_legacy_scoped_write_policies()
        for table_name in reversed(
            ("raw_documents", "entity_mentions", "events", "event_evidence")
        ):
            op.execute(f"DROP POLICY {table_name}_platform_admin_read ON {table_name}")

    op.drop_table("event_sharing_decision_evidence")
    for index_name in (
        "uq_event_sharing_source_outcome",
        "ix_event_sharing_decisions_action",
        "ix_event_sharing_decisions_actor_tenant_id",
        "ix_event_sharing_decisions_actor_user_id",
        "ix_event_sharing_decisions_shared_event_id",
        "ix_event_sharing_decisions_source_event_id",
    ):
        op.drop_index(index_name, table_name="event_sharing_decisions")
    op.drop_table("event_sharing_decisions")

    with op.batch_alter_table("event_evidence") as batch_op:
        batch_op.drop_index("ix_event_evidence_source_event_evidence_id")
        batch_op.drop_constraint("ck_event_evidence_single_origin", type_="check")
        batch_op.drop_constraint("uq_event_evidence_source_reference", type_="unique")
        batch_op.drop_constraint(
            "fk_event_evidence_source_event_evidence_id",
            type_="foreignkey",
        )
        for column_name in (
            "display_allowed",
            "display_license_status",
            "display_final_url",
            "display_url_checked_at",
            "display_url_http_status",
            "display_url_health_status",
            "display_observed_at",
            "display_published_on",
            "display_published_at",
            "display_canonical_url",
            "display_title",
            "display_source_quality",
            "display_source_name",
            "source_event_evidence_id",
        ):
            batch_op.drop_column(column_name)
        batch_op.alter_column(
            "raw_document_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )
