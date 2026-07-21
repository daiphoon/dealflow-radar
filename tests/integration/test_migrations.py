from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[2]


def test_initial_migration_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(ROOT / "alembic.ini"))

    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(database_url)
    assert len(set(inspect(engine).get_table_names()) - {"alembic_version"}) == 32
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
