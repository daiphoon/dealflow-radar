from __future__ import annotations

import json
import sys
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

import scripts.run_investor_analysis_worker as worker_cli
from backend.app.config import InvestorAnalysisPolicy, PublicationPolicy, Settings


def _safe_settings() -> Settings:
    return Settings(
        database_url="sqlite:///unused.db",
        app_mode="demo",
        external_calls_enabled=True,
        paid_api_calls_enabled=True,
        auto_refresh_enabled=False,
        investor_analysis_enabled=True,
        publication_policy=PublicationPolicy(enabled=False),
        investor_analysis_policy=InvestorAnalysisPolicy(
            input_cost_per_million_tokens=Decimal("1"),
            output_cost_per_million_tokens=Decimal("2"),
        ),
    )


def test_dry_run_does_not_read_secret_or_create_provider(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = replace(
        _safe_settings(),
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        investor_analysis_enabled=False,
    )
    monkeypatch.setenv("ANALYSIS_WORKER_USER_ID", str(uuid4()))
    monkeypatch.setenv("WORKER_TENANT_ID", str(uuid4()))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["run_investor_analysis_worker", "--dry-run"])
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
        "DeepSeekInvestorAnalysisProvider",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not create a model provider"),
    )

    worker_cli.main()

    assert json.loads(capsys.readouterr().out) == {
        "status": "dry_run",
        "external_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": "0",
    }


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"investor_analysis_enabled": False}, "INVESTOR_ANALYSIS_ENABLED"),
        ({"external_calls_enabled": False}, "EXTERNAL_CALLS_ENABLED"),
        ({"paid_api_calls_enabled": False}, "PAID_API_CALLS_ENABLED"),
        ({"auto_refresh_enabled": True}, "AUTO_REFRESH_ENABLED"),
        ({"publication_policy": PublicationPolicy(enabled=True)}, "AUTO_PUBLISH_ENABLED"),
        ({"trusted_source_calls_enabled": True}, "trusted source"),
        ({"source_monitor_scheduler_enabled": True}, "trusted source"),
        (
            {
                "investor_analysis_policy": InvestorAnalysisPolicy(
                    input_cost_per_million_tokens=Decimal("0"),
                    output_cost_per_million_tokens=Decimal("0"),
                )
            },
            "token prices",
        ),
    ],
)
def test_real_worker_requires_narrow_explicit_switches(
    override: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        worker_cli._validate_worker_safety(replace(_safe_settings(), **override))


def test_investor_analysis_worker_is_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "INVESTOR_ANALYSIS_ENABLED",
        "EXTERNAL_CALLS_ENABLED",
        "PAID_API_CALLS_ENABLED",
        "AUTO_REFRESH_ENABLED",
        "AUTO_PUBLISH_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.investor_analysis_enabled is False
    assert settings.external_calls_enabled is False
    assert settings.paid_api_calls_enabled is False
    assert settings.auto_refresh_enabled is False
    assert settings.publication_policy.enabled is False
