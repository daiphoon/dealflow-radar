from __future__ import annotations

import pytest

from scripts.check_production_config import check_production_config, main


def _set_valid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "SITE_ADDRESS": "app.dealflow.test",
        "APP_PUBLIC_ORIGIN": "https://app.dealflow.test",
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
        "WEB_RESEARCH_ENABLED",
        "WEB_RESEARCH_CALLS_ENABLED",
        "INVESTOR_ANALYSIS_ENABLED",
        "REVIEW_WORKBENCH_ENABLED",
        "TENDER_EVENTS_ENABLED",
        "WATCHLIST_MONITOR_ENABLED",
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
        "deployment_profile": "external_database",
        "backup_protection": "operator_managed",
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
    monkeypatch.setenv("APP_PUBLIC_ORIGIN", "http://localhost:3100")
    monkeypatch.setenv("ALLOW_INSECURE_LOCALHOST", "true")

    assert check_production_config()["site_scheme"] == "http"


def test_production_preflight_rejects_unapproved_localhost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv("SITE_ADDRESS", "localhost")

    with pytest.raises(RuntimeError, match="local test authorization"):
        check_production_config()


def test_production_preflight_rejects_public_origin_hostname_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv("APP_PUBLIC_ORIGIN", "https://internal.example.net")

    with pytest.raises(RuntimeError, match="must match SITE_ADDRESS"):
        check_production_config()


def test_production_preflight_rejects_public_origin_port_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv("APP_PUBLIC_ORIGIN", "https://app.dealflow.test:8443")

    with pytest.raises(RuntimeError, match="scheme and port"):
        check_production_config()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("AUTO_PUBLISH_ENABLED", "true", "AUTO_PUBLISH_ENABLED"),
        ("EXTERNAL_CALLS_ENABLED", "1", "EXTERNAL_CALLS_ENABLED"),
        ("INVESTOR_ANALYSIS_ENABLED", "true", "INVESTOR_ANALYSIS_ENABLED"),
        ("WEB_RESEARCH_ENABLED", "true", "WEB_RESEARCH_ENABLED"),
        ("WEB_RESEARCH_CALLS_ENABLED", "true", "WEB_RESEARCH_CALLS_ENABLED"),
        ("TENDER_EVENTS_ENABLED", "true", "TENDER_EVENTS_ENABLED"),
        ("WATCHLIST_MONITOR_ENABLED", "true", "WATCHLIST_MONITOR_ENABLED"),
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


def _set_single_host_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "single_host")
    monkeypatch.setenv("POSTGRES_DATABASE", "equity_radar")
    monkeypatch.setenv("POSTGRES_OWNER_USER", "migration_user")
    monkeypatch.setenv("POSTGRES_OWNER_PASSWORD", "admin-secret")
    monkeypatch.setenv(
        "DATABASE_ADMIN_URL",
        "postgresql+psycopg://migration_user:admin-secret@database/equity_radar",
    )
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://equity_app:app-secret@database/equity_radar"
    )
    monkeypatch.setenv(
        "BACKUP_DATABASE_URL",
        "postgresql://migration_user:admin-secret@database/equity_radar",
    )
    monkeypatch.setenv("BACKUP_REQUIRE_ENCRYPTION", "true")
    monkeypatch.setenv("BACKUP_AGE_RECIPIENT", "age1safeoperatorrecipient")
    monkeypatch.setenv("COS_BUCKET_ALIAS", "private-backups")
    monkeypatch.setenv("COS_BACKUP_PREFIX", "dealflow-radar/postgres")


def test_production_preflight_accepts_single_host_with_encrypted_backup_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_single_host_environment(monkeypatch)

    result = check_production_config()

    assert result["deployment_profile"] == "single_host"
    assert result["backup_protection"] == "client_encryption_required"


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        (
            "DATABASE_URL",
            "postgresql+psycopg://equity_app:app-secret@db/equity_radar",
            "internal database",
        ),
        ("BACKUP_REQUIRE_ENCRYPTION", "false", "encrypted backups"),
        ("BACKUP_AGE_RECIPIENT", "not-an-age-recipient", "age public recipient"),
        ("COS_BUCKET_ALIAS", "private/backups", "unsupported characters"),
        ("COS_BACKUP_PREFIX", "../postgres", "safe relative"),
    ],
)
def test_production_preflight_rejects_unsafe_single_host_configuration(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, message: str
) -> None:
    _set_single_host_environment(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=message):
        check_production_config()
