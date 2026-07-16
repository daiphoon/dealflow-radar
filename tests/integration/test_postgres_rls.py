from __future__ import annotations

import os
from uuid import UUID

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DBAPIError

from backend.app.demo import (
    ALPHA_TENANT_ID,
    ALPHA_USER_ID,
    BETA_TENANT_ID,
    BETA_USER_ID,
    MOCK_SOURCE_ID,
    NO_ACCESS_USER_ID,
    demo_uuid,
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
    connection: Connection, user_id: UUID | None = None, tenant_id: UUID | None = None
) -> tuple[int, int, int, int, int]:
    connection.execute(
        text(
            "SELECT "
            "set_config('app.current_user_id', :user_id, true), "
            "set_config('app.current_tenant_id', :tenant_id, true)"
        ),
        {
            "user_id": str(user_id) if user_id is not None else "",
            "tenant_id": str(tenant_id) if tenant_id is not None else "",
        },
    )
    row = connection.execute(
        text(
            "SELECT "
            "(SELECT count(*) FROM investments), "
            "(SELECT count(*) FROM funds), "
            "(SELECT count(*) FROM review_queue), "
            "(SELECT count(*) FROM research_imports), "
            "(SELECT count(*) FROM official_identity_verifications)"
        )
    ).one()
    return tuple(row)


def _visible_scope_counts(
    connection: Connection, user_id: UUID | None = None, tenant_id: UUID | None = None
) -> tuple[int, int, int, int, int, int, int]:
    connection.execute(
        text(
            "SELECT "
            "set_config('app.current_user_id', :user_id, true), "
            "set_config('app.current_tenant_id', :tenant_id, true)"
        ),
        {
            "user_id": str(user_id) if user_id is not None else "",
            "tenant_id": str(tenant_id) if tenant_id is not None else "",
        },
    )
    return tuple(
        connection.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM companies), "
                "(SELECT count(*) FROM company_aliases), "
                "(SELECT count(*) FROM raw_documents), "
                "(SELECT count(*) FROM entity_mentions), "
                "(SELECT count(*) FROM events), "
                "(SELECT count(*) FROM event_evidence), "
                "(SELECT count(*) FROM company_snapshots)"
            )
        ).one()
    )


def _create_scoped_lead(
    connection: Connection,
    *,
    user_id: UUID,
    tenant_id: UUID,
    scope: str,
    owner_user_id: UUID | None,
    owner_tenant_id: UUID | None,
    suffix: str,
    document_id: str,
    mention_id: str,
    event_id: str,
    evidence_id: str,
) -> None:
    company_id = str(demo_uuid("company-示例星河科技一号有限公司"))
    source_id = str(MOCK_SOURCE_ID)
    connection.execute(
        text(
            "SELECT "
            "set_config('app.current_user_id', :user_id, true), "
            "set_config('app.current_tenant_id', :tenant_id, true)"
        ),
        {"user_id": str(user_id), "tenant_id": str(tenant_id)},
    )
    common = {
        "scope": scope,
        "owner_user_id": str(owner_user_id) if owner_user_id else None,
        "owner_tenant_id": str(owner_tenant_id) if owner_tenant_id else None,
    }
    connection.execute(
        text(
            """
                INSERT INTO raw_documents (
                    id, source_id, research_import_id, visibility_scope,
                    owner_user_id, owner_tenant_id, external_record_id,
                    canonical_url, title, published_at, published_on, observed_at,
                    content_hash, document_dedupe_key, license_status, payload,
                    created_at, updated_at
                ) VALUES (
                    :id, :source_id, NULL, :scope,
                    :owner_user_id, :owner_tenant_id, :external_record_id,
                    :canonical_url, :title, NULL, NULL, CURRENT_TIMESTAMP,
                    :content_hash, :document_dedupe_key, 'private_test',
                    CAST('{}' AS JSON), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                ) ON CONFLICT DO NOTHING
                """
        ),
        {
            **common,
            "id": document_id,
            "source_id": source_id,
            "external_record_id": f"rls-{suffix}",
            "canonical_url": f"https://example.invalid/rls-{suffix}",
            "title": f"RLS {suffix} document",
            "content_hash": suffix.rjust(64, "1")[:64],
            "document_dedupe_key": suffix.rjust(64, "2")[:64],
        },
    )
    connection.execute(
        text(
            """
                INSERT INTO entity_mentions (
                    id, raw_document_id, visibility_scope, owner_user_id,
                    owner_tenant_id, candidate_company_id, mention_text,
                    match_rule, match_confidence, resolution_status,
                    created_at, updated_at
                ) VALUES (
                    :id, :document_id, :scope, :owner_user_id,
                    :owner_tenant_id, :company_id, :mention_text,
                    'rls_test', 1.000, 'verified', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                ) ON CONFLICT DO NOTHING
                """
        ),
        {
            **common,
            "id": mention_id,
            "document_id": document_id,
            "company_id": company_id,
            "mention_text": f"RLS {suffix} mention",
        },
    )
    connection.execute(
        text(
            """
                INSERT INTO events (
                    id, company_id, visibility_scope, owner_user_id, owner_tenant_id,
                    event_type, event_subtype, status, direction, materiality_score,
                    risk_severity, confidence_score, source_quality, title, summary,
                    facts, uncertainties, occurred_at, published_at, published_on,
                    observed_at, fingerprint_version, event_fingerprint,
                    publication_route, publication_policy_version, publication_reasons,
                    created_at, updated_at
                ) VALUES (
                    :id, :company_id, :scope, :owner_user_id, :owner_tenant_id,
                    'information_quality', 'rls_test', 'candidate', 'neutral', 10,
                    'low', 0.800, 'A', :title, 'RLS scope test',
                    CAST('[]' AS JSON), CAST('[]' AS JSON), NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, 'rls-v1', :fingerprint,
                    'unconfirmed_lead', 'rls-v1', CAST('[]' AS JSON),
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                ) ON CONFLICT DO NOTHING
                """
        ),
        {
            **common,
            "id": event_id,
            "company_id": company_id,
            "title": f"RLS {suffix} lead",
            "fingerprint": suffix.rjust(64, "3")[:64],
        },
    )
    connection.execute(
        text(
            """
                INSERT INTO event_evidence (
                    id, event_id, raw_document_id, visibility_scope,
                    owner_user_id, owner_tenant_id, evidence_excerpt,
                    span_hash, support_type, created_at, updated_at
                ) VALUES (
                    :id, :event_id, :document_id, :scope,
                    :owner_user_id, :owner_tenant_id, 'RLS private evidence',
                    :span_hash, 'supports', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                ) ON CONFLICT DO NOTHING
                """
        ),
        {
            **common,
            "id": evidence_id,
            "event_id": event_id,
            "document_id": document_id,
            "span_hash": suffix.rjust(64, "4")[:64],
        },
    )


