from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

import scripts.import_research_json as import_cli
from backend.app.config import PublicationPolicy, Settings
from backend.app.providers import ManualResearchImportProvider
from scripts.import_research_json import (
    _dry_run_enabled,
    _dry_run_result,
    _import_user_id,
    _required_env,
)


def test_manual_import_cli_requires_file_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEARCH_IMPORT_FILE", raising=False)

    with pytest.raises(RuntimeError, match="RESEARCH_IMPORT_FILE is required"):
        _required_env("RESEARCH_IMPORT_FILE")


def test_manual_import_cli_validates_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMPORT_USER_ID", "not-a-uuid")

    with pytest.raises(RuntimeError, match="IMPORT_USER_ID must be a UUID"):
        _import_user_id()


def test_manual_import_cli_accepts_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid4()
    monkeypatch.setenv("IMPORT_USER_ID", str(user_id))

    assert _import_user_id() == user_id


def test_manual_import_cli_dry_run_plans_checks_without_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manual_import_payload: dict[str, object],
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(manual_import_payload), encoding="utf-8")
    provider = ManualResearchImportProvider(path, allowed_root=tmp_path)
    settings = Settings(
        database_url="postgresql+psycopg://must-not-connect.invalid/database",
        app_mode="demo",
        external_calls_enabled=True,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
        publication_policy=PublicationPolicy(max_source_url_checks_per_import=1),
    )
    monkeypatch.setenv("RESEARCH_IMPORT_DRY_RUN", "true")
    monkeypatch.setenv("RESEARCH_IMPORT_FILE", "batch.json")
    monkeypatch.setenv("EXTERNAL_CALLS_ENABLED", "true")
    monkeypatch.setenv("SOURCE_URL_MAX_CHECKS_PER_IMPORT", "1")
    monkeypatch.delenv("IMPORT_USER_ID", raising=False)
    monkeypatch.setattr(import_cli, "ManualResearchImportProvider", lambda _: provider)
    monkeypatch.setattr(
        import_cli,
        "build_engine",
        lambda _: pytest.fail("dry-run must not connect to the database"),
    )

    result = _dry_run_result(provider, settings)
    import_cli.main()
    cli_result = json.loads(capsys.readouterr().out)

    assert _dry_run_enabled() is True
    assert result["status"] == "dry_run"
    assert result["records_seen"] == 1
    assert result["source_url_checks_upper_bound"] == 1
    assert result["verification_attempts_upper_bound"] == 2
    assert result["database_writes"] == 0
    assert cli_result["status"] == "dry_run"
    assert cli_result["source_url_checks_upper_bound"] == 1
