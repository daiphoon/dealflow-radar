from __future__ import annotations

import json
import sys
from dataclasses import replace
from uuid import uuid4

import pytest

import scripts.run_web_research_worker as worker_cli
from backend.app.config import PublicationPolicy, Settings, WebResearchPolicy


def _safe_settings() -> Settings:
    return Settings(
        database_url="sqlite:///unused.db",
        app_mode="demo",
        external_calls_enabled=True,
        paid_api_calls_enabled=True,
        auto_refresh_enabled=False,
        web_research_enabled=True,
        web_research_calls_enabled=True,
        publication_policy=PublicationPolicy(enabled=False),
    )


def test_dry_run_needs_no_provider_key_or_external_switch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = replace(
        _safe_settings(),
        external_calls_enabled=False,
        web_research_enabled=False,
        web_research_calls_enabled=False,
    )
    monkeypatch.setenv("WEB_RESEARCH_WORKER_USER_ID", str(uuid4()))
    monkeypatch.setenv("WORKER_TENANT_ID", str(uuid4()))
    monkeypatch.delenv("BAIDU_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("BOCHA_SEARCH_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["run_web_research_worker", "--dry-run"])
    monkeypatch.setattr(worker_cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(
        worker_cli,
        "_with_worker_session",
        lambda *_args, **_kwargs: {
            "status": "dry_run",
            "queued_jobs": 0,
            "external_calls": 0,
        },
    )
    monkeypatch.setattr(
        worker_cli,
        "BaiduSearchProvider",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not create providers"),
    )
    monkeypatch.setattr(
        worker_cli,
        "BochaSearchProvider",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not create providers"),
    )

    worker_cli.main()

    assert json.loads(capsys.readouterr().out) == {
        "status": "dry_run",
        "queued_jobs": 0,
        "external_calls": 0,
    }


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"web_research_enabled": False}, "WEB_RESEARCH_ENABLED"),
        ({"external_calls_enabled": False}, "EXTERNAL_CALLS_ENABLED"),
        ({"web_research_calls_enabled": False}, "WEB_RESEARCH_CALLS_ENABLED"),
        ({"paid_api_calls_enabled": False}, "PAID_API_CALLS_ENABLED"),
        ({"auto_refresh_enabled": True}, "AUTO_REFRESH_ENABLED"),
        ({"publication_policy": PublicationPolicy(enabled=True)}, "AUTO_PUBLISH_ENABLED"),
        ({"trusted_source_calls_enabled": True}, "trusted source"),
        ({"source_monitor_scheduler_enabled": True}, "trusted source"),
        ({"investor_analysis_enabled": True}, "INVESTOR_ANALYSIS_ENABLED"),
    ],
)
def test_real_worker_requires_narrow_explicit_switches(
    override: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        worker_cli._validate_worker_safety(replace(_safe_settings(), **override))


def test_web_research_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "WEB_RESEARCH_ENABLED",
        "WEB_RESEARCH_CALLS_ENABLED",
        "EXTERNAL_CALLS_ENABLED",
        "PAID_API_CALLS_ENABLED",
        "AUTO_REFRESH_ENABLED",
        "AUTO_PUBLISH_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.web_research_enabled is False
    assert settings.web_research_calls_enabled is False
    assert settings.external_calls_enabled is False
    assert settings.paid_api_calls_enabled is False
    assert settings.auto_refresh_enabled is False
    assert settings.publication_policy.enabled is False


def test_stale_refresh_and_topic_worker_can_run_together_without_auto_publication():
    settings = replace(
        _safe_settings(),
        auto_refresh_enabled=True,
        web_research_policy=WebResearchPolicy(
            incremental_research_enabled=True, topic_planning_enabled=True
        ),
    )
    worker_cli._validate_worker_safety(settings)
    with pytest.raises(RuntimeError, match="EXTERNAL_CALLS_ENABLED"):
        worker_cli._validate_worker_safety(replace(settings, external_calls_enabled=False))


def test_topic_worker_uses_same_ttl_and_cooldown_as_visit_policy(monkeypatch):
    monkeypatch.setenv("WEB_RESEARCH_INCREMENTAL_ENABLED", "true")
    monkeypatch.setenv("WEB_RESEARCH_TOPIC_PLANNING_ENABLED", "true")
    monkeypatch.setenv("RECENT_QUERY_TTL_DAYS", "21")
    monkeypatch.setenv("PERSONAL_REQUEST_COOLDOWN_HOURS", "36")
    settings = Settings.from_env()
    assert settings.web_research_policy.topic_planning_enabled
    assert (
        settings.web_research_policy.company_check_ttl_days
        == settings.refresh_policy.recent_query_ttl_days
        == 21
    )
    assert (
        settings.web_research_policy.company_cooldown_hours
        == settings.personal_entitlement_policy.request_cooldown_hours
        == 36
    )
