from __future__ import annotations

import json
import sys
from dataclasses import replace
from uuid import uuid4

import pytest

import scripts.run_on_demand_research_worker as worker_cli
from backend.app.config import OnDemandResearchPolicy, PublicationPolicy, Settings


def test_dry_run_does_not_read_provider_secret_or_create_provider(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    worker_user_id = uuid4()
    worker_tenant_id = uuid4()
    settings = Settings(
        database_url="sqlite:///unused.db",
        app_mode="demo",
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
    )
    monkeypatch.setenv("ON_DEMAND_WORKER_USER_ID", str(worker_user_id))
    monkeypatch.setenv("WORKER_TENANT_ID", str(worker_tenant_id))
    monkeypatch.delenv("TIANYANCHA_AUTHORIZATION", raising=False)
    monkeypatch.delenv("TIANYANCHA_AUTHORIZATION_FILE", raising=False)
    monkeypatch.setattr(sys, "argv", ["run_on_demand_research_worker", "--dry-run"])
    monkeypatch.setattr(worker_cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(
        worker_cli,
        "_dry_run",
        lambda *_args, **_kwargs: {
            "status": "dry_run",
            "external_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost": "0",
        },
    )
    monkeypatch.setattr(
        worker_cli,
        "TianyanchaIdentityProvider",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not create the external provider"),
    )

    worker_cli.main()

    assert json.loads(capsys.readouterr().out) == {
        "status": "dry_run",
        "external_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": "0",
    }


def test_healthcheck_does_not_read_provider_secret_or_create_provider(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    worker_user_id = uuid4()
    worker_tenant_id = uuid4()
    settings = Settings(
        database_url="sqlite:///unused.db",
        app_mode="demo",
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
    )
    monkeypatch.setenv("ON_DEMAND_WORKER_USER_ID", str(worker_user_id))
    monkeypatch.setenv("WORKER_TENANT_ID", str(worker_tenant_id))
    monkeypatch.setenv("TIANYANCHA_AUTHORIZATION_FILE", "/missing/secret")
    monkeypatch.setattr(sys, "argv", ["run_on_demand_research_worker", "--healthcheck"])
    monkeypatch.setattr(worker_cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(
        worker_cli,
        "_healthcheck",
        lambda *_args, **_kwargs: {"status": "healthy", "external_calls": 0},
    )
    monkeypatch.setattr(
        worker_cli,
        "TianyanchaIdentityProvider",
        lambda *_args, **_kwargs: pytest.fail("healthcheck must not create the provider"),
    )

    worker_cli.main()

    assert json.loads(capsys.readouterr().out) == {
        "status": "healthy",
        "external_calls": 0,
    }


def test_provider_authorization_can_be_read_from_private_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    secret_file = tmp_path / "authorization"
    secret_file.write_text("test-authorization\n", encoding="utf-8")
    secret_file.chmod(0o400)
    monkeypatch.delenv("TIANYANCHA_AUTHORIZATION", raising=False)
    monkeypatch.setenv("TIANYANCHA_AUTHORIZATION_FILE", str(secret_file))

    assert worker_cli._provider_authorization() == "test-authorization"


def test_provider_authorization_rejects_ambiguous_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    secret_file = tmp_path / "authorization"
    secret_file.write_text("file-value", encoding="utf-8")
    monkeypatch.setenv("TIANYANCHA_AUTHORIZATION", "environment-value")
    monkeypatch.setenv("TIANYANCHA_AUTHORIZATION_FILE", str(secret_file))

    with pytest.raises(RuntimeError, match="only one"):
        worker_cli._provider_authorization()


def test_provider_authorization_rejects_group_readable_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    secret_file = tmp_path / "authorization"
    secret_file.write_text("file-value", encoding="utf-8")
    secret_file.chmod(0o440)
    monkeypatch.delenv("TIANYANCHA_AUTHORIZATION", raising=False)
    monkeypatch.setenv("TIANYANCHA_AUTHORIZATION_FILE", str(secret_file))

    with pytest.raises(RuntimeError, match="owner-only"):
        worker_cli._provider_authorization()


def test_worker_lock_rejects_a_second_process_slot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    lock_file = tmp_path / "worker.lock"
    monkeypatch.setenv("ON_DEMAND_WORKER_LOCK_FILE", str(lock_file))

    with worker_cli._exclusive_worker_lock():
        with pytest.raises(RuntimeError, match="already holds the lock"):
            with worker_cli._exclusive_worker_lock():
                pytest.fail("a second worker must not acquire the lock")
        with pytest.raises(RuntimeError, match="already holds the lock"):
            with worker_cli._exclusive_worker_lock():
                pytest.fail("a failed lock attempt must not release the first worker")

    assert lock_file.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"on_demand_research_enabled": False}, "ON_DEMAND_RESEARCH_ENABLED"),
        ({"external_calls_enabled": False}, "EXTERNAL_CALLS_ENABLED"),
        ({"tianyancha_identity_calls_enabled": False}, "TIANYANCHA_IDENTITY_CALLS_ENABLED"),
        ({"paid_api_calls_enabled": True}, "PAID_API_CALLS_ENABLED"),
        ({"auto_refresh_enabled": True}, "AUTO_REFRESH_ENABLED"),
        (
            {"publication_policy": PublicationPolicy(enabled=True)},
            "AUTO_PUBLISH_ENABLED",
        ),
        ({"trusted_source_calls_enabled": True}, "trusted-source"),
        ({"source_monitor_scheduler_enabled": True}, "trusted-source"),
    ],
)
def test_real_worker_requires_narrow_explicit_switches(
    override: dict[str, object],
    message: str,
) -> None:
    safe = Settings(
        database_url="sqlite:///unused.db",
        app_mode="demo",
        on_demand_research_enabled=True,
        external_calls_enabled=True,
        tianyancha_identity_calls_enabled=True,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
        publication_policy=PublicationPolicy(enabled=False),
        trusted_source_calls_enabled=False,
        source_monitor_scheduler_enabled=False,
    )

    with pytest.raises(RuntimeError, match=message):
        worker_cli._validate_worker_safety(replace(safe, **override))


def test_on_demand_worker_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ON_DEMAND_RESEARCH_ENABLED",
        "EXTERNAL_CALLS_ENABLED",
        "TIANYANCHA_IDENTITY_CALLS_ENABLED",
        "TIANYANCHA_RESEARCH_CALLS_ENABLED",
        "PAID_API_CALLS_ENABLED",
        "AUTO_REFRESH_ENABLED",
        "AUTO_PUBLISH_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.on_demand_research_enabled is False
    assert settings.external_calls_enabled is False
    assert settings.tianyancha_identity_calls_enabled is False
    assert settings.tianyancha_research_calls_enabled is False
    assert settings.paid_api_calls_enabled is False
    assert settings.auto_refresh_enabled is False
    assert settings.publication_policy.enabled is False


def test_worker_rejects_retry_policy_above_per_company_call_cap() -> None:
    settings = Settings(
        database_url="sqlite:///unused.db",
        app_mode="demo",
        on_demand_research_enabled=True,
        external_calls_enabled=True,
        tianyancha_identity_calls_enabled=True,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
        publication_policy=PublicationPolicy(enabled=False),
        on_demand_research_policy=OnDemandResearchPolicy(max_provider_calls_per_company=3),
    )

    with pytest.raises(RuntimeError, match="MAX_PROVIDER_CALLS_PER_COMPANY"):
        worker_cli._validate_worker_safety(settings)
