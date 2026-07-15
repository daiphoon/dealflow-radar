from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from backend.app.config import Settings
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


def test_review_workbench_requires_explicit_enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REVIEW_WORKBENCH_ENABLED", raising=False)
    assert Settings.from_env().review_workbench_enabled is False

    monkeypatch.setenv("REVIEW_WORKBENCH_ENABLED", "true")
    assert Settings.from_env().review_workbench_enabled is True


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
