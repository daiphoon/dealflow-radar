from __future__ import annotations

import os
from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.app.auth import AuthTokenSet, VerificationChallenge, VerifiedIdentity
from backend.app.config import (
    CloudBaseAuthPolicy,
    InvestorAnalysisPolicy,
    Settings,
)
from backend.app.database import set_request_context
from backend.app.demo import (
    ALPHA_TENANT_ID,
    ALPHA_USER_ID,
    BETA_TENANT_ID,
    BETA_USER_ID,
    MOCK_SOURCE_ID,
    NO_ACCESS_USER_ID,
    demo_uuid,
)
from backend.app.investor_analysis import run_investor_analysis_worker_once
from backend.app.investor_analysis_schema import (
    INVESTOR_ANALYSIS_DISCLAIMER,
    INVESTOR_ANALYSIS_SCHEMA_VERSION,
    InvestorChangeAnalysisOutput,
    InvestorChangeAnalysisRequest,
    LLMProviderResult,
)
from backend.app.main import create_app
from backend.app.models import User

POSTGRES_RLS_DATABASE_URL = os.getenv("POSTGRES_RLS_DATABASE_URL")
DATABASE_ADMIN_URL = os.getenv("DATABASE_ADMIN_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_RLS_DATABASE_URL,
        reason="set POSTGRES_RLS_DATABASE_URL to run live PostgreSQL RLS tests",
    ),
]


