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
    assert len(set(inspect(engine).get_table_names()) - {"alembic_version"}) == 18
    assert "heartbeat_at" in {
        column["name"] for column in inspect(engine).get_columns("refresh_jobs")
    }

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
