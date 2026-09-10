from dataclasses import replace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from backend.app.config import Settings, WatchlistMonitorPolicy
from backend.app.watchlist_monitoring import interval_days
from scripts import queue_watchlist_checks as cli
from scripts.run_web_research_worker import _watchlist_gate


def test_policy_is_disabled_by_default_and_backoff_is_bounded(monkeypatch):
    monkeypatch.delenv("WATCHLIST_MONITOR_ENABLED", raising=False)
    assert not Settings.from_env().web_research_policy.watchlist.enabled
    policy = WatchlistMonitorPolicy()
    assert [interval_days(policy, value) for value in (0, 2, 3, 6, 9, 1000)] == [
        7,
        7,
        14,
        28,
        30,
        30,
    ]
    with pytest.raises(ValueError):
        replace(policy, interval_days=0)


@pytest.mark.parametrize("enqueue", [False, True])
def test_queue_cli_defaults_to_read_only_without_creating_providers(monkeypatch, capsys, enqueue):
    monkeypatch.setenv("WEB_RESEARCH_WORKER_USER_ID", str(uuid4()))
    monkeypatch.setenv("WORKER_TENANT_ID", str(uuid4()))
    monkeypatch.setattr("sys.argv", ["queue_watchlist_checks"] + (["--enqueue"] if enqueue else []))
    mock = Mock(return_value={"external_calls": 0})
    monkeypatch.setattr(cli, "queue_due_watch_checks", mock)
    monkeypatch.setattr(
        cli, "_with_worker_session", lambda settings, uid, tid, cb: cb("session", "user")
    )
    cli.main()
    assert mock.call_args.kwargs["dry_run"] is not enqueue
    assert '"external_calls":0' in capsys.readouterr().out


def test_watch_gate_rechecks_process_configuration_before_dispatch(monkeypatch):
    for key in (
        "WATCHLIST_MONITOR_ENABLED",
        "WEB_RESEARCH_ENABLED",
        "EXTERNAL_CALLS_ENABLED",
        "WEB_RESEARCH_CALLS_ENABLED",
        "PAID_API_CALLS_ENABLED",
    ):
        monkeypatch.setenv(key, "true")
    for key in (
        "AUTO_REFRESH_ENABLED",
        "AUTO_PUBLISH_ENABLED",
        "TRUSTED_SOURCE_CALLS_ENABLED",
        "SOURCE_MONITOR_SCHEDULER_ENABLED",
        "INVESTOR_ANALYSIS_ENABLED",
    ):
        monkeypatch.setenv(key, "false")
    assert _watchlist_gate()
    monkeypatch.setenv("EXTERNAL_CALLS_ENABLED", "false")
    assert not _watchlist_gate()
