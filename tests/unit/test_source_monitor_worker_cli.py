from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from backend.app.config import PublicationPolicy, Settings
from scripts.run_source_monitor_worker import (
    _require_explicit_demo_mode,
    _required_uuid,
    _validate_source_worker_safety,
)


@pytest.mark.parametrize("app_mode", [None, "", "production", " Demo "])
def test_source_worker_requires_explicit_demo_mode(
    monkeypatch: pytest.MonkeyPatch,
    app_mode: str | None,
) -> None:
    if app_mode is None:
        monkeypatch.delenv("APP_MODE", raising=False)
    else:
        monkeypatch.setenv("APP_MODE", app_mode)

    with pytest.raises(RuntimeError, match="explicitly set to demo"):
        _require_explicit_demo_mode()


def test_source_worker_requires_valid_worker_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_MONITOR_WORKER_USER_ID", "not-a-uuid")
    with pytest.raises(RuntimeError, match="must be a UUID"):
        _required_uuid("SOURCE_MONITOR_WORKER_USER_ID")

    user_id = uuid4()
    monkeypatch.setenv("SOURCE_MONITOR_WORKER_USER_ID", str(user_id))
    assert _required_uuid("SOURCE_MONITOR_WORKER_USER_ID") == user_id


@pytest.mark.parametrize("unsafe_switch", ["paid", "refresh", "publish"])
def test_source_worker_rejects_unrelated_dangerous_switches(unsafe_switch: str) -> None:
    settings = Settings(
        database_url="sqlite://",
        app_mode="demo",
        external_calls_enabled=False,
        paid_api_calls_enabled=unsafe_switch == "paid",
        auto_refresh_enabled=unsafe_switch == "refresh",
        publication_policy=PublicationPolicy(enabled=unsafe_switch == "publish"),
    )

    with pytest.raises(RuntimeError, match="must be false"):
        _validate_source_worker_safety(settings)

    safe_settings = replace(
        settings,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
        publication_policy=PublicationPolicy(enabled=False),
    )
    _validate_source_worker_safety(safe_settings)
