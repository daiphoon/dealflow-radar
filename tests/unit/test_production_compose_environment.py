from __future__ import annotations

import json
import os
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.config import Settings

ROOT = Path(__file__).resolve().parents[2]
BACKEND_SERVICES = (
    "api",
    "web-research-worker",
    "investor-analysis-worker",
    "preflight",
    "migrate",
)


@pytest.fixture(scope="module")
def docker() -> str:
    executable = shutil.which("docker")
    if executable is None:
        pytest.skip("Docker Compose CLI is required; no containers are started")
    result = subprocess.run(
        [executable, "compose", "version"], capture_output=True, check=False, timeout=15
    )
    if result.returncode:
        pytest.skip("Docker Compose plugin is required; no containers are started")
    return executable


def _resolve(docker: str, example: str, overrides: dict[str, str]) -> dict:
    result = subprocess.run(
        [
            docker,
            "compose",
            "--profile",
            "*",
            "--env-file",
            str(ROOT / "deploy" / f"{example}.env.example"),
            "-f",
            str(ROOT / "compose.production.yml"),
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env={
            **{key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ},
            "CLOUDBASE_ENV_ID": "env-compose-test",
            **overrides,
        },
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(result.stdout)["services"]


@pytest.mark.parametrize("example", ["production", "single-host", "acceptance"])
def test_initial_deployment_keeps_features_closed_and_prices_unknown(
    docker: str, example: str
) -> None:
    services = _resolve(docker, example, {})
    for name in BACKEND_SERVICES:
        environment = services[name]["environment"]
        for switch in (
            "TENDER_EVENTS_ENABLED",
            "WATCHLIST_MONITOR_ENABLED",
            "EXTERNAL_CALLS_ENABLED",
            "PAID_API_CALLS_ENABLED",
            "AUTO_REFRESH_ENABLED",
            "AUTO_PUBLISH_ENABLED",
        ):
            assert environment[switch] == "false", (name, switch)
        with patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_env()
        assert settings.tender_events_enabled is False
        policy = settings.web_research_policy
        assert policy.watchlist.enabled is False
        assert policy.watchlist.interval_days == 7
        assert policy.watchlist.max_companies_per_run == 5
        assert policy.cost.baidu_price_per_call is None
        assert policy.cost.bocha_price_per_call is None
        assert policy.cost.unknown_price_upper_bound is None
        for key in (
            "task_limit",
            "company_daily_limit",
            "company_weekly_limit",
            "system_weekly_limit",
            "system_monthly_limit",
        ):
            assert getattr(policy.cost, key) == Decimal(0)


def test_explicit_event_monitor_and_budget_settings_reach_all_backend_services(
    docker: str,
) -> None:
    overrides = {
        "TENDER_EVENTS_ENABLED": "true",
        "WATCHLIST_MONITOR_ENABLED": "true",
        "WATCHLIST_MONITOR_POLICY_VERSION": "watchlist-compose-test",
        "WATCHLIST_MONITOR_INTERVAL_DAYS": "14",
        "WATCHLIST_MONITOR_MAX_COMPANIES_PER_RUN": "2",
        "WATCHLIST_MONITOR_COOLDOWN_HOURS": "48",
        "WATCHLIST_MONITOR_FAILURE_BACKOFF_MAX_DAYS": "21",
        "WATCHLIST_MONITOR_NO_CHANGE_BACKOFF_AFTER": "4",
        "WATCHLIST_MONITOR_MAX_INTERVAL_DAYS": "28",
        "WEB_RESEARCH_COST_VERSION": "web-cost-compose-test",
        "WEB_RESEARCH_BAIDU_PRICE_PER_CALL": "0.01",
        "WEB_RESEARCH_BOCHA_PRICE_PER_CALL": "0.02",
        "WEB_RESEARCH_UNKNOWN_PRICE_UPPER_BOUND": "0.03",
        "WEB_RESEARCH_TASK_LIMIT": "0.12",
        "WEB_RESEARCH_COMPANY_DAILY_LIMIT": "0.24",
        "WEB_RESEARCH_COMPANY_WEEKLY_LIMIT": "0.48",
        "WEB_RESEARCH_SYSTEM_WEEKLY_LIMIT": "0.96",
        "WEB_RESEARCH_SYSTEM_MONTHLY_LIMIT": "1.92",
    }
    services = _resolve(docker, "single-host", overrides)
    for name in BACKEND_SERVICES:
        environment = services[name]["environment"]
        assert {key: environment.get(key) for key in overrides} == overrides, name
        with patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_env()
        assert settings.tender_events_enabled is True
        assert settings.web_research_policy.watchlist.enabled is True
        assert settings.web_research_policy.watchlist.interval_days == 14
        assert settings.web_research_policy.cost.task_limit == Decimal("0.12")
        assert settings.web_research_policy.cost.bocha_price_per_call == Decimal("0.02")
        assert settings.external_calls_enabled is False
        assert settings.paid_api_calls_enabled is False
