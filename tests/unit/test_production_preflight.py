from __future__ import annotations

import pytest

from scripts.check_production_config import check_production_config, main


def _set_valid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "SITE_ADDRESS": "app.dealflow.test",
        "ALLOW_INSECURE_LOCALHOST": "false",
        "APP_MODE": "production",
        "AUTH_PROVIDER": "cloudbase",
        "CLOUDBASE_ENV_ID": "env-production-123",
        "DATABASE_ADMIN_URL": (
            "postgresql+psycopg://migration_user:admin-secret@database.internal/equity_radar"
        ),
        "DATABASE_URL": (
            "postgresql+psycopg://equity_app:app-secret@database.internal/equity_radar"
        ),
        "BACKUP_DATABASE_URL": (
            "postgresql://migration_user:admin-secret@database.internal/equity_radar"
        ),
        "APP_DATABASE_USER": "equity_app",
        "APP_DATABASE_PASSWORD": "app-secret",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    for name in (
        "EXTERNAL_CALLS_ENABLED",
        "PAID_API_CALLS_ENABLED",
        "AUTO_REFRESH_ENABLED",
        "AUTO_PUBLISH_ENABLED",
        "TRUSTED_SOURCE_CALLS_ENABLED",
        "SOURCE_MONITOR_SCHEDULER_ENABLED",
        "TIANYANCHA_IDENTITY_CALLS_ENABLED",
        "REVIEW_WORKBENCH_ENABLED",
    ):
        monkeypatch.setenv(name, "false")


def test_production_preflight_accepts_safe_initial_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_valid_environment(monkeypatch)

    result = check_production_config()
    main()
    output = capsys.readouterr().out

    assert result == {
        "status": "ok",
        "app_mode": "production",
        "auth_provider": "cloudbase",
        "database": "postgresql",
        "site_scheme": "https",
        "initial_safety_switches": "closed",
    }
    assert "admin-secret" not in output
    assert "app-secret" not in output


def test_production_preflight_allows_explicit_local_http_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv("SITE_ADDRESS", "http://localhost")
    monkeypatch.setenv("ALLOW_INSECURE_LOCALHOST", "true")

    assert check_production_config()["site_scheme"] == "http"


def test_production_preflight_rejects_unapproved_localhost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv("SITE_ADDRESS", "localhost")

    with pytest.raises(RuntimeError, match="local test authorization"):
        check_production_config()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("AUTO_PUBLISH_ENABLED", "true", "AUTO_PUBLISH_ENABLED"),
        ("EXTERNAL_CALLS_ENABLED", "1", "EXTERNAL_CALLS_ENABLED"),
        ("SITE_ADDRESS", "http://app.dealflow.test", "HTTPS"),
        ("CLOUDBASE_ENV_ID", "replace-with-env", "placeholder"),
    ],
)
def test_production_preflight_fails_closed(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, message: str
) -> None:
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises((RuntimeError, ValueError), match=message):
        check_production_config()


def test_production_preflight_separates_database_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://migration_user:admin-secret@database.internal/equity_radar",
    )
    monkeypatch.setenv("APP_DATABASE_USER", "migration_user")
    monkeypatch.setenv("APP_DATABASE_PASSWORD", "admin-secret")

    with pytest.raises(RuntimeError, match="users must differ"):
        check_production_config()
