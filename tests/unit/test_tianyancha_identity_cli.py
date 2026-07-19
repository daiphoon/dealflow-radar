from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.import_tianyancha_identities as import_cli
from backend.app.config import Settings


def _write_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "request_id": "cli-test-001",
                "queries": [
                    {
                        "legal_name": "示例星河科技一号有限公司",
                        "credit_code": "91310000MA1K000006",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_tianyancha_cli_dry_run_never_reads_secret_connects_or_calls_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write_manifest(tmp_path)
    monkeypatch.setenv("TIANYANCHA_IDENTITY_DRY_RUN", "true")
    monkeypatch.setenv("TIANYANCHA_IDENTITY_MANIFEST", str(path))
    monkeypatch.setattr(import_cli, "DEFAULT_PRIVATE_IDENTITY_IMPORT_ROOT", tmp_path)
    monkeypatch.delenv("TIANYANCHA_AUTHORIZATION", raising=False)
    monkeypatch.delenv("IMPORT_USER_ID", raising=False)
    monkeypatch.setattr(
        import_cli,
        "build_engine",
        lambda _: pytest.fail("dry-run must not connect to the database"),
    )
    monkeypatch.setattr(
        import_cli,
        "TianyanchaIdentityProvider",
        lambda *args, **kwargs: pytest.fail("dry-run must not create the network provider"),
    )

    import_cli.main()

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "status": "dry_run",
        "provider": "tianyancha_licensed_identity",
        "companies": 1,
        "planned_requests": 2,
        "queries": [
            {
                "legal_name": "示例星河科技一号有限公司",
                "credit_code": "91310000MA1K000006",
            }
        ],
        "external_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": "0",
        "database_writes": 0,
    }


@pytest.mark.parametrize(
    ("settings_overrides", "message"),
    [
        ({"external_calls_enabled": False}, "EXTERNAL_CALLS_ENABLED"),
        ({"tianyancha_identity_calls_enabled": False}, "TIANYANCHA_IDENTITY_CALLS_ENABLED"),
        ({"paid_api_calls_enabled": True}, "PAID_API_CALLS_ENABLED"),
        ({"auto_refresh_enabled": True}, "AUTO_REFRESH_ENABLED"),
    ],
)
def test_tianyancha_cli_real_run_requires_narrow_safe_switches(
    settings_overrides: dict[str, bool],
    message: str,
) -> None:
    values = {
        "database_url": "sqlite:///unused.db",
        "app_mode": "demo",
        "external_calls_enabled": True,
        "paid_api_calls_enabled": False,
        "auto_refresh_enabled": False,
        "trusted_source_calls_enabled": False,
        "tianyancha_identity_calls_enabled": True,
        "publication_policy": SimpleNamespace(enabled=False),
    }
    values.update(settings_overrides)
    settings = SimpleNamespace(**values)

    with pytest.raises(RuntimeError, match=message):
        import_cli._validate_real_run_settings(settings)


def test_tianyancha_cli_default_settings_keep_provider_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "EXTERNAL_CALLS_ENABLED",
        "PAID_API_CALLS_ENABLED",
        "AUTO_REFRESH_ENABLED",
        "AUTO_PUBLISH_ENABLED",
        "TRUSTED_SOURCE_CALLS_ENABLED",
        "TIANYANCHA_IDENTITY_CALLS_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.external_calls_enabled is False
    assert settings.paid_api_calls_enabled is False
    assert settings.auto_refresh_enabled is False
    assert settings.publication_policy.enabled is False
    assert settings.trusted_source_calls_enabled is False
    assert settings.tianyancha_identity_calls_enabled is False
