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
