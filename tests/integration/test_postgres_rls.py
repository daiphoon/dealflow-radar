from __future__ import annotations

import os
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine, text

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
) -> tuple[int, int, int]:
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
                "(SELECT count(*) FROM review_queue)"
            )
        ).one()
        return tuple(row)


def test_non_owner_role_enforces_tenant_fund_and_review_rls() -> None:
    assert POSTGRES_RLS_DATABASE_URL is not None
    engine = create_engine(POSTGRES_RLS_DATABASE_URL, pool_pre_ping=True)
    try:
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
                        'review_queue', 'refresh_jobs', 'usage_ledger'
                    )
                      AND relrowsecurity
                    """
                )
            )
        assert role == (False, False, False, False, False)
        assert enabled_rls_tables == 6
        assert _visible_counts(engine) == (0, 0, 0)
        assert _visible_counts(engine, ALPHA_USER_ID, ALPHA_TENANT_ID) == (10, 1, 10)
        assert _visible_counts(engine, BETA_USER_ID, BETA_TENANT_ID) == (1, 1, 0)
        assert _visible_counts(engine, NO_ACCESS_USER_ID, ALPHA_TENANT_ID) == (0, 0, 0)
        assert _visible_counts(engine, ALPHA_USER_ID, BETA_TENANT_ID) == (0, 0, 0)
    finally:
        engine.dispose()
