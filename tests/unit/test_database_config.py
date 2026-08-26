from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from backend.app.config import CloudBaseAuthPolicy, PublicationPolicy, Settings
from scripts.bootstrap_local_database import bootstrap_application_role


def test_default_database_url_uses_restricted_application_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert make_url(Settings.from_env().database_url).username == "equity_app"


def test_refresh_policy_is_configured_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFRESH_POLICY_VERSION", "validation-v2")
    monkeypatch.setenv("RECENT_QUERY_TTL_DAYS", "9")
    monkeypatch.setenv("REFRESH_REQUEST_COOLDOWN_HOURS", "6")
    monkeypatch.setenv("MOCK_WORKER_LEASE_SECONDS", "45")

    policy = Settings.from_env().refresh_policy

    assert policy.version == "validation-v2"
    assert policy.recent_query_ttl_days == 9
    assert policy.request_cooldown_hours == 6
    assert policy.mock_worker_lease_seconds == 45


def test_refresh_policy_rejects_non_positive_intervals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECENT_QUERY_TTL_DAYS", "0")

    with pytest.raises(ValueError, match="RECENT_QUERY_TTL_DAYS"):
        Settings.from_env()


def test_personal_test_entitlements_are_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONAL_MONTHLY_SEARCH_LIMIT", "101")
    monkeypatch.setenv("PERSONAL_WATCHLIST_COMPANY_LIMIT", "21")
    monkeypatch.setenv("PERSONAL_MONTHLY_REPORT_LIMIT", "11")
    monkeypatch.setenv("PERSONAL_DAILY_REQUEST_LIMIT", "7")
    monkeypatch.setenv("PERSONAL_MONTHLY_REQUEST_LIMIT", "6")
    monkeypatch.setenv("PERSONAL_REQUEST_COOLDOWN_HOURS", "25")

    policy = Settings.from_env().personal_entitlement_policy

    assert policy.monthly_search_limit == 101
    assert policy.watchlist_company_limit == 21
    assert policy.monthly_report_limit == 11
    assert policy.daily_request_limit == 7
    assert policy.monthly_request_limit == 6
    assert policy.request_cooldown_hours == 25


def test_review_workbench_requires_explicit_enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REVIEW_WORKBENCH_ENABLED", raising=False)
    assert Settings.from_env().review_workbench_enabled is False

    monkeypatch.setenv("REVIEW_WORKBENCH_ENABLED", "true")
    assert Settings.from_env().review_workbench_enabled is True


def test_authentication_defaults_to_demo_and_cloudbase_requires_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTH_PROVIDER", raising=False)
    monkeypatch.delenv("CLOUDBASE_ENV_ID", raising=False)
    assert Settings.from_env().auth_provider == "demo"

    monkeypatch.setenv("AUTH_PROVIDER", "cloudbase")
    with pytest.raises(ValueError, match="CLOUDBASE_ENV_ID"):
        Settings.from_env()


def test_production_mode_requires_cloudbase_and_postgresql() -> None:
    with pytest.raises(ValueError, match="AUTH_PROVIDER=cloudbase"):
        Settings(
            database_url="postgresql+psycopg://app:secret@db/example",
            app_mode="production",
            external_calls_enabled=False,
            paid_api_calls_enabled=False,
            auto_refresh_enabled=False,
        )

    with pytest.raises(ValueError, match="PostgreSQL DATABASE_URL"):
        Settings(
            database_url="sqlite:///production.db",
            app_mode="production",
            external_calls_enabled=False,
            paid_api_calls_enabled=False,
            auto_refresh_enabled=False,
            auth_provider="cloudbase",
            cloudbase_auth_policy=CloudBaseAuthPolicy(env_id="env-production"),
        )

    settings = Settings(
        database_url="postgresql+psycopg://app:secret@db/example",
        app_mode="production",
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
        auth_provider="cloudbase",
        cloudbase_auth_policy=CloudBaseAuthPolicy(env_id="env-production"),
    )

    assert settings.app_mode == "production"


def test_cloudbase_authentication_configuration_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "cloudbase")
    monkeypatch.setenv("CLOUDBASE_ENV_ID", "env-demo-123")
    monkeypatch.setenv("CLOUDBASE_CLIENT_ID", "client-demo-456")
    monkeypatch.setenv("CLOUDBASE_AUTH_TIMEOUT_SECONDS", "4")
    monkeypatch.setenv("CLOUDBASE_AUTH_MAX_RESPONSE_BYTES", "32000")

    settings = Settings.from_env()

    assert settings.auth_provider == "cloudbase"
    assert settings.cloudbase_auth_policy.env_id == "env-demo-123"
    assert settings.cloudbase_auth_policy.client_id == "client-demo-456"
    assert settings.cloudbase_auth_policy.timeout_seconds == 4
    assert settings.cloudbase_auth_policy.max_response_bytes == 32_000


