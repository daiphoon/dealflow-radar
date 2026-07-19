from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from backend.app.config import PublicationPolicy, Settings
from scripts.queue_due_source_checks import (
    _dry_run_enabled as scheduler_dry_run_enabled,
)
from scripts.queue_due_source_checks import (
    _validate_scheduler_safety,
)
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


@pytest.mark.parametrize("unsafe_switch", ["paid", "publish"])
def test_source_worker_rejects_unrelated_dangerous_switches(unsafe_switch: str) -> None:
    settings = Settings(
        database_url="sqlite://",
        app_mode="demo",
        external_calls_enabled=False,
        paid_api_calls_enabled=unsafe_switch == "paid",
        auto_refresh_enabled=True,
        publication_policy=PublicationPolicy(enabled=unsafe_switch == "publish"),
    )

    with pytest.raises(RuntimeError, match="must be false"):
        _validate_source_worker_safety(settings)

    safe_settings = replace(
        settings,
        paid_api_calls_enabled=False,
        publication_policy=PublicationPolicy(enabled=False),
    )
    _validate_source_worker_safety(safe_settings)


def test_source_scheduler_defaults_to_read_only_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SOURCE_MONITOR_SCHEDULER_DRY_RUN", raising=False)
    assert scheduler_dry_run_enabled() is True

    settings = Settings(
        database_url="sqlite://",
        app_mode="demo",
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
    )
    _validate_scheduler_safety(settings, dry_run=True)


def test_source_scheduler_actual_queue_requires_all_explicit_switches() -> None:
    settings = Settings(
        database_url="sqlite://",
        app_mode="demo",
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
    )
    with pytest.raises(RuntimeError, match="actual scheduling requires"):
        _validate_scheduler_safety(settings, dry_run=False)

    enabled = replace(
        settings,
        external_calls_enabled=True,
        auto_refresh_enabled=True,
        trusted_source_calls_enabled=True,
        source_monitor_scheduler_enabled=True,
    )
    _validate_scheduler_safety(enabled, dry_run=False)
