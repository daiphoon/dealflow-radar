from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[2]


def test_initial_migration_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(ROOT / "alembic.ini"))

    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(database_url)
    assert len(set(inspect(engine).get_table_names()) - {"alembic_version"}) == 39
    user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
    assert {"auth_provider", "auth_subject"} <= user_columns
    assert "uq_users_auth_identity" in {
        index["name"] for index in inspect(engine).get_indexes("users")
    }
    assert "authentication_audit_logs" in inspect(engine).get_table_names()
    assert {
        "tenant_id",
        "user_id",
        "provider",
        "subject_hash",
        "event_type",
        "outcome",
        "reason_code",
        "created_at",
    } <= {column["name"] for column in inspect(engine).get_columns("authentication_audit_logs")}
    assert "heartbeat_at" in {
        column["name"] for column in inspect(engine).get_columns("refresh_jobs")
    }
    assert "research_import_id" in {
        column["name"] for column in inspect(engine).get_columns("raw_documents")
    }
    assert "candidate_document_id" in {
        column["name"] for column in inspect(engine).get_columns("raw_documents")
    }
    review_columns = {
        column["name"]: column for column in inspect(engine).get_columns("review_queue")
    }
    assert review_columns["event_id"]["nullable"] is True
    assert review_columns["entity_mention_id"]["nullable"] is True
    assert "official_website" in {
        column["name"] for column in inspect(engine).get_columns("companies")
    }
    assert "last_identity_checked_at" in {
        column["name"] for column in inspect(engine).get_columns("companies")
    }
    event_columns = {column["name"] for column in inspect(engine).get_columns("events")}
    assert {
        "published_on",
        "publication_route",
        "publication_policy_version",
        "publication_reasons",
        "visibility_scope",
        "owner_user_id",
        "owner_tenant_id",
    } <= event_columns
    for table_name in (
        "company_aliases",
        "raw_documents",
        "entity_mentions",
        "event_evidence",
        "company_snapshots",
    ):
        assert {
            "visibility_scope",
            "owner_user_id",
            "owner_tenant_id",
        } <= {column["name"] for column in inspect(engine).get_columns(table_name)}
    event_indexes = {index["name"] for index in inspect(engine).get_indexes("events")}
    assert {
        "uq_event_fingerprint_platform_shared",
        "uq_event_fingerprint_personal_private",
        "uq_event_fingerprint_organization_private",
        "uq_event_fingerprint_system_restricted",
    } <= event_indexes
    alias_indexes = {index["name"] for index in inspect(engine).get_indexes("company_aliases")}
    assert {
        "uq_company_alias_platform_shared",
        "uq_company_alias_personal_private",
        "uq_company_alias_organization_private",
        "uq_company_alias_system_restricted",
    } <= alias_indexes
    document_indexes = {index["name"] for index in inspect(engine).get_indexes("raw_documents")}
    assert {
        "uq_raw_doc_source_record_platform_shared",
        "uq_raw_doc_source_record_personal_private",
        "uq_raw_doc_source_record_organization_private",
        "uq_raw_doc_source_record_system_restricted",
        "uq_raw_doc_dedupe_platform_shared",
        "uq_raw_doc_dedupe_personal_private",
        "uq_raw_doc_dedupe_organization_private",
        "uq_raw_doc_dedupe_system_restricted",
    } <= document_indexes
    snapshot_columns = {
        column["name"]: column for column in inspect(engine).get_columns("company_snapshots")
    }
    assert snapshot_columns["data_as_of"]["nullable"] is True
    identity_columns = {
        column["name"] for column in inspect(engine).get_columns("official_identity_verifications")
    }
    assert {
        "tenant_id",
        "company_id",
        "raw_document_id",
        "credit_code",
        "verification_status",
        "verification_basis",
        "checked_at",
    } <= identity_columns
    assert "identity_verification_basis" in {
        column["name"] for column in inspect(engine).get_columns("companies")
    }
    evidence_columns = {
        column["name"]: column for column in inspect(engine).get_columns("event_evidence")
    }
    assert evidence_columns["raw_document_id"]["nullable"] is True
    assert {
        "source_event_evidence_id",
        "display_source_name",
        "display_canonical_url",
        "display_url_health_status",
        "display_license_status",
        "display_allowed",
    } <= evidence_columns.keys()
    assert {
        "event_sharing_decisions",
        "event_sharing_decision_evidence",
    } <= set(inspect(engine).get_table_names())
    assert "ck_event_evidence_single_origin" in {
        constraint["name"] for constraint in inspect(engine).get_check_constraints("event_evidence")
    }
    assert "uq_event_sharing_source_outcome" in {
        index["name"] for index in inspect(engine).get_indexes("event_sharing_decisions")
    }
    assert {
        "trusted_sources",
        "source_check_runs",
        "candidate_documents",
    } <= set(inspect(engine).get_table_names())
    assert {
        "root_domain",
        "source_type",
        "access_basis",
        "content_retention_policy",
        "list_path_prefix",
        "last_etag",
        "last_modified",
    } <= {column["name"] for column in inspect(engine).get_columns("trusted_sources")}
    assert {
        "request_count",
        "downloaded_bytes",
        "request_log",
        "robots_status",
        "leased_until",
        "trigger_type",
        "scheduled_for",
    } <= {column["name"] for column in inspect(engine).get_columns("source_check_runs")}
    assert {
        "canonical_url",
        "content_hash",
        "change_type",
        "processing_status",
        "identity_status_at_discovery",
        "handoff_payload",
    } <= {column["name"] for column in inspect(engine).get_columns("candidate_documents")}
    assert "uq_source_check_run_active" in {
        index["name"] for index in inspect(engine).get_indexes("source_check_runs")
    }
    assert "uq_raw_document_candidate_handoff" in {
        index["name"] for index in inspect(engine).get_indexes("raw_documents")
    }
    assert {
        "company_research_jobs",
        "personal_quota_increase_requests",
    } <= set(inspect(engine).get_table_names())
    assert {
        "research_job_id",
        "resolved_legal_name",
        "resolved_credit_code",
        "identity_checked_at",
        "cancel_requested_at",
        "cancellation_reason",
        "external_calls",
        "cache_hits",
    } <= {column["name"] for column in inspect(engine).get_columns("personal_company_requests")}
    assert {"voided_at", "void_reason"} <= {
        column["name"] for column in inspect(engine).get_columns("personal_usage_records")
    }
    assert "display_detail_payload" in {
        column["name"] for column in inspect(engine).get_columns("event_evidence")
    }
    assert "uq_company_research_job_active" in {
        index["name"] for index in inspect(engine).get_indexes("company_research_jobs")
    }
    assert "investor_change_analyses" in inspect(engine).get_table_names()
    assert {
        "event_id",
        "visibility_scope",
        "status",
        "prompt_version",
        "schema_version",
        "input_hash",
        "evidence_ids",
        "analysis_output",
        "input_tokens",
        "output_tokens",
        "estimated_cost",
    } <= {column["name"] for column in inspect(engine).get_columns("investor_change_analyses")}
    assert "ix_investor_change_analysis_status_created" in {
        index["name"] for index in inspect(engine).get_indexes("investor_change_analyses")
    }
    assert "web_search_cache_entries" in inspect(engine).get_table_names()
    assert {
        "company_id",
        "provider_code",
        "query_kind",
        "query_hash",
        "identity_fingerprint",
        "response_hash",
        "results",
        "fetched_at",
        "expires_at",
    } <= {column["name"] for column in inspect(engine).get_columns("web_search_cache_entries")}
    assert "uq_web_search_cache_identity_query" in {
        constraint["name"]
        for constraint in inspect(engine).get_unique_constraints("web_search_cache_entries")
    }
    assert {"event_facts", "event_fact_supports"} <= set(inspect(engine).get_table_names())
    assert {
        "event_id",
        "fact_key",
        "name",
        "value",
        "unit",
        "position",
        "occurrence_count",
    } <= {column["name"] for column in inspect(engine).get_columns("event_facts")}
    assert {
        "event_id",
        "event_fact_id",
        "event_evidence_id",
        "support_status",
        "evidence_locator",
        "deterministic_checks",
        "support_reasons",
        "policy_version",
        "assessed_at",
    } <= {column["name"] for column in inspect(engine).get_columns("event_fact_supports")}
    assert "uq_event_evidence_id_event" in {
        constraint["name"]
        for constraint in inspect(engine).get_unique_constraints("event_evidence")
    }
    assert "ck_event_fact_support_status" in {
        constraint["name"]
        for constraint in inspect(engine).get_check_constraints("event_fact_supports")
    }

    command.downgrade(config, "0007")
    assert "visibility_scope" not in {
        column["name"] for column in inspect(engine).get_columns("events")
    }
    command.upgrade(config, "head")
    command.check(config)
    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()


