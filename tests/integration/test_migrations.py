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
    assert len(set(inspect(engine).get_table_names()) - {"alembic_version"}) == 19
    assert "heartbeat_at" in {
        column["name"] for column in inspect(engine).get_columns("refresh_jobs")
    }
    assert "research_import_id" in {
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
    event_columns = {column["name"] for column in inspect(engine).get_columns("events")}
    assert {
        "published_on",
        "publication_route",
        "publication_policy_version",
        "publication_reasons",
    } <= event_columns
    snapshot_columns = {
        column["name"]: column for column in inspect(engine).get_columns("company_snapshots")
    }
    assert snapshot_columns["data_as_of"]["nullable"] is True

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
