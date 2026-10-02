"""HTTP/控制存储边界用 Mock 查询，不为每个拒绝例创建业务数据库。"""

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.testclient import TestClient

from backend.app.mcp_control import ControlStore, digest
from tests.support.business_oauth import (
    RESOURCE,
    StubRead,
    invoke,
    login,
    rows,
    setup,
)


@pytest.mark.parametrize("mode", ["missing", "invalid", "expired", "revoked"])
def test_prehandler_denials_have_correlated_challenge_and_bounded_metadata(tmp_path, mode):
    app, oauth, grant = setup(tmp_path)
    with TestClient(app, base_url=RESOURCE) as client:
        token = login(client)
        if mode == "expired":
            with oauth.store.transaction() as c:
                c.execute(
                    "UPDATE records SET expires=? WHERE kind='access' AND key=?",
                    (time.time() - 1, digest(token)),
                )
        elif mode == "revoked":
            oauth.store.revoke("grant", grant.id)
        response = invoke(
            client,
            None if mode == "missing" else "private_invalid_token" if mode == "invalid" else token,
        )
        assert response.status_code == 401
        assert 'error="invalid_token"' in response.headers["www-authenticate"]
        assert (
            RESOURCE + "/.well-known/oauth-protected-resource"
            in response.headers["www-authenticate"]
        )
        audit = rows(oauth.store, response)
        assert len(audit) == 1 and audit[0]["tool"] == "find_company"
        assert audit[0]["reason"] == "authentication_rejected"
        assert audit[0]["objects"] == "{}" and audit[0]["principal"] is None
        serialized = json.dumps(audit)
        for value in (
            token,
            "private_query_do_not_log",
            "untrusted_rpc_id_do_not_log",
            "private_invalid_token",
        ):
            assert value not in serialized


def test_audit_failure_cannot_release_completed_content(tmp_path, monkeypatch):
    app, oauth, _ = setup(tmp_path)
    original = oauth.store.finish
    with TestClient(app, base_url=RESOURCE) as client:
        token = login(client)

        def fail(audit_id, outcome, *args, **kwargs):
            if outcome == "returned":
                raise sqlite3.OperationalError("simulated_full_disk")
            return original(audit_id, outcome, *args, **kwargs)

        monkeypatch.setattr(oauth.store, "finish", fail)
        response = invoke(client, token)
        assert response.status_code == 503 and "synthetic" not in response.text
        assert all(r["outcome"] != "returned" for r in rows(oauth.store, response))


def test_sdk_argument_rejection_is_not_a_successful_tool_result(tmp_path):
    app, oauth, _ = setup(tmp_path)
    with TestClient(app, base_url=RESOURCE) as client:
        response = invoke(client, login(client), arguments={"untrusted_key": "do_not_log"})
        assert response.status_code == 200 and response.json()["result"]["isError"]
        audit = rows(oauth.store, response)
        assert len(audit) == 1 and audit[0]["tool"] == "find_company"
        assert audit[0]["reason"] == "protocol_or_tool_validation_rejected"
        assert "do_not_log" not in json.dumps(audit)


def test_late_query_cannot_overwrite_outer_timeout(tmp_path, monkeypatch):
    from backend.app import business_mcp

    done = threading.Event()
    app, oauth, _ = setup(tmp_path)

    def slow(self, grant, scopes, tool, params, **kwargs):
        time.sleep(0.08)
        oauth.store.query_finish(kwargs["audit_id"], "query_complete", 0.08, 1, 1, 15, {})
        done.set()
        return {"items": [{"synthetic": True}]}

    monkeypatch.setattr(StubRead, "call", slow)
    monkeypatch.setattr(business_mcp, "TOOL_TIMEOUT_SECONDS", 0.02)
    with TestClient(app, base_url=RESOURCE) as client:
        response = invoke(client, login(client))
        assert "read_timeout" in response.text
        assert done.wait(1)
        item = next(r for r in rows(oauth.store, response) if r["operation"] == "business_read")
        assert item["outcome"] == "denied" and item["reason"] == "read_timeout"
        assert item["query_outcome"] == "query_complete" and item["delivery"] == "unknown"