def test_postgresql_migration_compiles_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://demo_user:replace_me@localhost:5432/equity_radar",
    )
    output = StringIO()
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)

    command.upgrade(config, "head", sql=True)
    ddl = output.getvalue()

    assert "CREATE TABLE events" in ddl
    assert "CREATE TABLE event_evidence" in ddl
    assert "CREATE UNIQUE INDEX uq_company_snapshot_current" in ddl
    assert "WHERE is_current" in ddl
    assert "ALTER TABLE investments ENABLE ROW LEVEL SECURITY" in ddl
    assert "CREATE POLICY investments_authorized" in ddl
    assert "CREATE POLICY review_queue_reviewer" in ddl
    assert (
        "CREATE UNIQUE INDEX uq_refresh_job_active "
        "ON refresh_jobs (tenant_id, company_id, job_type)" in ddl
    )
    assert "ADD COLUMN heartbeat_at TIMESTAMP WITH TIME ZONE" in ddl
    assert "CREATE TABLE research_imports" in ddl
    assert "CREATE POLICY research_imports_admin" in ddl
    assert "ADD COLUMN entity_mention_id UUID" in ddl
    assert "ck_review_queue_subject" in ddl
    assert "ADD COLUMN official_website" in ddl
    assert "ADD COLUMN publication_route" in ddl
    assert "ALTER COLUMN data_as_of DROP NOT NULL" in ddl
    assert "ADD COLUMN last_identity_checked_at" in ddl
    assert "CREATE TABLE official_identity_verifications" in ddl
    assert "official_identity_verifications_read" in ddl
    assert "official_identity_verifications_insert" in ddl
    assert "ADD COLUMN visibility_scope" in ddl
    assert "CREATE POLICY events_scope_read" in ddl
    assert "CREATE POLICY raw_documents_scope_read" in ddl
    assert "CREATE POLICY companies_scope_read" in ddl
    assert "CREATE TABLE event_sharing_decisions" in ddl
    assert "CREATE TABLE event_sharing_decision_evidence" in ddl
    assert "event_sharing_decisions_platform_admin_insert" in ddl
    assert "ck_event_evidence_single_origin" in ddl
    assert "uq_event_sharing_source_outcome" in ddl
    assert "events_platform_admin_read" in ddl
    assert "roles.code IN ('platform_admin')" in ddl
    assert "CREATE TABLE trusted_sources" in ddl
    assert "CREATE TABLE source_check_runs" in ddl
    assert "CREATE TABLE candidate_documents" in ddl
    assert "trusted_sources_platform_admin_read" in ddl
    assert "source_check_runs_platform_admin_insert" in ddl
    assert "candidate_documents_platform_admin_update" in ddl
    assert "ADD COLUMN identity_verification_basis" in ddl
    assert "ADD COLUMN verification_basis" in ddl
    assert "ck_company_identity_verification_basis" in ddl
    assert "ck_official_identity_verification_basis" in ddl
    assert "ADD COLUMN trigger_type" in ddl
    assert "ADD COLUMN scheduled_for" in ddl
    assert "ADD COLUMN candidate_document_id" in ddl
    assert "uq_raw_document_candidate_handoff" in ddl
    assert "ADD COLUMN auth_provider" in ddl
    assert "CREATE UNIQUE INDEX uq_users_auth_identity" in ddl
    assert "CREATE TABLE authentication_audit_logs" in ddl
    assert "ALTER TABLE authentication_audit_logs ENABLE ROW LEVEL SECURITY" in ddl
    assert "CREATE POLICY authentication_audit_logs_insert" in ddl
    assert "CREATE POLICY authentication_audit_logs_read" in ddl
    assert "CREATE TABLE personal_watchlist_items" in ddl
    assert "CREATE TABLE personal_company_requests" in ddl
    assert "CREATE TABLE personal_usage_records" in ddl
    assert "personal_watchlist_items_owner_read" in ddl
    assert "personal_company_requests_platform_admin_update" in ddl
    assert "personal_usage_records_owner_insert" in ddl
    assert "CREATE TABLE personal_company_view_states" in ddl
    assert "CREATE TABLE personal_event_view_receipts" in ddl
    assert "CREATE TABLE personal_company_reports" in ddl
    assert "personal_company_view_states_owner_update" in ddl
    assert "personal_event_view_receipts_owner_insert" in ddl
    assert "personal_company_reports_owner_read" in ddl
    assert "CREATE TABLE company_research_jobs" in ddl
    assert "CREATE TABLE personal_quota_increase_requests" in ddl
    assert "company_research_jobs_requester_read" in ddl
    assert "personal_quota_increase_owner_read" in ddl
    assert "usage_ledger_tianyancha_platform_admin_read" in ddl
    assert "personal_usage_records_on_demand_admin_update" in ddl
    assert "request_type = 'refresh' AND company_id IS NOT NULL" in ddl
    assert "visible_shared_company.identity_status = 'verified'" in ddl
    assert "ADD COLUMN display_detail_payload" in ddl
    assert "CREATE TABLE investor_change_analyses" in ddl
    assert "investor_change_analyses_read" in ddl
    assert "investor_change_analyses_platform_admin_read" in ddl
    assert "investor_change_analyses_insert" in ddl
    assert "investor_change_analyses_update" in ddl
    assert "DROP POLICY IF EXISTS usage_ledger_tianyancha_platform_admin_read" in ddl
    assert "DROP POLICY IF EXISTS raw_documents_tianyancha_admin_read" in ddl
    assert "DROP POLICY IF EXISTS official_identity_verifications_platform_admin_read" in ddl
    assert "CREATE TABLE web_search_cache_entries" in ddl
    assert "web_search_cache_platform_admin_read" in ddl
    assert "raw_documents_bounded_web_admin_read" in ddl
    assert "entity_mentions_bounded_web_admin_insert" in ddl
    assert "CREATE TABLE event_facts" in ddl
    assert "CREATE TABLE event_fact_supports" in ddl
    assert "uq_event_evidence_id_event" in ddl
    assert "event_facts_scope_read" in ddl
    assert "event_fact_supports_scope_insert" in ddl
    assert "research-candidate-analysis-v1" in ddl
    assert "bounded-web-quality-v4" in ddl
    assert "DROP POLICY IF EXISTS investor_change_analyses_read" in ddl


