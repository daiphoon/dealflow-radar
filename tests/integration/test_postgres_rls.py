from __future__ import annotations

import os
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from backend.app.demo import (
    ALPHA_TENANT_ID,
    ALPHA_USER_ID,
    BETA_TENANT_ID,
    BETA_USER_ID,
    NO_ACCESS_USER_ID,
)

POSTGRES_RLS_DATABASE_URL = os.getenv("POSTGRES_RLS_DATABASE_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_RLS_DATABASE_URL,
        reason="set POSTGRES_RLS_DATABASE_URL to run live PostgreSQL RLS tests",
    ),
]


def _visible_counts(
    engine: Engine, user_id: UUID | None = None, tenant_id: UUID | None = None
) -> tuple[int, int, int, int]:
    with engine.begin() as connection:
        if user_id is not None and tenant_id is not None:
            connection.execute(
                text(
                    "SELECT "
                    "set_config('app.current_user_id', :user_id, true), "
                    "set_config('app.current_tenant_id', :tenant_id, true)"
                ),
                {"user_id": str(user_id), "tenant_id": str(tenant_id)},
            )
        row = connection.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM investments), "
                "(SELECT count(*) FROM funds), "
                "(SELECT count(*) FROM review_queue), "
                "(SELECT count(*) FROM research_imports)"
            )
        ).one()
        return tuple(row)


def _create_research_import(
    engine: Engine,
    *,
    user_id: UUID,
    tenant_id: UUID,
    research_import_id: str,
    batch_id: str,
    file_hash: str,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "SELECT "
                "set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(user_id), "tenant_id": str(tenant_id)},
        )
        connection.execute(
            text(
                """
                INSERT INTO research_imports (
                    id, tenant_id, imported_by, schema_version, batch_id,
                    queried_at, research_tool, agent_name, original_query,
                    target_company_hint, source_filename, file_format, file_hash,
                    parser_version, license_status, status, record_count,
                    resolved_count, unresolved_count, created_at, updated_at
                )
                VALUES (
                    :id, :tenant_id, :imported_by, '1.0', :batch_id,
                    CURRENT_TIMESTAMP, 'test', NULL, 'test query',
                    'test company', 'rls-test.json', 'json', :file_hash,
                    '1', 'public', 'completed', 1, 1, 0,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "id": research_import_id,
                "tenant_id": str(tenant_id),
                "imported_by": str(user_id),
                "batch_id": batch_id,
                "file_hash": file_hash,
            },
        )


def test_non_owner_role_enforces_tenant_fund_and_review_rls() -> None:
    assert POSTGRES_RLS_DATABASE_URL is not None
    engine = create_engine(POSTGRES_RLS_DATABASE_URL, pool_pre_ping=True)
    try:
        _create_research_import(
            engine,
            user_id=ALPHA_USER_ID,
            tenant_id=ALPHA_TENANT_ID,
            research_import_id="4ae1b11f-cd59-47df-9e28-695ecdb659af",
            batch_id="rls-test-batch",
            file_hash="1" * 64,
        )
        with pytest.raises(DBAPIError):
            _create_research_import(
                engine,
                user_id=ALPHA_USER_ID,
                tenant_id=BETA_TENANT_ID,
                research_import_id="b509d1b0-a164-4cb6-bd28-3d5282f3ef29",
                batch_id="rls-cross-tenant-batch",
                file_hash="2" * 64,
            )
        with engine.connect() as connection:
            role = connection.execute(
                text(
                    """
                    SELECT roles.rolsuper,
                           roles.rolbypassrls,
                           EXISTS (
                               SELECT 1
                               FROM pg_class AS tables
                               JOIN pg_roles AS owners ON owners.oid = tables.relowner
                               JOIN pg_namespace AS schemas ON schemas.oid = tables.relnamespace
                               WHERE schemas.nspname = 'public'
                                 AND tables.relname = 'investments'
                                 AND owners.rolname = current_user
                           ) AS owns_investments,
                           has_table_privilege(
                               current_user, 'public.alembic_version', 'SELECT'
                           ) AS can_read_migration_state,
                           has_table_privilege(
                               current_user, 'public.investments', 'DELETE'
                           ) AS can_delete_investments
                    FROM pg_roles AS roles
                    WHERE roles.rolname = current_user
                    """
                )
            ).one()
            enabled_rls_tables = connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM pg_class
                    WHERE relname IN (
                        'fund_access_grants', 'funds', 'investments',
                        'research_imports', 'review_queue', 'refresh_jobs',
                        'usage_ledger'
                    )
                      AND relrowsecurity
                    """
                )
            )
        assert role == (False, False, False, False, False)
        assert enabled_rls_tables == 7
        assert _visible_counts(engine) == (0, 0, 0, 0)
        assert _visible_counts(engine, ALPHA_USER_ID, ALPHA_TENANT_ID) == (10, 1, 10, 1)
        assert _visible_counts(engine, BETA_USER_ID, BETA_TENANT_ID) == (1, 1, 0, 0)
        assert _visible_counts(engine, NO_ACCESS_USER_ID, ALPHA_TENANT_ID) == (0, 0, 0, 0)
        assert _visible_counts(engine, ALPHA_USER_ID, BETA_TENANT_ID) == (0, 0, 0, 0)
    finally:
        engine.dispose()
