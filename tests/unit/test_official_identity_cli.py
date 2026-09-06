from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.import_official_identity_json as import_cli
from backend.app.providers import ManualOfficialIdentityImportProvider


@pytest.mark.parametrize(
    ("filename", "basis"),
    [
        ("official_identity_import.json", "official_government"),
        ("exchange_identity_import.json", "exchange_disclosure"),
    ],
)
def test_official_identity_cli_dry_run_never_connects_or_calls_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    filename: str,
    basis: str,
) -> None:
    sample = Path("data/sample") / filename
    path = tmp_path / "identity.json"
    path.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")
    provider = ManualOfficialIdentityImportProvider(path, allowed_root=tmp_path)
    monkeypatch.setenv("IDENTITY_IMPORT_DRY_RUN", "true")
    monkeypatch.setenv("IDENTITY_IMPORT_FILE", "identity.json")
    monkeypatch.delenv("IMPORT_USER_ID", raising=False)
    monkeypatch.setattr(import_cli, "ManualOfficialIdentityImportProvider", lambda _: provider)
    monkeypatch.setattr(
        import_cli,
        "build_engine",
        lambda _: pytest.fail("dry-run must not connect to the database"),
    )

    import_cli.main()
    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "dry_run"
    assert result["records_seen"] == 1
    assert result["verification_basis"] == basis
    assert result["external_calls"] == 0
    assert result["estimated_cost"] == "0"
    assert result["database_writes"] == 0
