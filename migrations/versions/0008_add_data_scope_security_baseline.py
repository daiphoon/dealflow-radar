"""add data scope security baseline

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCOPED_TABLES = (
    "company_aliases",
    "raw_documents",
    "entity_mentions",
    "events",
    "event_evidence",
    "company_snapshots",
)

SCOPE_OWNER_CHECK = (
    "(visibility_scope = 'platform_shared' "
    "AND owner_user_id IS NULL AND owner_tenant_id IS NULL) OR "
    "(visibility_scope = 'personal_private' "
    "AND owner_user_id IS NOT NULL AND owner_tenant_id IS NULL) OR "
    "(visibility_scope = 'organization_private' "
    "AND owner_user_id IS NULL AND owner_tenant_id IS NOT NULL) OR "
    "(visibility_scope = 'system_restricted' "
    "AND owner_user_id IS NULL AND owner_tenant_id IS NULL)"
)


def _add_scope_columns(table_name: str, check_name: str) -> None:
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.add_column(
            sa.Column(
                "visibility_scope",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'system_restricted'"),
            )
        )
        batch_op.add_column(sa.Column("owner_user_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("owner_tenant_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            f"fk_{table_name}_owner_user_id",
            "users",
            ["owner_user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            f"fk_{table_name}_owner_tenant_id",
            "tenants",
            ["owner_tenant_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_check_constraint(check_name, SCOPE_OWNER_CHECK)
        batch_op.create_index(f"ix_{table_name}_owner_user_id", ["owner_user_id"])
        batch_op.create_index(f"ix_{table_name}_owner_tenant_id", ["owner_tenant_id"])


def _backfill_scopes() -> None:
    op.execute(
        """
        UPDATE raw_documents
        SET visibility_scope = 'organization_private',
            owner_tenant_id = (
                SELECT research_imports.tenant_id
                FROM research_imports
                WHERE research_imports.id = raw_documents.research_import_id
            )
        WHERE research_import_id IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM research_imports
              WHERE research_imports.id = raw_documents.research_import_id
          )
        """
    )
    op.execute(
        """
        UPDATE raw_documents
        SET visibility_scope = 'platform_shared',
            owner_user_id = NULL,
            owner_tenant_id = NULL
        WHERE research_import_id IS NULL
          AND license_status = 'synthetic_demo'
          AND EXISTS (
              SELECT 1 FROM sources
              WHERE sources.id = raw_documents.source_id
                AND sources.code = 'mock_official'
          )
        """
    )
    for table_name, document_reference in (
        ("entity_mentions", "raw_document_id"),
        ("event_evidence", "raw_document_id"),
    ):
        op.execute(
            f"""
            UPDATE {table_name}
            SET visibility_scope = (
                    SELECT raw_documents.visibility_scope
                    FROM raw_documents
                    WHERE raw_documents.id = {table_name}.{document_reference}
                ),
                owner_user_id = (
                    SELECT raw_documents.owner_user_id
                    FROM raw_documents
                    WHERE raw_documents.id = {table_name}.{document_reference}
                ),
                owner_tenant_id = (
                    SELECT raw_documents.owner_tenant_id
                    FROM raw_documents
                    WHERE raw_documents.id = {table_name}.{document_reference}
                )
            WHERE EXISTS (
                SELECT 1 FROM raw_documents
                WHERE raw_documents.id = {table_name}.{document_reference}
            )
            """
        )

    op.execute(
        """
        UPDATE events
        SET visibility_scope = 'organization_private',
            owner_user_id = NULL,
            owner_tenant_id = (
                SELECT raw_documents.owner_tenant_id
                FROM event_evidence
                JOIN raw_documents ON raw_documents.id = event_evidence.raw_document_id
                WHERE event_evidence.event_id = events.id
                  AND raw_documents.visibility_scope = 'organization_private'
                LIMIT 1
            )
        WHERE EXISTS (
            SELECT 1
            FROM event_evidence
            JOIN raw_documents ON raw_documents.id = event_evidence.raw_document_id
            WHERE event_evidence.event_id = events.id
              AND raw_documents.visibility_scope = 'organization_private'
        )
          AND NOT EXISTS (
              SELECT 1
              FROM event_evidence
              JOIN raw_documents ON raw_documents.id = event_evidence.raw_document_id
              WHERE event_evidence.event_id = events.id
                AND raw_documents.visibility_scope <> 'organization_private'
          )
          AND (
              SELECT COUNT(DISTINCT raw_documents.owner_tenant_id)
              FROM event_evidence
              JOIN raw_documents ON raw_documents.id = event_evidence.raw_document_id
              WHERE event_evidence.event_id = events.id
                AND raw_documents.visibility_scope = 'organization_private'
          ) = 1
        """
    )
    op.execute(
        """
        UPDATE events
        SET visibility_scope = 'organization_private',
            owner_user_id = NULL,
            owner_tenant_id = (
                SELECT review_queue.tenant_id
                FROM review_queue
                WHERE review_queue.event_id = events.id
                LIMIT 1
            )
        WHERE status <> 'published'
          AND EXISTS (
              SELECT 1 FROM review_queue WHERE review_queue.event_id = events.id
          )
        """
    )
    op.execute(
        """
        UPDATE events
        SET visibility_scope = 'platform_shared',
            owner_user_id = NULL,
            owner_tenant_id = NULL
        WHERE status = 'published'
          AND EXISTS (
              SELECT 1 FROM event_evidence WHERE event_evidence.event_id = events.id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM event_evidence
              JOIN raw_documents ON raw_documents.id = event_evidence.raw_document_id
              WHERE event_evidence.event_id = events.id
                AND raw_documents.visibility_scope <> 'platform_shared'
          )
          AND EXISTS (
              SELECT 1 FROM companies
              WHERE companies.id = events.company_id
                AND companies.tenant_id IS NULL
                AND companies.visibility_scope = 'public'
                AND companies.identity_status = 'verified'
          )
        """
    )
    op.execute(
        """
        UPDATE event_evidence
        SET visibility_scope = (
                SELECT events.visibility_scope
                FROM events
                WHERE events.id = event_evidence.event_id
            ),
            owner_user_id = (
                SELECT events.owner_user_id
                FROM events
                WHERE events.id = event_evidence.event_id
            ),
            owner_tenant_id = (
                SELECT events.owner_tenant_id
                FROM events
                WHERE events.id = event_evidence.event_id
            )
        WHERE EXISTS (
            SELECT 1 FROM events WHERE events.id = event_evidence.event_id
        )
        """
    )

    op.execute(
        """
        UPDATE company_aliases
        SET visibility_scope = 'organization_private',
            owner_user_id = NULL,
            owner_tenant_id = (
                SELECT companies.tenant_id
                FROM companies
                WHERE companies.id = company_aliases.company_id
            )
        WHERE EXISTS (
            SELECT 1 FROM companies
            WHERE companies.id = company_aliases.company_id
              AND companies.tenant_id IS NOT NULL
        )
        """
    )
    op.execute(
        """
        UPDATE company_aliases
        SET visibility_scope = 'platform_shared',
            owner_user_id = NULL,
            owner_tenant_id = NULL
        WHERE verification_status = 'verified'
          AND EXISTS (
              SELECT 1
              FROM companies
              JOIN sources ON sources.id = company_aliases.source_id
              WHERE companies.id = company_aliases.company_id
                AND companies.tenant_id IS NULL
                AND companies.visibility_scope = 'public'
                AND companies.identity_status = 'verified'
                AND sources.code = 'mock_official'
                AND sources.license_status = 'synthetic_demo'
          )
        """
    )

    op.execute(
        """
        UPDATE company_snapshots
        SET visibility_scope = 'organization_private',
            owner_user_id = NULL,
            owner_tenant_id = (
                SELECT companies.tenant_id
                FROM companies
                WHERE companies.id = company_snapshots.company_id
            )
        WHERE EXISTS (
            SELECT 1 FROM companies
            WHERE companies.id = company_snapshots.company_id
              AND companies.tenant_id IS NOT NULL
        )
        """
    )
    op.execute(
        """
        UPDATE company_snapshots
        SET visibility_scope = 'platform_shared',
            owner_user_id = NULL,
            owner_tenant_id = NULL
        WHERE EXISTS (
            SELECT 1 FROM events
            WHERE events.company_id = company_snapshots.company_id
              AND events.status = 'published'
        )
          AND NOT EXISTS (
              SELECT 1 FROM events
              WHERE events.company_id = company_snapshots.company_id
                AND events.status = 'published'
                AND events.visibility_scope <> 'platform_shared'
          )
        """
    )
    op.execute(
        """
        UPDATE company_snapshots
        SET visibility_scope = 'organization_private',
            owner_user_id = NULL,
            owner_tenant_id = (
                SELECT events.owner_tenant_id
                FROM events
                WHERE events.company_id = company_snapshots.company_id
                  AND events.status = 'published'
                  AND events.visibility_scope = 'organization_private'
                LIMIT 1
            )
        WHERE EXISTS (
            SELECT 1 FROM events
            WHERE events.company_id = company_snapshots.company_id
              AND events.status = 'published'
              AND events.visibility_scope = 'organization_private'
        )
          AND NOT EXISTS (
              SELECT 1 FROM events
              WHERE events.company_id = company_snapshots.company_id
                AND events.status = 'published'
                AND events.visibility_scope <> 'organization_private'
          )
          AND (
              SELECT COUNT(DISTINCT events.owner_tenant_id)
              FROM events
              WHERE events.company_id = company_snapshots.company_id
                AND events.status = 'published'
                AND events.visibility_scope = 'organization_private'
          ) = 1
        """
    )


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


def _scoped_read_clause(table_name: str, organization_access: str) -> str:
    return f"""
        ({_active_user_clause()})
        AND (
            ({table_name}.visibility_scope = 'platform_shared'
             AND {table_name}.owner_user_id IS NULL
             AND {table_name}.owner_tenant_id IS NULL)
            OR ({table_name}.visibility_scope = 'personal_private'
                AND {table_name}.owner_user_id = {_current_user()}
                AND {table_name}.owner_tenant_id IS NULL)
            OR ({table_name}.visibility_scope = 'organization_private'
                AND {table_name}.owner_user_id IS NULL
                AND {table_name}.owner_tenant_id = {_current_tenant()}
                AND ({organization_access}))
        )
    """


def _scoped_write_clause(table_name: str, platform_write: str, organization_write: str) -> str:
    return f"""
        ({_active_user_clause()})
        AND (
            ({table_name}.visibility_scope = 'platform_shared'
             AND {table_name}.owner_user_id IS NULL
             AND {table_name}.owner_tenant_id IS NULL
             AND ({platform_write}))
            OR ({table_name}.visibility_scope = 'personal_private'
                AND {table_name}.owner_user_id = {_current_user()}
                AND {table_name}.owner_tenant_id IS NULL)
            OR ({table_name}.visibility_scope = 'organization_private'
                AND {table_name}.owner_user_id IS NULL
                AND {table_name}.owner_tenant_id = {_current_tenant()}
                AND ({organization_write}))
        )
    """


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    role_access = _active_role_clause(("institution_admin", "reviewer"))
    company_access = {
        "company_aliases": _authorized_company_clause("company_aliases.company_id"),
        "entity_mentions": _authorized_company_clause("entity_mentions.candidate_company_id"),
        "events": _authorized_company_clause("events.company_id"),
        "event_evidence": _authorized_company_clause(
            "(SELECT events.company_id FROM events WHERE events.id = event_evidence.event_id)"
        ),
        "company_snapshots": _authorized_company_clause("company_snapshots.company_id"),
    }
    raw_document_access = " OR ".join(
        [
            role_access,
            _authorized_company_clause(
                "(SELECT entity_mentions.candidate_company_id FROM entity_mentions "
                "WHERE entity_mentions.raw_document_id = raw_documents.id "
                "AND entity_mentions.candidate_company_id IS NOT NULL LIMIT 1)"
            ),
            _authorized_company_clause(
                "(SELECT events.company_id FROM event_evidence "
                "JOIN events ON events.id = event_evidence.event_id "
                "WHERE event_evidence.raw_document_id = raw_documents.id LIMIT 1)"
            ),
        ]
    )
    organization_access = {
        table_name: f"{role_access} OR {company_access[table_name]}"
        for table_name in company_access
    }
    organization_access["raw_documents"] = raw_document_access

    for table_name in SCOPED_TABLES:
        read_clause = _scoped_read_clause(table_name, organization_access[table_name])
        write_roles = role_access
        platform_write = write_roles
        organization_write = write_roles
        if table_name == "company_snapshots":
            snapshot_access = _authorized_company_clause("company_snapshots.company_id")
            platform_write = f"{write_roles} OR {snapshot_access}"
            organization_write = f"{write_roles} OR {snapshot_access}"
        write_clause = _scoped_write_clause(
            table_name,
            platform_write,
            organization_write,
        )
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table_name}_scope_read ON {table_name} "
            f"FOR SELECT USING ({read_clause})"
        )
        op.execute(
            f"CREATE POLICY {table_name}_scope_insert ON {table_name} "
            f"FOR INSERT WITH CHECK ({write_clause})"
        )
        op.execute(
            f"CREATE POLICY {table_name}_scope_update ON {table_name} "
            f"FOR UPDATE USING ({read_clause}) WITH CHECK ({write_clause})"
        )

    company_read = f"""
        ({_active_user_clause()})
        AND (
            (companies.visibility_scope = 'public' AND companies.tenant_id IS NULL)
            OR (
                companies.visibility_scope = 'tenant'
                AND companies.tenant_id = {_current_tenant()}
                AND ({role_access} OR {_authorized_company_clause("companies.id")})
            )
        )
    """
    company_write = f"""
        ({_active_user_clause()})
        AND ({role_access})
        AND (
            (companies.visibility_scope = 'public' AND companies.tenant_id IS NULL)
            OR (
                companies.visibility_scope = 'tenant'
                AND companies.tenant_id = {_current_tenant()}
            )
        )
    """
    op.execute("ALTER TABLE companies ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY companies_scope_read ON companies FOR SELECT USING ({company_read})")
    op.execute(
        f"CREATE POLICY companies_scope_insert ON companies FOR INSERT WITH CHECK ({company_write})"
    )
    op.execute(
        f"CREATE POLICY companies_scope_update ON companies "
        f"FOR UPDATE USING ({company_read}) WITH CHECK ({company_write})"
    )


def upgrade() -> None:
    op.drop_index("uq_company_snapshot_current", table_name="company_snapshots")
    for table_name, check_name in (
        ("company_aliases", "ck_company_alias_scope_owner"),
        ("raw_documents", "ck_raw_document_scope_owner"),
        ("entity_mentions", "ck_entity_mention_scope_owner"),
        ("events", "ck_event_scope_owner"),
        ("event_evidence", "ck_event_evidence_scope_owner"),
        ("company_snapshots", "ck_company_snapshot_scope_owner"),
    ):
        if table_name == "events":
            with op.batch_alter_table("events") as batch_op:
                batch_op.drop_constraint("uq_event_fingerprint", type_="unique")
        _add_scope_columns(table_name, check_name)

    _backfill_scopes()

    for scope in (
        "platform_shared",
        "personal_private",
        "organization_private",
        "system_restricted",
    ):
        event_columns = ["company_id", "fingerprint_version", "event_fingerprint"]
        snapshot_columns = ["company_id"]
        if scope == "personal_private":
            event_columns.insert(1, "owner_user_id")
            snapshot_columns.append("owner_user_id")
        elif scope == "organization_private":
            event_columns.insert(1, "owner_tenant_id")
            snapshot_columns.append("owner_tenant_id")
        op.create_index(
            f"uq_event_fingerprint_{scope}",
            "events",
            event_columns,
            unique=True,
            postgresql_where=sa.text(f"visibility_scope = '{scope}'"),
            sqlite_where=sa.text(f"visibility_scope = '{scope}'"),
        )
        op.create_index(
            f"uq_company_snapshot_current_{scope}",
            "company_snapshots",
            snapshot_columns,
            unique=True,
            postgresql_where=sa.text(f"is_current AND visibility_scope = '{scope}'"),
            sqlite_where=sa.text(f"is_current = 1 AND visibility_scope = '{scope}'"),
        )

    _enable_rls()


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP POLICY companies_scope_update ON companies")
    op.execute("DROP POLICY companies_scope_insert ON companies")
    op.execute("DROP POLICY companies_scope_read ON companies")
    op.execute("ALTER TABLE companies DISABLE ROW LEVEL SECURITY")
    for table_name in reversed(SCOPED_TABLES):
        op.execute(f"DROP POLICY {table_name}_scope_update ON {table_name}")
        op.execute(f"DROP POLICY {table_name}_scope_insert ON {table_name}")
        op.execute(f"DROP POLICY {table_name}_scope_read ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    _disable_rls()
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY company_id, fingerprint_version, event_fingerprint
                       ORDER BY created_at, id
                   ) AS row_number
            FROM events
        )
        UPDATE events
        SET event_fingerprint =
            substr(event_fingerprint, 1, 31) ||
            replace(CAST(id AS VARCHAR), '-', '') || '0'
        WHERE id IN (SELECT id FROM ranked WHERE row_number > 1)
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY company_id
                       ORDER BY snapshot_version DESC, created_at DESC, id
                   ) AS row_number
            FROM company_snapshots
            WHERE is_current
        )
        UPDATE company_snapshots
        SET is_current = FALSE
        WHERE id IN (SELECT id FROM ranked WHERE row_number > 1)
        """
    )
    for scope in reversed(
        (
            "platform_shared",
            "personal_private",
            "organization_private",
            "system_restricted",
        )
    ):
        op.drop_index(f"uq_company_snapshot_current_{scope}", table_name="company_snapshots")
        op.drop_index(f"uq_event_fingerprint_{scope}", table_name="events")

    for table_name, check_name in reversed(
        (
            ("company_aliases", "ck_company_alias_scope_owner"),
            ("raw_documents", "ck_raw_document_scope_owner"),
            ("entity_mentions", "ck_entity_mention_scope_owner"),
            ("events", "ck_event_scope_owner"),
            ("event_evidence", "ck_event_evidence_scope_owner"),
            ("company_snapshots", "ck_company_snapshot_scope_owner"),
        )
    ):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_index(f"ix_{table_name}_owner_tenant_id")
            batch_op.drop_index(f"ix_{table_name}_owner_user_id")
            batch_op.drop_constraint(check_name, type_="check")
            batch_op.drop_constraint(
                f"fk_{table_name}_owner_tenant_id",
                type_="foreignkey",
            )
            batch_op.drop_constraint(
                f"fk_{table_name}_owner_user_id",
                type_="foreignkey",
            )
            batch_op.drop_column("owner_tenant_id")
            batch_op.drop_column("owner_user_id")
            batch_op.drop_column("visibility_scope")

    with op.batch_alter_table("events") as batch_op:
        batch_op.create_unique_constraint(
            "uq_event_fingerprint",
            ["company_id", "fingerprint_version", "event_fingerprint"],
        )
    op.create_index(
        "uq_company_snapshot_current",
        "company_snapshots",
        ["company_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
        sqlite_where=sa.text("is_current = 1"),
    )
