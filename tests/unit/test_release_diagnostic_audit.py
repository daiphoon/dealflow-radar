import json
import logging
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.diagnostic_mcp import create_mcp_app
from backend.app.diagnostics import DiagnosticService, Principal


@pytest.fixture(autouse=True)
def restore_logger_after_inprocess_alembic(monkeypatch):
    # Alembic's in-process fileConfig disables existing loggers; the MCP process is separate.
    monkeypatch.setattr(logging.getLogger("diagnostic.audit"), "disabled", False)


def test_pre_call_rejections_have_redacted_correlated_audit(caplog):
    caplog.set_level("INFO", logger="diagnostic.audit")
    service = DiagnosticService(None, b"synthetic-audit-key" * 3)
    principal = Principal("synthetic-private-principal", frozenset(), time.time() + 60)
    service.slots.acquire()
    service.slots.acquire()
    for actor, tool, values, expected in [
        (Principal("expired", frozenset(), 1), "find_company", {}, "invalid_principal"),
        (principal, "arbitrary-secret-text", {}, "tool_not_allowed"),
        (principal, "find_company", {"secret-input": "do-not-log"}, "unknown_parameters"),
        (principal, "find_company", {"query": "x"}, "concurrency_limit"),
    ]:
        with pytest.raises((ValueError, PermissionError)):
            service.call(actor, tool, values)
        row = json.loads(caplog.records[-1].message)
        assert row["reason_code"] == expected
        assert row["request_id"] and row["utc"] and row["version"]
    assert "synthetic-private-principal" not in caplog.text
    assert "do-not-log" not in caplog.text and "arbitrary-secret-text" not in caplog.text


def test_http_rejects_are_audited_and_aggregated(caplog):
    caplog.set_level("INFO", logger="diagnostic.audit")
    app = create_mcp_app(DiagnosticService(None, b"synthetic-audit-key" * 3), lambda: {})
    with TestClient(app) as client:
        for _ in range(30):
            assert (
                client.post("/mcp", headers={"Authorization": "Bearer never-log-this"}).status_code
                == 401
            )
        assert client.post("/mcp", headers={"Host": "bad.example"}).status_code == 401
    records = [json.loads(r.message) for r in caplog.records if r.name == "diagnostic.audit"]
    assert records and len(records) <= 4
    assert {r["reason_code"] for r in records} >= {"http_auth_rejected", "http_host_rejected"}
    assert "never-log-this" not in caplog.text