def test_phone_authentication_is_opt_in_and_rate_limits_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "cloudbase")
    monkeypatch.setenv("CLOUDBASE_ENV_ID", "env-demo-123")
    assert Settings.from_env().cloudbase_auth_policy.phone_login_enabled is False

    monkeypatch.setenv("PHONE_LOGIN_ENABLED", "true")
    monkeypatch.setenv("AUTH_PHONE_CODE_COOLDOWN_SECONDS", "90")
    monkeypatch.setenv("AUTH_PHONE_DAILY_LIMIT", "4")
    monkeypatch.setenv("AUTH_PHONE_ENV_DAILY_LIMIT", "20")
    policy = Settings.from_env().cloudbase_auth_policy

    assert policy.phone_login_enabled is True
    assert policy.phone_code_cooldown_seconds == 90
    assert policy.phone_daily_limit == 4
    assert policy.phone_environment_daily_limit == 20


def test_phone_authentication_rejects_an_environment_limit_below_phone_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "cloudbase")
    monkeypatch.setenv("CLOUDBASE_ENV_ID", "env-demo-123")
    monkeypatch.setenv("AUTH_PHONE_DAILY_LIMIT", "5")
    monkeypatch.setenv("AUTH_PHONE_ENV_DAILY_LIMIT", "4")

    with pytest.raises(ValueError, match="below per-phone"):
        Settings.from_env()


def test_cloudbase_authentication_rejects_unsafe_environment_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "cloudbase")
    monkeypatch.setenv("CLOUDBASE_ENV_ID", "https://attacker.invalid")

    with pytest.raises(ValueError, match="CLOUDBASE_ENV_ID"):
        Settings.from_env()


def test_publication_policy_is_configured_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLICATION_POLICY_VERSION", "identity-first-v2")
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_MIN_CONFIDENCE", "0.91")
    monkeypatch.setenv("SOURCE_URL_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("SOURCE_URL_MAX_CHECKS_PER_IMPORT", "12")

    policy = Settings.from_env().publication_policy

    assert policy.version == "identity-first-v2"
    assert policy.enabled is False
    assert str(policy.min_confidence) == "0.91"
    assert policy.source_url_timeout_seconds == 7
    assert policy.max_source_url_checks_per_import == 12


def test_auto_publish_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTO_PUBLISH_ENABLED", raising=False)

    assert Settings.from_env().publication_policy.enabled is False
    assert PublicationPolicy().enabled is False


def test_trusted_source_monitoring_is_disabled_and_bounded_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRUSTED_SOURCE_CALLS_ENABLED", raising=False)
    monkeypatch.delenv("SOURCE_MONITOR_SCHEDULER_ENABLED", raising=False)

    settings = Settings.from_env()

    assert settings.trusted_source_calls_enabled is False
    assert settings.source_monitor_scheduler_enabled is False
    assert settings.source_monitoring_policy.max_requests_per_run == 10
    assert settings.source_monitoring_policy.max_response_bytes == 1_000_000
    assert settings.source_monitoring_policy.worker_lease_seconds == 300
    assert settings.source_monitoring_policy.scheduler_max_sources_per_run == 10
    assert settings.source_monitoring_policy.failure_backoff_max_multiplier == 8


def test_source_monitor_scheduler_requires_explicit_enable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOURCE_MONITOR_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("SOURCE_MONITOR_SCHEDULER_MAX_SOURCES", "4")
    monkeypatch.setenv("SOURCE_MONITOR_FAILURE_BACKOFF_MAX_MULTIPLIER", "4")

    settings = Settings.from_env()

    assert settings.source_monitor_scheduler_enabled is True
    assert settings.source_monitoring_policy.scheduler_max_sources_per_run == 4
    assert settings.source_monitoring_policy.failure_backoff_max_multiplier == 4


def test_source_monitoring_policy_rejects_invalid_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOURCE_MONITOR_RETRY_LIMIT", "-1")

    with pytest.raises(ValueError, match="SOURCE_MONITOR_RETRY_LIMIT"):
        Settings.from_env()


def test_publication_policy_rejects_invalid_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_PUBLISH_MIN_CONFIDENCE", "1.1")

    with pytest.raises(ValueError, match="AUTO_PUBLISH_MIN_CONFIDENCE"):
        Settings.from_env()


def test_identity_policy_is_configured_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IDENTITY_POLICY_VERSION", "official-identity-v2")
    monkeypatch.setenv("IDENTITY_VERIFICATION_TTL_DAYS", "21")

    policy = Settings.from_env().identity_policy

    assert policy.version == "official-identity-v2"
    assert policy.verification_ttl_days == 21


def test_identity_policy_rejects_non_positive_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IDENTITY_VERIFICATION_TTL_DAYS", "0")

    with pytest.raises(ValueError, match="IDENTITY_VERIFICATION_TTL_DAYS"):
        Settings.from_env()


def test_bootstrap_rejects_shared_database_password() -> None:
    with pytest.raises(RuntimeError, match="passwords must differ"):
        bootstrap_application_role(
            "postgresql+psycopg://demo_user:same_password@localhost/equity_radar",
            "equity_app",
            "same_password",
        )


def test_bootstrap_rejects_shared_database_user() -> None:
    with pytest.raises(RuntimeError, match="users must differ"):
        bootstrap_application_role(
            "postgresql+psycopg://demo_user:owner_password@localhost/equity_radar",
            "demo_user",
            "app_password",
        )


def test_bootstrap_rejects_unsafe_role_name_before_connecting() -> None:
    with pytest.raises(RuntimeError, match="lowercase PostgreSQL role name"):
        bootstrap_application_role(
            "postgresql+psycopg://demo_user:owner_password@localhost/equity_radar",
            "Equity-App",
            "app_password",
        )