class _PostgresCloudBaseProvider:
    def send_email_code(self, email: str) -> VerificationChallenge:
        assert email == "alpha-admin@example.invalid"
        return VerificationChallenge("postgres-verification", 600)

    def sign_in_with_email_code(self, verification_id: str, verification_code: str) -> AuthTokenSet:
        assert (verification_id, verification_code) == ("postgres-verification", "123456")
        return AuthTokenSet("postgres-access-token", "postgres-refresh-token", 7200)

    def refresh_tokens(self, refresh_token: str) -> AuthTokenSet:
        assert refresh_token == "postgres-refresh-token"
        return AuthTokenSet("postgres-access-token", "postgres-refresh-token-2", 7200)

    def verify_access_token(self, access_token: str) -> VerifiedIdentity:
        assert access_token == "postgres-access-token"
        return VerifiedIdentity(
            provider="cloudbase",
            subject="postgres-subject-alpha",
            email="alpha-admin@example.invalid",
        )

    def sign_out(self, access_token: str) -> None:
        assert access_token == "postgres-access-token"


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
                           ) AS can_delete_investments,
                           has_table_privilege(
                               current_user, 'public.personal_watchlist_items', 'DELETE'
                           ) AS can_delete_personal_watchlist_items,
                           (
                               SELECT count(*)
                               FROM information_schema.tables
                               WHERE table_schema = 'public'
                                 AND table_type = 'BASE TABLE'
                                 AND table_name <> 'personal_watchlist_items'
                                 AND has_table_privilege(
                                     current_user,
                                     format('%I.%I', table_schema, table_name),
                                     'DELETE'
                                 )
                           ) AS unexpected_delete_table_count
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
                        'company_snapshots', 'event_sharing_decisions',
                        'event_sharing_decision_evidence', 'trusted_sources',
                        'source_check_runs', 'candidate_documents',
                        'authentication_audit_logs',
                        'personal_watchlist_items', 'personal_company_requests',
                        'personal_usage_records', 'personal_company_view_states',
                        'personal_event_view_receipts', 'personal_company_reports',
                        'personal_quota_increase_requests', 'company_research_jobs',
                        'investor_change_analyses'
                    )
                      AND relrowsecurity
                    """
                )
            )
        assert role == (False, False, False, False, False, True, 0)
        assert enabled_rls_tables == 30
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

        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(ALPHA_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        connection.execute(
            text(
                """
                INSERT INTO user_role_assignments (
                    id, user_id, role_id, scope_id, valid_until, created_at, updated_at
                )
                SELECT :id, :user_id, roles.id, NULL, NULL,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                FROM roles WHERE roles.code = 'reviewer'
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "id": "e0ad5f0c-8c82-49ae-b31a-3015f44bd247",
                "user_id": str(BETA_USER_ID),
            },
        )
        beta_private_event_id = "88d374ea-7db5-403c-aafe-4a7c862893ba"
        _create_scoped_lead(
            connection,
            user_id=BETA_USER_ID,
            tenant_id=BETA_TENANT_ID,
            scope="organization_private",
            owner_user_id=None,
            owner_tenant_id=BETA_TENANT_ID,
            suffix="organization-beta-admin-test",
            document_id="17a11817-6324-4d52-8f03-f96a1bbe90f5",
            mention_id="ad71d3f0-6bef-4444-bf17-6f4c8419ef47",
            event_id=beta_private_event_id,
            evidence_id="cb70fab8-9c9f-47a9-8588-3a87e68b3e38",
        )
        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(ALPHA_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM events WHERE id = :id"),
                {"id": beta_private_event_id},
            )
            == 0
        )
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                connection.execute(
                    text(
                        """
                        INSERT INTO event_evidence (
                            id, event_id, raw_document_id, source_event_evidence_id,
                            owner_user_id, owner_tenant_id, visibility_scope,
                            evidence_excerpt, span_hash, support_type, display_allowed,
                            created_at, updated_at
                        ) VALUES (
                            :id, :event_id, :raw_document_id, NULL,
                            NULL, :owner_tenant_id, 'organization_private',
                            'cross-tenant attachment must fail', :span_hash, 'supports', FALSE,
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {
                        "id": "d46870e9-44c8-4ed3-8cd2-e3f1befa0b4d",
                        "event_id": beta_private_event_id,
                        "raw_document_id": "e2fc5bea-0e37-48ef-8688-359657f45775",
                        "owner_tenant_id": str(ALPHA_TENANT_ID),
                        "span_hash": "8" * 64,
                    },
                )

        shared_event_id = "8ec6df70-2dbc-46b7-b180-775b8af0a580"
        shared_event_insert = text(
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
                :id, :company_id, 'platform_shared', NULL, NULL,
                'information_quality', 'sharing_rls_test', 'published', 'neutral', 10,
                'low', 0.900, 'A', 'RLS shared fact', 'RLS shared fact',
                CAST('[]' AS JSON), CAST('[]' AS JSON), NULL, NULL, NULL,
                CURRENT_TIMESTAMP, 'sharing-rls-v1', :fingerprint,
                'human_promoted', 'controlled-promotion-v1', CAST('[]' AS JSON),
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        )
        shared_event_parameters = {
            "id": shared_event_id,
            "company_id": str(demo_uuid("company-示例星河科技一号有限公司")),
            "fingerprint": "7" * 64,
        }
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                connection.execute(shared_event_insert, shared_event_parameters)

        connection.execute(
            text(
                """
                INSERT INTO user_role_assignments (
                    id, user_id, role_id, scope_id, valid_until, created_at, updated_at
                )
                SELECT :id, :user_id, roles.id, NULL, NULL,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                FROM roles WHERE roles.code = 'platform_admin'
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "id": "8dce4c28-92df-491b-8d65-29304b1995fc",
                "user_id": str(ALPHA_USER_ID),
            },
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM events WHERE id = :id"),
                {"id": beta_private_event_id},
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM events WHERE id = :id"),
                {"id": "41508868-cf3f-4b6c-84ea-7df06ca21e0f"},
            )
            == 1
        )
        connection.execute(shared_event_insert, shared_event_parameters)
        audit_id = "4ee61a7f-bb2d-4aeb-a0db-f7dd91b31af5"
        connection.execute(
            text(
                """
                INSERT INTO event_sharing_decisions (
                    id, source_event_id, shared_event_id, actor_user_id,
                    actor_tenant_id, action, reason, shared_title, shared_summary,
                    policy_version, idempotency_key, created_at
                ) VALUES (
                    :id, NULL, :shared_event_id, :actor_user_id,
                    :actor_tenant_id, 'retract', 'RLS append-only test',
                    'RLS shared fact', 'RLS shared fact',
                    'controlled-promotion-v1', :idempotency_key, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": audit_id,
                "shared_event_id": shared_event_id,
                "actor_user_id": str(ALPHA_USER_ID),
                "actor_tenant_id": str(ALPHA_TENANT_ID),
                "idempotency_key": "8" * 64,
            },
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM event_sharing_decisions WHERE id = :id"),
                {"id": audit_id},
            )
            == 1
        )
        update_result = connection.execute(
            text("UPDATE event_sharing_decisions SET reason = 'tampered' WHERE id = :id"),
            {"id": audit_id},
        )
        assert update_result.rowcount == 0
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                connection.execute(
                    text("DELETE FROM event_sharing_decisions WHERE id = :id"),
                    {"id": audit_id},
                )
        assert (
            connection.scalar(
                text("SELECT reason FROM event_sharing_decisions WHERE id = :id"),
                {"id": audit_id},
            )
            == "RLS append-only test"
        )
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_investor_change_analysis_rls_hides_queue_and_allows_completed_shared_read() -> None:
    assert POSTGRES_RLS_DATABASE_URL is not None
    engine = create_engine(POSTGRES_RLS_DATABASE_URL, pool_pre_ping=True)
    connection = engine.connect()
    transaction = connection.begin()
    event_id = str(uuid4())
    analysis_id = str(uuid4())
    try:
        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(ALPHA_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        connection.execute(
            text(
                """
                INSERT INTO user_role_assignments (
                    id, user_id, role_id, scope_id, valid_until, created_at, updated_at
                )
                SELECT :id, :user_id, roles.id, NULL, NULL,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                FROM roles
                WHERE roles.code = 'platform_admin'
                  AND NOT EXISTS (
                      SELECT 1 FROM user_role_assignments AS existing
                      WHERE existing.user_id = :user_id
                        AND existing.role_id = roles.id
                  )
                """
            ),
            {"id": str(uuid4()), "user_id": str(ALPHA_USER_ID)},
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
                    :id, :company_id, 'platform_shared', NULL, NULL,
                    'financing_cap_table', 'shareholder_ratio_changed', 'published',
                    'neutral', 80, 'low', 0.950, 'A',
                    'RLS investor change', '登记持股比例由20%变为25%。',
                    CAST(:facts AS JSON),
                    CAST('[]' AS JSON), CURRENT_TIMESTAMP, NULL, NULL,
                    CURRENT_TIMESTAMP, 'change-v1', :fingerprint,
                    'deterministic_change', 'investor-material-change-v1',
                    CAST('["material_change_threshold_met"]' AS JSON),
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": event_id,
                "company_id": str(demo_uuid("company-示例星河科技一号有限公司")),
                "fingerprint": uuid4().hex.ljust(64, "0"),
                "facts": (
                    '[{"name":"变化字段","value":"工商登记持股比例","unit":null},'
                    '{"name":"变更前","value":"20%","unit":null},'
                    '{"name":"变更后","value":"25%","unit":null}]'
                ),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO investor_change_analyses (
                    id, event_id, visibility_scope, status, provider, model,
                    prompt_version, schema_version, input_hash, evidence_ids,
                    analysis_output, input_tokens, output_tokens, estimated_cost,
                    attempt_count, response_id, last_error_code, leased_until,
                    heartbeat_at, created_at, updated_at
                ) VALUES (
                    :id, :event_id, 'platform_shared', 'pending', NULL, NULL,
                    'investor-change-zh-v1', 'investor-change-analysis-v1',
                    :input_hash, CAST('[]' AS JSON), NULL, 0, 0, 0, 0,
                    NULL, NULL, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": analysis_id,
                "event_id": event_id,
                "input_hash": "a" * 64,
            },
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM investor_change_analyses WHERE id = :id"),
                {"id": analysis_id},
            )
            == 1
        )

        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(NO_ACCESS_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM investor_change_analyses WHERE id = :id"),
                {"id": analysis_id},
            )
            == 0
        )
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                connection.execute(
                    text(
                        """
                        INSERT INTO investor_change_analyses (
                            id, event_id, visibility_scope, status, prompt_version,
                            schema_version, input_hash, evidence_ids, input_tokens,
                            output_tokens, estimated_cost, attempt_count,
                            created_at, updated_at
                        ) VALUES (
                            :id, :event_id, 'platform_shared', 'pending', 'unauthorized',
                            'investor-change-analysis-v1', :hash, CAST('[]' AS JSON),
                            0, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {"id": str(uuid4()), "event_id": event_id, "hash": "b" * 64},
                )

        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(ALPHA_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        connection.execute(
            text(
                "UPDATE investor_change_analyses "
                "SET status = 'completed', analysis_output = CAST('{}' AS JSON), "
                "updated_at = CURRENT_TIMESTAMP WHERE id = :id"
            ),
            {"id": analysis_id},
        )

        for user_id, tenant_id in (
            (NO_ACCESS_USER_ID, ALPHA_TENANT_ID),
            (BETA_USER_ID, BETA_TENANT_ID),
        ):
            connection.execute(
                text(
                    "SELECT set_config('app.current_user_id', :user_id, true), "
                    "set_config('app.current_tenant_id', :tenant_id, true)"
                ),
                {"user_id": str(user_id), "tenant_id": str(tenant_id)},
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM investor_change_analyses WHERE id = :id"),
                    {"id": analysis_id},
                )
                == 1
            )
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_investor_analysis_worker_rebinds_postgres_rls_context_after_commits() -> None:
    if not DATABASE_ADMIN_URL:
        pytest.skip("set DATABASE_ADMIN_URL to prepare worker transaction fixtures")
    assert POSTGRES_RLS_DATABASE_URL is not None
    role_assignment_id = uuid4()
    document_id = uuid4()
    event_id = uuid4()
    evidence_id = uuid4()
    company_id = demo_uuid("company-示例星河科技一号有限公司")

    class FixedAnalysisProvider:
        code = "postgres_analysis_test"
        model = "fixed-output"

        def analyze_investor_change(
            self,
            request: InvestorChangeAnalysisRequest,
        ) -> LLMProviderResult:
            return LLMProviderResult(
                analysis=InvestorChangeAnalysisOutput(
                    schema_version=INVESTOR_ANALYSIS_SCHEMA_VERSION,
                    headline="工商登记持股比例发生变化",
                    before_value=request.before_value,
                    after_value=request.after_value,
                    what_changed="工商登记持股比例由20%变为25%。",
                    why_it_matters="该变化可能影响股东表决权和公司治理判断。",
                    potential_impacts=["需要重新核对治理关系。"],
                    uncertainties=["实际控制关系尚不确定。"],
                    evidence_ids=[request.evidence[0].evidence_id],
                    confidence=0.9,
                    follow_up_items=["关注后续股东变更。"],
                    impact_direction="uncertain",
                    disclaimer=INVESTOR_ANALYSIS_DISCLAIMER,
                ),
                external_calls=0,
                input_tokens=10,
                output_tokens=5,
                estimated_cost=Decimal("0"),
                response_id="postgres-fixed-output",
            )

    admin_engine = create_engine(DATABASE_ADMIN_URL, pool_pre_ping=True)
    app_engine = create_engine(POSTGRES_RLS_DATABASE_URL, pool_pre_ping=True)
    try:
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO user_role_assignments (
                        id, user_id, role_id, scope_id, valid_until, created_at, updated_at
                    )
                    SELECT :id, :user_id, roles.id, NULL, NULL,
                           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    FROM roles
                    WHERE roles.code = 'platform_admin'
                      AND NOT EXISTS (
                          SELECT 1 FROM user_role_assignments AS existing
                          WHERE existing.user_id = :user_id
                            AND existing.role_id = roles.id
                      )
                    """
                ),
                {"id": str(role_assignment_id), "user_id": str(ALPHA_USER_ID)},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO raw_documents (
                        id, source_id, research_import_id, candidate_document_id,
                        owner_user_id, owner_tenant_id, visibility_scope,
                        external_record_id, canonical_url, title, published_at,
                        published_on, observed_at, content_hash, document_dedupe_key,
                        license_status, payload, created_at, updated_at
                    ) VALUES (
                        :id, :source_id, NULL, NULL, NULL, NULL, 'platform_shared',
                        :external_record_id, :canonical_url, '共享变化测试证据', NULL,
                        NULL, CURRENT_TIMESTAMP, :content_hash, :dedupe_key,
                        'permission_confirmed', CAST('{}' AS JSON),
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "id": str(document_id),
                    "source_id": str(MOCK_SOURCE_ID),
                    "external_record_id": f"postgres-analysis-{document_id}",
                    "canonical_url": f"https://example.invalid/postgres-analysis-{document_id}",
                    "content_hash": uuid4().hex.ljust(64, "0"),
                    "dedupe_key": uuid4().hex.ljust(64, "0"),
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
                        :id, :company_id, 'platform_shared', NULL, NULL,
                        'financing_cap_table', 'shareholder_ratio_changed', 'published',
                        'neutral', 80, 'low', 0.950, 'A',
                        'PostgreSQL Worker 变化', '工商登记持股比例由20%变为25%。',
                        CAST(:facts AS JSON), CAST(:uncertainties AS JSON),
                        CURRENT_TIMESTAMP, NULL, NULL, CURRENT_TIMESTAMP,
                        'change-v1', :fingerprint, 'deterministic_change',
                        'investor-material-change-v1',
                        CAST('["material_change_threshold_met"]' AS JSON),
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "id": str(event_id),
                    "company_id": str(company_id),
                    "fingerprint": uuid4().hex.ljust(64, "0"),
                    "facts": (
                        '[{"name":"变化字段","value":"工商登记持股比例","unit":null},'
                        '{"name":"变更前","value":"20%","unit":null},'
                        '{"name":"变更后","value":"25%","unit":null}]'
                    ),
                    "uncertainties": '["实际控制关系尚不确定。"]',
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO event_evidence (
                        id, event_id, raw_document_id, source_event_evidence_id,
                        owner_user_id, owner_tenant_id, visibility_scope,
                        evidence_excerpt, span_hash, support_type,
                        display_source_name, display_source_quality, display_title,
                        display_canonical_url, display_observed_at,
                        display_url_health_status, display_license_status,
                        display_allowed, created_at, updated_at
                    ) VALUES (
                        :id, :event_id, :document_id, NULL, NULL, NULL,
                        'platform_shared', '工商登记持股比例由20%变为25%。',
                        :span_hash, 'supports', '授权工商数据源', 'A',
                        '股东信息比较记录', :canonical_url, CURRENT_TIMESTAMP,
                        'unchecked', 'permission_confirmed', TRUE,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "id": str(evidence_id),
                    "event_id": str(event_id),
                    "document_id": str(document_id),
                    "span_hash": uuid4().hex.ljust(64, "0"),
                    "canonical_url": f"https://example.invalid/postgres-analysis-{document_id}",
                },
            )

        with Session(app_engine, expire_on_commit=False) as session:
            set_request_context(session, ALPHA_USER_ID, ALPHA_TENANT_ID)
            worker_user = session.get(User, ALPHA_USER_ID)
            assert worker_user is not None
            result = run_investor_analysis_worker_once(
                session,
                worker_user,
                FixedAnalysisProvider(),
                InvestorAnalysisPolicy(),
            )

        assert result.outcome == "completed"
        with admin_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT status FROM investor_change_analyses WHERE event_id = :event_id"),
                    {"event_id": str(event_id)},
                )
                == "completed"
            )
    finally:
        with admin_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM usage_ledger WHERE metrics->>'event_id' = :event_id"),
                {"event_id": str(event_id)},
            )
            connection.execute(
                text("DELETE FROM events WHERE id = :event_id"),
                {"event_id": str(event_id)},
            )
            connection.execute(
                text("DELETE FROM raw_documents WHERE id = :document_id"),
                {"document_id": str(document_id)},
            )
            connection.execute(
                text("DELETE FROM user_role_assignments WHERE id = :id"),
                {"id": str(role_assignment_id)},
            )
        app_engine.dispose()
        admin_engine.dispose()


def test_company_suggestions_keep_private_aliases_hidden_under_postgres_rls() -> None:
    if not DATABASE_ADMIN_URL:
        pytest.skip("set DATABASE_ADMIN_URL to prepare suggestion scope fixtures")

    shared_company_id = demo_uuid("company-示例星河科技一号有限公司")
    shared_alias_id = uuid4()
    private_alias_id = uuid4()
    admin_engine = create_engine(DATABASE_ADMIN_URL)
    with admin_engine.begin() as connection:
        usage_count = connection.execute(
            text(
                "SELECT count(*) FROM personal_usage_records WHERE owner_user_id = :owner_user_id"
            ),
            {"owner_user_id": str(NO_ACCESS_USER_ID)},
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO company_aliases "
                "(id, company_id, source_id, owner_user_id, owner_tenant_id, "
                "visibility_scope, alias, normalized_alias, alias_type, "
                "verification_status, created_at, updated_at) VALUES "
                "(:shared_id, :company_id, :source_id, NULL, NULL, "
                "'platform_shared', '共享联想代号', '共享联想代号', "
                "'suggestion_shared_test', 'verified', now(), now()), "
                "(:private_id, :company_id, :source_id, NULL, :tenant_id, "
                "'organization_private', '机密联想代号', '机密联想代号', "
                "'suggestion_private_test', 'verified', now(), now())"
            ),
            {
                "shared_id": str(shared_alias_id),
                "private_id": str(private_alias_id),
                "company_id": str(shared_company_id),
                "source_id": str(MOCK_SOURCE_ID),
                "tenant_id": str(ALPHA_TENANT_ID),
            },
        )

    settings = Settings(
        database_url=POSTGRES_RLS_DATABASE_URL,
        app_mode="demo",
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
    )
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            headers = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}
            legal_name_matches = client.get(
                "/api/v1/companies/suggestions",
                params={"q": "星河科技", "limit": 3},
                headers=headers,
            )
            assert legal_name_matches.status_code == 200
            assert len(legal_name_matches.json()) == 3
            assert all("星河科技" in item["legal_name"] for item in legal_name_matches.json())

            shared_alias = client.get(
                "/api/v1/companies/suggestions",
                params={"q": "共享联想代号"},
                headers=headers,
            )
            assert [item["id"] for item in shared_alias.json()] == [str(shared_company_id)]

            private_alias = client.get(
                "/api/v1/companies/suggestions",
                params={"q": "机密联想代号"},
                headers=headers,
            )
            assert private_alias.status_code == 200
            assert private_alias.json() == []

        with admin_engine.begin() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM personal_usage_records "
                        "WHERE owner_user_id = :owner_user_id"
                    ),
                    {"owner_user_id": str(NO_ACCESS_USER_ID)},
                ).scalar_one()
                == usage_count
            )
    finally:
        with admin_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM company_aliases WHERE id IN (:shared_id, :private_id)"),
                {"shared_id": str(shared_alias_id), "private_id": str(private_alias_id)},
            )
        admin_engine.dispose()


def test_trusted_source_monitoring_rls_is_platform_admin_and_tenant_scoped() -> None:
    assert POSTGRES_RLS_DATABASE_URL is not None
    engine = create_engine(POSTGRES_RLS_DATABASE_URL, pool_pre_ping=True)
    connection = engine.connect()
    transaction = connection.begin()
    source_id = "c3d5f98a-a4dc-45f0-becb-b86e44f97452"
    run_id = "3872fe98-dbbe-4385-806d-8082bc027fb0"
    candidate_id = "e775a5e4-988a-4fd1-8e13-1806d07945a7"
    company_id = str(demo_uuid("company-示例星河科技一号有限公司"))
    try:
        for user_id, tenant_id, assignment_id in (
            (
                ALPHA_USER_ID,
                ALPHA_TENANT_ID,
                "369e78c1-420d-4301-9553-0e66d548aa5c",
            ),
            (
                BETA_USER_ID,
                BETA_TENANT_ID,
                "886f3fe2-1bbd-46b1-9d2d-36ec59034e51",
            ),
        ):
            connection.execute(
                text(
                    "SELECT set_config('app.current_user_id', :user_id, true), "
                    "set_config('app.current_tenant_id', :tenant_id, true)"
                ),
                {"user_id": str(user_id), "tenant_id": str(tenant_id)},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO user_role_assignments (
                        id, user_id, role_id, scope_id, valid_until, created_at, updated_at
                    )
                    SELECT :id, :user_id, roles.id, NULL, NULL,
                           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    FROM roles WHERE roles.code = 'platform_admin'
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"id": assignment_id, "user_id": str(user_id)},
            )

        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(ALPHA_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        connection.execute(
            text(
                """
                INSERT INTO trusted_sources (
                    id, tenant_id, company_id, name, source_type, root_domain,
                    start_url, enabled, access_basis, license_status,
                    check_frequency_minutes, content_retention_policy,
                    visibility_scope, consecutive_failures, created_by, updated_by,
                    created_at, updated_at
                ) VALUES (
                    :id, :tenant_id, :company_id, 'RLS trusted source', 'single_page',
                    'example.invalid', 'https://example.invalid/news', TRUE,
                    'RLS controlled source', 'public_access', 10080, 'metadata_only',
                    'organization_private', 0, :user_id, :user_id,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": source_id,
                "tenant_id": str(ALPHA_TENANT_ID),
                "company_id": company_id,
                "user_id": str(ALPHA_USER_ID),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO source_check_runs (
                    id, tenant_id, company_id, trusted_source_id, requested_by,
                    status, dry_run, visibility_scope, policy_version, idempotency_key,
                    max_requests, max_download_bytes, max_response_bytes,
                    timeout_seconds, retry_limit, max_redirects, request_count,
                    downloaded_bytes, new_count, changed_count, unchanged_count,
                    duplicate_count, failure_count, external_calls, paid_api_calls,
                    input_tokens, output_tokens, estimated_cost, request_log,
                    created_at, updated_at
                ) VALUES (
                    :id, :tenant_id, :company_id, :source_id, :user_id,
                    'completed', TRUE, 'organization_private', 'trusted-source-v1',
                    :idempotency_key, 10, 5000000, 1000000, 10, 1, 3,
                    0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    CAST('[]' AS JSON), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": run_id,
                "tenant_id": str(ALPHA_TENANT_ID),
                "company_id": company_id,
                "source_id": source_id,
                "user_id": str(ALPHA_USER_ID),
                "idempotency_key": "6" * 64,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO candidate_documents (
                    id, tenant_id, company_id, trusted_source_id, discovery_run_id,
                    previous_candidate_id, canonical_url, title, published_at,
                    first_discovered_at, last_observed_at, content_hash, change_type,
                    link_health_status, http_status, excerpt, license_status,
                    processing_status, identity_status_at_discovery, visibility_scope,
                    document_metadata, handoff_payload, created_at, updated_at
                ) VALUES (
                    :id, :tenant_id, :company_id, :source_id, :run_id,
                    NULL, 'https://example.invalid/news', 'RLS candidate', NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :content_hash, 'new',
                    'healthy', 200, NULL, 'public_access', 'pending', 'verified',
                    'organization_private', CAST('{}' AS JSON), CAST('{}' AS JSON),
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": candidate_id,
                "tenant_id": str(ALPHA_TENANT_ID),
                "company_id": company_id,
                "source_id": source_id,
                "run_id": run_id,
                "content_hash": "7" * 64,
            },
        )
        assert connection.execute(
            text(
                "SELECT (SELECT count(*) FROM trusted_sources), "
                "(SELECT count(*) FROM source_check_runs), "
                "(SELECT count(*) FROM candidate_documents)"
            )
        ).one() == (1, 1, 1)

        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(BETA_USER_ID), "tenant_id": str(BETA_TENANT_ID)},
        )
        assert connection.execute(
            text(
                "SELECT (SELECT count(*) FROM trusted_sources), "
                "(SELECT count(*) FROM source_check_runs), "
                "(SELECT count(*) FROM candidate_documents)"
            )
        ).one() == (0, 0, 0)
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                connection.execute(
                    text(
                        """
                        INSERT INTO trusted_sources (
                            id, tenant_id, company_id, name, source_type, root_domain,
                            start_url, enabled, access_basis, license_status,
                            check_frequency_minutes, content_retention_policy,
                            visibility_scope, consecutive_failures, created_by, updated_by,
                            created_at, updated_at
                        ) VALUES (
                            :id, :tenant_id, :company_id, 'forged source', 'single_page',
                            'example.invalid', 'https://example.invalid/forged', TRUE,
                            'cross tenant attempt', 'public_access', 10080, 'metadata_only',
                            'organization_private', 0, :user_id, :user_id,
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {
                        "id": "23a96772-6050-4c9f-a8dd-fcfcd76da650",
                        "tenant_id": str(ALPHA_TENANT_ID),
                        "company_id": company_id,
                        "user_id": str(BETA_USER_ID),
                    },
                )

        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(NO_ACCESS_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        assert connection.execute(
            text(
                "SELECT (SELECT count(*) FROM trusted_sources), "
                "(SELECT count(*) FROM source_check_runs), "
                "(SELECT count(*) FROM candidate_documents)"
            )
        ).one() == (0, 0, 0)
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_candidate_research_import_restores_rls_context_after_commit() -> None:
    assert POSTGRES_RLS_DATABASE_URL is not None
    suffix = uuid4().hex
    source_url = f"https://example.com/postgres-rls-candidate-handoff-{suffix}"
    settings = replace(
        Settings.from_env(),
        database_url=POSTGRES_RLS_DATABASE_URL,
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
        trusted_source_calls_enabled=False,
        source_monitor_scheduler_enabled=False,
    )
    engine = create_engine(POSTGRES_RLS_DATABASE_URL, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(ALPHA_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        connection.execute(
            text(
                """
                INSERT INTO user_role_assignments (
                    id, user_id, role_id, scope_id, valid_until, created_at, updated_at
                )
                SELECT :id, :user_id, roles.id, NULL, NULL,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                FROM roles WHERE roles.code = 'platform_admin'
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "id": str(uuid4()),
                "user_id": str(ALPHA_USER_ID),
            },
        )

    with TestClient(create_app(settings)) as client:
        headers = {"X-Demo-User-Id": str(ALPHA_USER_ID)}
        source_response = client.post(
            "/api/v1/trusted-sources",
            headers=headers,
            json={
                "company_id": str(demo_uuid("company-示例星河科技一号有限公司")),
                "name": f"PostgreSQL RLS 候选交接测试来源 {suffix}",
                "source_type": "single_page",
                "root_domain": "example.com",
                "start_url": source_url,
                "access_basis": "确定性 PostgreSQL 交接测试",
                "license_status": "permission_confirmed",
                "content_retention_policy": "minimal_excerpt",
            },
        )
        assert source_response.status_code == 200, source_response.text
        source_id = source_response.json()["id"]

        run_id = str(uuid4())
        candidate_id = str(uuid4())
        with engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT set_config('app.current_user_id', :user_id, true), "
                    "set_config('app.current_tenant_id', :tenant_id, true)"
                ),
                {"user_id": str(ALPHA_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO source_check_runs (
                        id, tenant_id, company_id, trusted_source_id, requested_by,
                        status, dry_run, visibility_scope, policy_version, idempotency_key,
                        max_requests, max_download_bytes, max_response_bytes,
                        timeout_seconds, retry_limit, max_redirects, request_count,
                        downloaded_bytes, new_count, changed_count, unchanged_count,
                        duplicate_count, failure_count, external_calls, paid_api_calls,
                        input_tokens, output_tokens, estimated_cost, request_log,
                        created_at, updated_at
                    ) VALUES (
                        :id, :tenant_id, :company_id, :source_id, :user_id,
                        'completed', FALSE, 'organization_private', 'trusted-source-v1',
                        :idempotency_key, 10, 5000000, 1000000, 10, 1, 3,
                        0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                        CAST('[]' AS JSON), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "id": run_id,
                    "tenant_id": str(ALPHA_TENANT_ID),
                    "company_id": str(demo_uuid("company-示例星河科技一号有限公司")),
                    "source_id": source_id,
                    "user_id": str(ALPHA_USER_ID),
                    "idempotency_key": suffix * 2,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO candidate_documents (
                        id, tenant_id, company_id, trusted_source_id, discovery_run_id,
                        canonical_url, title, first_discovered_at, last_observed_at,
                        content_hash, change_type, link_health_status, http_status,
                        excerpt, license_status, processing_status,
                        identity_status_at_discovery, visibility_scope, document_metadata,
                        handoff_payload, processed_by, processed_at, decision_reason,
                        created_at, updated_at
                    ) VALUES (
                        :id, :tenant_id, :company_id, :source_id, :run_id,
                        :canonical_url,
                        'PostgreSQL RLS 候选交接', CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP, :content_hash, 'new', 'healthy', 200,
                        '可复核的候选摘录', 'permission_confirmed',
                        'worth_research', 'verified', 'organization_private',
                        CAST('{}' AS JSON), CAST('{}' AS JSON), :user_id,
                        CURRENT_TIMESTAMP, '值得研究', CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "id": candidate_id,
                    "tenant_id": str(ALPHA_TENANT_ID),
                    "company_id": str(demo_uuid("company-示例星河科技一号有限公司")),
                    "source_id": source_id,
                    "run_id": run_id,
                    "canonical_url": source_url,
                    "content_hash": suffix[::-1] * 2,
                    "user_id": str(ALPHA_USER_ID),
                },
            )

        payload = {
            "title": "PostgreSQL RLS 候选交接",
            "evidence_excerpt": "可复核的候选摘录",
            "event_type": "information_quality",
            "event_subtype": "candidate_handoff",
            "direction": "neutral",
            "materiality_score": 20,
            "risk_severity": "none",
            "confidence_score": 0.9,
            "source_quality": "A",
            "fact_name": "handoff_status",
            "fact_value": "verified",
            "uncertainties": [],
            "research_reason": "验证提交后 RLS 上下文恢复",
        }
        first = client.post(
            f"/api/v1/candidate-documents/{candidate_id}/research-import",
            headers=headers,
            json=payload,
        )
        assert first.status_code == 200, first.text
        assert first.json()["reused"] is False
        repeated = client.post(
            f"/api/v1/candidate-documents/{candidate_id}/research-import",
            headers=headers,
            json=payload,
        )
        assert repeated.status_code == 200
        assert repeated.json()["reused"] is True
        assert repeated.json()["private_event_id"] == first.json()["private_event_id"]

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    "SELECT set_config('app.current_user_id', :user_id, true), "
                    "set_config('app.current_tenant_id', :tenant_id, true)"
                ),
                {"user_id": str(ALPHA_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
            )
            lineage = connection.execute(
                text(
                    "SELECT count(*), min(owner_tenant_id::text) "
                    "FROM raw_documents WHERE candidate_document_id = :candidate_id"
                ),
                {"candidate_id": candidate_id},
            ).one()
            assert lineage == (1, str(ALPHA_TENANT_ID))
        finally:
            transaction.rollback()
    engine.dispose()


def test_authentication_audit_rls_is_self_scoped_and_append_only() -> None:
    assert POSTGRES_RLS_DATABASE_URL is not None
    engine = create_engine(POSTGRES_RLS_DATABASE_URL, pool_pre_ping=True)
    connection = engine.connect()
    transaction = connection.begin()
    audit_id = str(uuid4())
    try:
        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(ALPHA_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        connection.execute(
            text(
                """
                INSERT INTO authentication_audit_logs (
                    id, tenant_id, user_id, provider, subject_hash,
                    event_type, outcome, reason_code, created_at
                ) VALUES (
                    :id, :tenant_id, :user_id, 'cloudbase', :subject_hash,
                    'session_started', 'succeeded', NULL, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": audit_id,
                "tenant_id": str(ALPHA_TENANT_ID),
                "user_id": str(ALPHA_USER_ID),
                "subject_hash": "a" * 64,
            },
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM authentication_audit_logs WHERE id = :id"),
                {"id": audit_id},
            )
            == 1
        )
        update_result = connection.execute(
            text("UPDATE authentication_audit_logs SET reason_code = 'tampered' WHERE id = :id"),
            {"id": audit_id},
        )
        assert update_result.rowcount == 0
        assert (
            connection.scalar(
                text("SELECT reason_code FROM authentication_audit_logs WHERE id = :id"),
                {"id": audit_id},
            )
            is None
        )

        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(BETA_USER_ID), "tenant_id": str(BETA_TENANT_ID)},
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM authentication_audit_logs WHERE id = :id"),
                {"id": audit_id},
            )
            == 0
        )
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                connection.execute(
                    text(
                        """
                        INSERT INTO authentication_audit_logs (
                            id, tenant_id, user_id, provider, subject_hash,
                            event_type, outcome, reason_code, created_at
                        ) VALUES (
                            :id, :tenant_id, :user_id, 'cloudbase', :subject_hash,
                            'session_started', 'succeeded', NULL, CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "tenant_id": str(ALPHA_TENANT_ID),
                        "user_id": str(ALPHA_USER_ID),
                        "subject_hash": "b" * 64,
                    },
                )
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_personal_retention_rls_is_owner_private_and_requests_are_explicitly_admin_reviewable() -> (
    None
):
    assert POSTGRES_RLS_DATABASE_URL is not None
    engine = create_engine(POSTGRES_RLS_DATABASE_URL, pool_pre_ping=True)
    connection = engine.connect()
    transaction = connection.begin()
    company_id = str(demo_uuid("company-示例星河科技一号有限公司"))
    personal_request_id = "94af856a-a089-4055-9dca-0a427daf20fe"
    beta_request_id = "5b8c9b46-12e0-4ee2-8141-8d81596e1588"
    try:
        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(NO_ACCESS_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        connection.execute(
            text(
                """
                INSERT INTO personal_watchlist_items (
                    id, owner_user_id, company_id, created_at, updated_at
                ) VALUES (
                    :id, :owner_user_id, :company_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": "117ea380-dc73-4e8f-a0c4-8b60c2611fef",
                "owner_user_id": str(NO_ACCESS_USER_ID),
                "company_id": company_id,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO personal_usage_records (
                    id, owner_user_id, operation, period_key,
                    resource_id, idempotency_key, created_at
                ) VALUES (
                    :id, :owner_user_id, 'company_search', '2026-07',
                    NULL, :idempotency_key, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": "d2e68eab-8b40-4a62-80ea-3bf716791423",
                "owner_user_id": str(NO_ACCESS_USER_ID),
                "idempotency_key": "a" * 64,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO personal_company_requests (
                    id, owner_user_id, request_type, company_id,
                    requested_name, requested_credit_code, target_key,
                    status, reviewed_by_id, reviewed_at, decision_reason,
                    created_at, updated_at
                ) VALUES (
                    :id, :owner_user_id, 'refresh', :company_id,
                    '示例星河科技一号有限公司', '91310000MA1K000006',
                    :target_key, 'pending', NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": personal_request_id,
                "owner_user_id": str(NO_ACCESS_USER_ID),
                "company_id": company_id,
                "target_key": f"company:{company_id}",
            },
        )
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                connection.execute(
                    text(
                        """
                        INSERT INTO personal_watchlist_items (
                            id, owner_user_id, company_id, created_at, updated_at
                        ) VALUES (
                            :id, :owner_user_id, :company_id,
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {
                        "id": "fd52a03d-eab7-4d41-9487-5c993856bdcb",
                        "owner_user_id": str(BETA_USER_ID),
                        "company_id": company_id,
                    },
                )

        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(BETA_USER_ID), "tenant_id": str(BETA_TENANT_ID)},
        )
        assert connection.scalar(text("SELECT count(*) FROM personal_watchlist_items")) == 0
        assert connection.scalar(text("SELECT count(*) FROM personal_usage_records")) == 0
        assert connection.scalar(text("SELECT count(*) FROM personal_company_requests")) == 0
        cross_owner_delete = connection.execute(
            text("DELETE FROM personal_watchlist_items WHERE id = :id"),
            {"id": "117ea380-dc73-4e8f-a0c4-8b60c2611fef"},
        )
        assert cross_owner_delete.rowcount == 0
        connection.execute(
            text(
                """
                INSERT INTO personal_company_requests (
                    id, owner_user_id, request_type, company_id,
                    requested_name, requested_credit_code, target_key,
                    status, reviewed_by_id, reviewed_at, decision_reason,
                    created_at, updated_at
                ) VALUES (
                    :id, :owner_user_id, 'refresh', :company_id,
                    '示例星河科技一号有限公司', '91310000MA1K000006',
                    :target_key, 'pending', NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": beta_request_id,
                "owner_user_id": str(BETA_USER_ID),
                "company_id": company_id,
                "target_key": f"company:{company_id}",
            },
        )

        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(NO_ACCESS_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        assert connection.scalar(text("SELECT count(*) FROM personal_company_requests")) == 1
        owner_delete = connection.execute(
            text("DELETE FROM personal_watchlist_items WHERE id = :id"),
            {"id": "117ea380-dc73-4e8f-a0c4-8b60c2611fef"},
        )
        assert owner_delete.rowcount == 1
        connection.execute(
            text(
                """
                INSERT INTO user_role_assignments (
                    id, user_id, role_id, scope_id, valid_until, created_at, updated_at
                )
                SELECT :id, :user_id, roles.id, NULL, NULL,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                FROM roles WHERE roles.code = 'platform_admin'
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "id": "c4d7a935-f453-425c-8909-4ba8659ce720",
                "user_id": str(ALPHA_USER_ID),
            },
        )
        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(ALPHA_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        assert connection.scalar(text("SELECT count(*) FROM personal_company_requests")) == 2
        assert connection.scalar(text("SELECT count(*) FROM personal_watchlist_items")) == 0
        assert connection.scalar(text("SELECT count(*) FROM personal_usage_records")) == 0
        updated = connection.execute(
            text(
                """
                UPDATE personal_company_requests
                SET status = 'completed', reviewed_by_id = :reviewer_id,
                    reviewed_at = CURRENT_TIMESTAMP, decision_reason = 'RLS admin review',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
                """
            ),
            {"reviewer_id": str(ALPHA_USER_ID), "id": beta_request_id},
        )
        assert updated.rowcount == 1

        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(NO_ACCESS_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        assert connection.scalar(text("SELECT count(*) FROM personal_company_requests")) == 1
        owner_update = connection.execute(
            text(
                "UPDATE personal_company_requests "
                "SET status = 'cancelled', cancel_requested_at = CURRENT_TIMESTAMP "
                "WHERE id = :id"
            ),
            {"id": personal_request_id},
        )
        assert owner_update.rowcount == 1
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_retired_provider_specific_rls_policies_are_absent() -> None:
    assert POSTGRES_RLS_DATABASE_URL is not None
    engine = create_engine(POSTGRES_RLS_DATABASE_URL, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            policy_names = set(
                connection.scalars(
                    text("SELECT policyname FROM pg_policies WHERE schemaname = current_schema()")
                )
            )
        assert (
            not {
                "usage_ledger_tianyancha_platform_admin_read",
                "raw_documents_tianyancha_admin_read",
                "raw_documents_tianyancha_admin_insert",
                "official_identity_verifications_platform_admin_read",
                "official_identity_verifications_platform_admin_insert",
                "official_identity_verifications_platform_admin_update",
            }
            & policy_names
        )
    finally:
        engine.dispose()


def test_cloudbase_login_uses_postgres_rls_application_role() -> None:
    assert POSTGRES_RLS_DATABASE_URL is not None
    settings = replace(
        Settings.from_env(),
        database_url=POSTGRES_RLS_DATABASE_URL,
        auth_provider="cloudbase",
        cloudbase_auth_policy=CloudBaseAuthPolicy(env_id="postgres-test-env"),
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
    )
    app = create_app(settings, identity_provider=_PostgresCloudBaseProvider())
    with TestClient(app) as client:
        challenge = client.post(
            "/api/v1/auth/email/verification",
            json={"email": "alpha-admin@example.invalid"},
        )
        assert challenge.status_code == 200
        login = client.post(
            "/api/v1/auth/email/login",
            json={
                "verification_id": challenge.json()["verification_id"],
                "verification_code": "123456",
            },
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        companies = client.get("/api/v1/companies", headers=headers)
        assert companies.status_code == 200
        assert companies.json()
        company_id = companies.json()[0]["id"]
        added = client.post(f"/api/v1/me/watchlist/{company_id}", headers=headers)
        assert added.status_code == 200, added.text
        removed = client.delete(f"/api/v1/me/watchlist/{company_id}", headers=headers)
        assert removed.status_code == 204, removed.text
        watchlist = client.get("/api/v1/me/watchlist", headers=headers)
        assert watchlist.status_code == 200
        assert company_id not in {item["company_id"] for item in watchlist.json()}
        logout = client.post("/api/v1/auth/logout", headers=headers)
        assert logout.status_code == 204

    engine = create_engine(POSTGRES_RLS_DATABASE_URL, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(ALPHA_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        event_types = set(
            connection.scalars(
                text("SELECT event_type FROM authentication_audit_logs WHERE user_id = :user_id"),
                {"user_id": str(ALPHA_USER_ID)},
            )
        )
        assert {"identity_linked", "session_started", "session_ended"} <= event_types
    engine.dispose()


def test_personal_request_api_keeps_rls_response_after_commit() -> None:
    assert POSTGRES_RLS_DATABASE_URL is not None
    settings = replace(
        Settings.from_env(),
        database_url=POSTGRES_RLS_DATABASE_URL,
        auth_provider="demo",
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
    )
    engine = create_engine(POSTGRES_RLS_DATABASE_URL, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(ALPHA_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        connection.execute(
            text(
                """
                INSERT INTO user_role_assignments (
                    id, user_id, role_id, scope_id, valid_until, created_at, updated_at
                )
                SELECT :id, :user_id, roles.id, NULL, NULL,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                FROM roles WHERE roles.code = 'platform_admin'
                ON CONFLICT DO NOTHING
                """
            ),
            {"id": str(uuid4()), "user_id": str(ALPHA_USER_ID)},
        )

    with TestClient(create_app(settings)) as client:
        owner_headers = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}
        created = client.post(
            "/api/v1/me/company-requests/inclusion",
            headers=owner_headers,
            json={"company_name": f"PostgreSQL RLS 申请响应测试 {uuid4().hex}"},
        )
        assert created.status_code == 200, created.text
        assert created.json()["status"] == "pending"

        reviewed = client.patch(
            f"/api/v1/platform/company-requests/{created.json()['id']}",
            headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
            json={"status": "completed", "reason": "验证提交后仍可安全返回响应"},
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["status"] == "completed"
        assert reviewed.json()["reviewed_by_id"] == str(ALPHA_USER_ID)
    engine.dispose()


def test_personal_changes_and_reports_rls_is_owner_only() -> None:
    assert POSTGRES_RLS_DATABASE_URL is not None
    engine = create_engine(POSTGRES_RLS_DATABASE_URL, pool_pre_ping=True)
    connection = engine.connect()
    transaction = connection.begin()
    company_id = str(demo_uuid("company-示例星河科技一号有限公司"))
    state_id = str(uuid4())
    receipt_id = str(uuid4())
    report_id = str(uuid4())
    try:
        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(ALPHA_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        event_id = connection.scalar(text("SELECT id FROM events ORDER BY id LIMIT 1"))
        assert event_id is not None

        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(NO_ACCESS_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        connection.execute(
            text(
                """
                INSERT INTO personal_company_view_states (
                    id, owner_user_id, company_id, last_viewed_at, created_at, updated_at
                ) VALUES (
                    :id, :owner_user_id, :company_id,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": state_id,
                "owner_user_id": str(NO_ACCESS_USER_ID),
                "company_id": company_id,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO personal_event_view_receipts (
                    id, owner_user_id, event_id, first_seen_at
                ) VALUES (
                    :id, :owner_user_id, :event_id, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": receipt_id,
                "owner_user_id": str(NO_ACCESS_USER_ID),
                "event_id": str(event_id),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO personal_company_reports (
                    id, owner_user_id, company_id, report_version, idempotency_key,
                    company_legal_name, title, as_of, markdown, content_hash,
                    source_event_ids, created_at
                ) VALUES (
                    :id, :owner_user_id, :company_id, 'personal-company-v1',
                    :idempotency_key, '示例星河科技一号有限公司', 'RLS report',
                    CURRENT_TIMESTAMP, '# RLS report',
                    :content_hash, CAST(:source_event_ids AS json), CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": report_id,
                "owner_user_id": str(NO_ACCESS_USER_ID),
                "company_id": company_id,
                "idempotency_key": "c" * 64,
                "content_hash": "d" * 64,
                "source_event_ids": f'["{event_id}"]',
            },
        )

        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(BETA_USER_ID), "tenant_id": str(BETA_TENANT_ID)},
        )
        assert connection.execute(
            text(
                "SELECT (SELECT count(*) FROM personal_company_view_states), "
                "(SELECT count(*) FROM personal_event_view_receipts), "
                "(SELECT count(*) FROM personal_company_reports)"
            )
        ).one() == (0, 0, 0)
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                connection.execute(
                    text(
                        """
                        INSERT INTO personal_company_reports (
                            id, owner_user_id, company_id, report_version,
                            idempotency_key, company_legal_name, title, as_of, markdown,
                            content_hash, source_event_ids, created_at
                        ) VALUES (
                            :id, :owner_user_id, :company_id, 'personal-company-v1',
                            :idempotency_key, '伪造公司', 'forged', CURRENT_TIMESTAMP, '# forged',
                            :content_hash, CAST('[]' AS json), CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "owner_user_id": str(NO_ACCESS_USER_ID),
                        "company_id": company_id,
                        "idempotency_key": "e" * 64,
                        "content_hash": "f" * 64,
                    },
                )

        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(ALPHA_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        assert connection.scalar(text("SELECT count(*) FROM personal_company_reports")) == 0

        connection.execute(
            text(
                "SELECT set_config('app.current_user_id', :user_id, true), "
                "set_config('app.current_tenant_id', :tenant_id, true)"
            ),
            {"user_id": str(NO_ACCESS_USER_ID), "tenant_id": str(ALPHA_TENANT_ID)},
        )
        assert connection.execute(
            text(
                "SELECT (SELECT count(*) FROM personal_company_view_states), "
                "(SELECT count(*) FROM personal_event_view_receipts), "
                "(SELECT count(*) FROM personal_company_reports)"
            )
        ).one() == (1, 1, 1)
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_personal_changes_and_reports_api_keeps_postgres_rls_context() -> None:
    assert POSTGRES_RLS_DATABASE_URL is not None
    base_settings = Settings.from_env()
    settings = replace(
        base_settings,
        database_url=POSTGRES_RLS_DATABASE_URL,
        auth_provider="demo",
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
        personal_entitlement_policy=replace(
            base_settings.personal_entitlement_policy,
            monthly_report_limit=1_000,
        ),
    )
    company_id = str(demo_uuid("company-示例星河科技一号有限公司"))
    with TestClient(create_app(settings)) as client:
        personal_headers = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}
        first_view = client.post(
            f"/api/v1/me/companies/{company_id}/view",
            headers=personal_headers,
        )
        second_view = client.post(
            f"/api/v1/me/companies/{company_id}/view",
            headers=personal_headers,
        )
        assert first_view.status_code == second_view.status_code == 200
        assert second_view.json()["first_view"] is False

        report_key = uuid4().hex * 2
        created = client.post(
            f"/api/v1/me/companies/{company_id}/reports",
            headers=personal_headers,
            json={"idempotency_key": report_key},
        )
        assert created.status_code == 200, created.text
        report_id = created.json()["id"]
        assert (
            client.get(f"/api/v1/me/reports/{report_id}", headers=personal_headers).status_code
            == 200
        )
        for other_user_id in (BETA_USER_ID, ALPHA_USER_ID):
            hidden = client.get(
                f"/api/v1/me/reports/{report_id}",
                headers={"X-Demo-User-Id": str(other_user_id)},
            )
            assert hidden.status_code == 404