def test_concurrency_rejection_is_audited_before_query_and_shared_quota(tmp_path, monkeypatch):
    app, oauth, _ = setup(tmp_path)
    barrier, release = threading.Barrier(3), threading.Event()

    def slow(self, *args, **kwargs):
        barrier.wait(timeout=2)
        assert release.wait(2)
        return {"items": [{"synthetic": True}]}

    monkeypatch.setattr(StubRead, "call", slow)
    with TestClient(app, base_url=RESOURCE) as client:
        token = login(client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            running = [pool.submit(invoke, client, token) for _ in range(2)]
            try:
                barrier.wait(timeout=2)
                response = invoke(client, token)
                assert "concurrency_limit" in response.text
                item = next(
                    r for r in rows(oauth.store, response) if r["operation"] == "business_read"
                )
                assert item["reason"] == "concurrency_limit" and item["objects"] == "{}"
            finally:
                release.set()
            assert all(not f.result().json()["result"].get("isError") for f in running)
    for tool in ("find_company", "get_saved_report"):
        oauth.store.quota("separate_quota_principal", "business_read", minute=2, tool=tool)
    with pytest.raises(PermissionError, match="quota_exceeded"):
        oauth.store.quota(
            "separate_quota_principal", "business_read", minute=2, tool="get_company_matters"
        )


def test_terminal_revoke_and_transport_failure_do_not_claim_delivery(tmp_path, monkeypatch):
    app, oauth, grant = setup(tmp_path)

    def revoke(self, *args, **kwargs):
        oauth.store.revoke("grant", grant.id)
        return {"items": [{"synthetic": True}]}

    with TestClient(app, base_url=RESOURCE) as client:
        token = login(client)
        with monkeypatch.context() as patch:
            patch.setattr(StubRead, "call", revoke)
            response = invoke(client, token)
        assert "grant_revoked_before_return" in response.text and "synthetic" not in response.text
        assert not any(r["outcome"] == "returned" for r in rows(oauth.store, response))

    app2, oauth2, _ = setup(tmp_path / "disconnect")
    drop = {"enabled": False}

    async def disconnected(scope, receive, send):
        async def failing_send(message):
            if drop["enabled"] and message["type"] == "http.response.body":
                raise OSError("client_disconnected")
            await send(message)

        return await app2(scope, receive, failing_send)

    with TestClient(disconnected, base_url=RESOURCE, raise_server_exceptions=False) as client:
        token = login(client)
        drop["enabled"] = True
        response = invoke(client, token)
        assert all(r["outcome"] == "cancelled" for r in rows(oauth2.store, response))


def test_control_upgrade_retains_quota_and_noise_is_bounded(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as c:
        c.execute(
            "CREATE TABLE audit(at REAL,principal TEXT,operation TEXT,outcome "
            "TEXT,seconds REAL,sql_count INTEGER,rows INTEGER,bytes INTEGER)"
        )
        c.execute(
            "INSERT INTO audit VALUES(?, 'approved','business_read','ok',1,1,1,20)", (time.time(),)
        )
    path.chmod(0o600)
    store = ControlStore(path)
    with pytest.raises(PermissionError, match="quota_exceeded"):
        store.quota(digest("approved"), "business_read", minute=1)
    for i in range(600):
        assert store.begin_mcp(str(i)) is not None
    assert store.begin_mcp("over_limit") is None
    assert store.begin_mcp("over_limit_again") is None
    with store.transaction() as c:
        assert (
            c.execute("SELECT count(*) FROM audit WHERE operation='mcp_http'").fetchone()[0] == 600
        )
        assert c.execute("SELECT sum(count) FROM audit_noise").fetchone()[0] == 2
        c.execute("UPDATE audit SET at=?", (time.time() - 32 * 86400,))
    assert store.begin_mcp("after_retention") is not None
    with store.transaction() as c:
        assert c.execute("SELECT count(*) FROM audit").fetchone()[0] == 1


def test_interrupted_control_upgrade_does_not_reset_existing_quota(tmp_path, monkeypatch):
    from backend.app import mcp_control

    path = tmp_path / "interrupted.db"
    with sqlite3.connect(path) as c:
        c.execute(
            "CREATE TABLE audit(at REAL,principal TEXT,operation TEXT,outcome "
            "TEXT,seconds REAL,sql_count INTEGER,rows INTEGER,bytes INTEGER)"
        )
        c.execute(
            "INSERT INTO audit VALUES(?, 'approved','business_read','ok',1,1,1,20)", (time.time(),)
        )
    path.chmod(0o600)

    def interrupt(_):
        raise RuntimeError("interrupted_upgrade")

    with monkeypatch.context() as patch:
        patch.setattr(mcp_control, "digest", interrupt)
        with pytest.raises(RuntimeError, match="interrupted_upgrade"):
            ControlStore(path)
    recovered = ControlStore(path)
    with pytest.raises(PermissionError, match="quota_exceeded"):
        recovered.quota(digest("approved"), "business_read", minute=1)