def _create_research_import(
    connection: Connection,
    *,
    user_id: UUID,
    tenant_id: UUID,
    research_import_id: str,
    batch_id: str,
    file_hash: str,
) -> None:
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
    connection = engine.connect()
    transaction = connection.begin()
    try:
        _create_research_import(
            connection,
            user_id=ALPHA_USER_ID,
            tenant_id=ALPHA_TENANT_ID,
            research_import_id="4ae1b11f-cd59-47df-9e28-695ecdb659af",
            batch_id="rls-test-batch",
            file_hash="1" * 64,
        )
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                _create_research_import(
                    connection,
                    user_id=ALPHA_USER_ID,
                    tenant_id=BETA_TENANT_ID,
                    research_import_id="b509d1b0-a164-4cb6-bd28-3d5282f3ef29",
                    batch_id="rls-cross-tenant-batch",
                    file_hash="2" * 64,
                )
        with connection.begin_nested():
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
                        'usage_ledger', 'official_identity_verifications',
                        'companies', 'company_aliases', 'raw_documents',
                        'entity_mentions', 'events', 'event_evidence',
                        'company_snapshots'
                    )
                      AND relrowsecurity
                    """
                )
            )
        assert role == (False, False, False, False, False)
        assert enabled_rls_tables == 15
        assert _visible_counts(connection) == (0, 0, 0, 0, 0)
        assert _visible_counts(connection, ALPHA_USER_ID, ALPHA_TENANT_ID) == (10, 1, 10, 1, 0)
        assert _visible_counts(connection, BETA_USER_ID, BETA_TENANT_ID) == (1, 1, 0, 0, 0)
        assert _visible_counts(connection, NO_ACCESS_USER_ID, ALPHA_TENANT_ID) == (0, 0, 0, 0, 0)
        assert _visible_counts(connection, ALPHA_USER_ID, BETA_TENANT_ID) == (0, 0, 0, 0, 0)
        assert _visible_scope_counts(connection) == (0, 0, 0, 0, 0, 0, 0)
        shared_scope_counts = _visible_scope_counts(connection, NO_ACCESS_USER_ID, ALPHA_TENANT_ID)
        assert shared_scope_counts[:4] == (10, 10, 10, 10)
        assert all(count in {0, 1} for count in shared_scope_counts[4:])
        alpha_scope_counts = _visible_scope_counts(connection, ALPHA_USER_ID, ALPHA_TENANT_ID)
        assert alpha_scope_counts == (
            10,
            10,
            10,
            10,
            10,
            10,
            shared_scope_counts[-1],
        )
        assert _visible_scope_counts(connection, BETA_USER_ID, BETA_TENANT_ID) == (
            shared_scope_counts
        )
        assert _visible_scope_counts(connection, ALPHA_USER_ID, BETA_TENANT_ID) == (
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )

        _create_scoped_lead(
            connection,
            user_id=ALPHA_USER_ID,
            tenant_id=ALPHA_TENANT_ID,
            scope="organization_private",
            owner_user_id=None,
            owner_tenant_id=ALPHA_TENANT_ID,
            suffix="organization-alpha",
            document_id="e2fc5bea-0e37-48ef-8688-359657f45775",
            mention_id="f7134684-ff40-47d4-93db-c4304ca9234b",
            event_id="2ab550d6-27f8-4fe0-82c4-a4d59eb6cd42",
            evidence_id="88466189-c3ba-4345-952e-fde0686d52b4",
        )
        _create_scoped_lead(
            connection,
            user_id=NO_ACCESS_USER_ID,
            tenant_id=ALPHA_TENANT_ID,
            scope="personal_private",
            owner_user_id=NO_ACCESS_USER_ID,
            owner_tenant_id=None,
            suffix="personal-no-access",
            document_id="be73d8d1-2e0b-464f-a562-0b9c4fe895ff",
            mention_id="b3f9af20-3cd1-4f0d-9389-f12342f1a820",
            event_id="41508868-cf3f-4b6c-84ea-7df06ca21e0f",
            evidence_id="bfda099e-a1b5-41bf-92ab-7192cc6b7611",
        )
        assert _visible_scope_counts(connection, ALPHA_USER_ID, ALPHA_TENANT_ID)[2:6] == (
            11,
            11,
            11,
            11,
        )
        assert _visible_scope_counts(connection, NO_ACCESS_USER_ID, ALPHA_TENANT_ID)[2:6] == (
            11,
            11,
            shared_scope_counts[4] + 1,
            shared_scope_counts[5] + 1,
        )
        assert _visible_scope_counts(connection, BETA_USER_ID, BETA_TENANT_ID)[2:6] == (
            *shared_scope_counts[2:6],
        )
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()