def test_fact_support_migration_backfills_historical_rows_conservatively(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'fact-ledger-backfill.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "0023")
    engine = create_engine(database_url)
    company_id = "11111111111141118111111111111111"
    source_id = "22222222222242228222222222222222"
    document_id = "33333333333343338333333333333333"
    event_id = "44444444444444448444444444444444"
    evidence_id = "55555555555545558555555555555555"
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO companies (
                    id, tenant_id, credit_code, legal_name, registered_region,
                    official_website, identity_status, identity_verification_basis,
                    last_identity_checked_at, visibility_scope, created_at, updated_at
                ) VALUES (
                    :id, NULL, NULL, '历史迁移测试公司', NULL,
                    NULL, 'verified', 'official_government', NULL, 'public',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {"id": company_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO sources (
                    id, code, name, source_quality, license_status, base_url,
                    created_at, updated_at
                ) VALUES (
                    :id, 'fact-ledger-history', '历史迁移测试来源', 'A', 'public', NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {"id": source_id},
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
                    :id, :source_id, NULL, NULL,
                    NULL, NULL, 'platform_shared',
                    'fact-ledger-history', 'https://example.invalid/history',
                    '历史迁移测试文档', NULL, NULL, CURRENT_TIMESTAMP,
                    :content_hash, :dedupe_key, 'public', CAST('{}' AS JSON),
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": document_id,
                "source_id": source_id,
                "content_hash": "1" * 64,
                "dedupe_key": "2" * 64,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO events (
                    id, company_id, owner_user_id, owner_tenant_id, visibility_scope,
                    event_type, event_subtype, status, direction, materiality_score,
                    risk_severity, confidence_score, source_quality, title, summary,
                    facts, uncertainties, occurred_at, published_at, published_on,
                    observed_at, fingerprint_version, event_fingerprint,
                    publication_route, publication_policy_version, publication_reasons,
                    created_at, updated_at
                ) VALUES (
                    :id, :company_id, NULL, NULL, 'platform_shared',
                    'information_quality', 'historical_backfill', 'published', 'neutral', 30,
                    'low', 0.900, 'A', '历史事实', '迁移不得自动认定逐条支持',
                    :facts, CAST('[]' AS JSON), NULL, NULL, NULL,
                    CURRENT_TIMESTAMP, 'history-v1', :fingerprint,
                    'human_promoted', 'history-v1', CAST('[]' AS JSON),
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": event_id,
                "company_id": company_id,
                "facts": '[{"name":"注册资本","value":"1000万元","unit":null}]',
                "fingerprint": "3" * 64,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO event_evidence (
                    id, event_id, raw_document_id, source_event_evidence_id,
                    owner_user_id, owner_tenant_id, visibility_scope,
                    evidence_excerpt, span_hash, support_type, display_allowed,
                    created_at, updated_at
                ) VALUES (
                    :id, :event_id, :document_id, NULL,
                    NULL, NULL, 'platform_shared',
                    '历史证据摘录', :span_hash, 'supports', FALSE,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": evidence_id,
                "event_id": event_id,
                "document_id": document_id,
                "span_hash": "4" * 64,
            },
        )

    command.upgrade(config, "0024")
    with engine.connect() as connection:
        fact = connection.execute(
            text(
                "SELECT name, value, occurrence_count FROM event_facts WHERE event_id = :event_id"
            ),
            {"event_id": event_id},
        ).one()
        support = connection.execute(
            text(
                "SELECT support_status, support_reasons FROM event_fact_supports "
                "WHERE event_id = :event_id"
            ),
            {"event_id": event_id},
        ).one()
        assert fact == ("注册资本", "1000万元", 1)
        assert support[0] == "pending_review"
        assert "historical_relationship_not_proven_per_fact" in support[1]

    command.downgrade(config, "0023")
    assert "event_facts" not in inspect(engine).get_table_names()
    assert (
        engine.connect().scalar(
            text("SELECT count(*) FROM events WHERE id = :event_id"),
            {"event_id": event_id},
        )
        == 1
    )
    engine.dispose()
