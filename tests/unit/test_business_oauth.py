import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from starlette.testclient import TestClient

from backend.app.business_mcp_auth import FeishuGateway
from tests.support.business_oauth import (
    CALLBACK,
    RESOURCE,
    authorization_code,
    exchange,
    params,
    register,
    setup,
)


def test_dcr_pkce_feishu_consent_mcp_refresh_revoke_restart(tmp_path):
    app, oauth, grant = setup(tmp_path)
    with TestClient(app, base_url=RESOURCE) as client:
        metadata = client.get("/.well-known/oauth-authorization-server").json()
        assert metadata["issuer"] == RESOURCE
        assert metadata["token_endpoint_auth_methods_supported"] == ["none"]
        assert metadata["code_challenge_methods_supported"] == ["S256"]
        protected = client.get("/.well-known/oauth-protected-resource").json()
        assert protected["resource"] == RESOURCE and len(protected["scopes_supported"]) == 3
        denied = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert (
            denied.status_code == 401 and "resource_metadata" in denied.headers["www-authenticate"]
        )
        cid = register(client)
        code = authorization_code(client, cid)
        assert exchange(client, cid, code, code_verifier="w" * 43).status_code == 400
        response = exchange(client, cid, code)
        assert response.status_code == 200, response.text
        tokens = response.json()
        assert exchange(client, cid, code).status_code == 400
        headers = {
            "Authorization": "Bearer " + tokens["access_token"],
            "Accept": "application/json, text/event-stream",
        }
        listed = client.post(
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        ).json()
        assert {t["name"] for t in listed["result"]["tools"]} == {
            "find_company",
            "get_company_matters",
            "list_company_reports",
            "get_saved_report",
        }
        assert all(t["annotations"]["readOnlyHint"] for t in listed["result"]["tools"])
        result = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "find_company", "arguments": {"query": "虚构"}},
            },
        ).json()
        assert "synthetic" in str(result) and not result["result"].get("isError")
        refreshed = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": cid,
                "resource": RESOURCE,
                "refresh_token": tokens["refresh_token"],
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        fresh = refreshed.json()
        assert fresh["refresh_token"] != tokens["refresh_token"]
        # 重放旧 refresh 撤销整条链。
        assert (
            client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": cid,
                    "resource": RESOURCE,
                    "refresh_token": tokens["refresh_token"],
                },
            ).status_code
            == 400
        )
        assert asyncio.run(oauth.load_access_token(fresh["access_token"])) is None
        cid2 = register(client)
        tokens2 = exchange(client, cid2, authorization_code(client, cid2)).json()
        assert (
            client.post(
                "/revoke",
                data={
                    "client_id": cid2,
                    "token": tokens2["access_token"],
                },
            ).status_code
            == 200
        )
        assert asyncio.run(oauth.load_access_token(tokens2["access_token"])) is None
        assert asyncio.run(oauth.get_client(cid)) is not None
    from backend.app.mcp_control import ControlStore

    restarted = ControlStore(tmp_path / "control.db")
    assert restarted.grant(grant.id) is not None
    assert restarted.get("client", cid) is not None
    raw = (tmp_path / "control.db").read_bytes()
    for secret in (
        tokens["access_token"],
        tokens["refresh_token"],
        code,
        "not_a_real_secret",
        "synthetic_code",
    ):
        assert secret.encode() not in raw
    assert oauth.gateway.calls == 2


@pytest.mark.parametrize(
    "change",
    [
        {"resource": "https://evil.example.com"},
        {"code_challenge_method": "plain"},
        {"code_challenge": "short"},
        {"redirect_uri": CALLBACK + "?evil=1"},
    ],
)
def test_authorize_contract_rejected_before_upstream(tmp_path, change):
    app, oauth, _ = setup(tmp_path)
    with TestClient(app, base_url=RESOURCE) as client:
        cid = register(client)
        assert client.get("/authorize", params={**params(cid), **change}).status_code == 400
        assert oauth.gateway.calls == 0


@pytest.mark.parametrize(
    "change",
    [
        {"redirect_uris": ["https://evil.example.com/callback"]},
        {"redirect_uris": [CALLBACK + "?x=1"]},
        {"token_endpoint_auth_method": "client_secret_post"},
        {"grant_types": ["client_credentials"]},
        {"response_types": ["token"]},
        {"scope": "dealflow.write"},
    ],
)
def test_dcr_cannot_grant_or_expand(tmp_path, change):
    app, _, _ = setup(tmp_path)
    with TestClient(app, base_url=RESOURCE) as client:
        response = client.post(
            "/register",
            json={
                "redirect_uris": [CALLBACK],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                **change,
            },
        )
        assert response.status_code == 400


@pytest.mark.parametrize("failure", ["state", "browser", "tenant", "identity", "upstream"])
def test_feishu_state_binding_and_identity_allowlist(tmp_path, failure):
    app, oauth, _ = setup(tmp_path)
    with TestClient(app, base_url=RESOURCE) as client:
        cid = register(client)
        auth = client.get("/authorize", params=params(cid), follow_redirects=False)
        start = client.get(auth.headers["location"], follow_redirects=False)
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        if failure == "state":
            state = "wrong"
        elif failure == "browser":
            client.cookies.clear()
        elif failure == "tenant":
            oauth.gateway.tenant = "not_approved"
        elif failure == "identity":
            oauth.gateway.open_id = "not_approved"
        else:
            oauth.gateway.fail = True
        result = client.get("/feishu/callback", params={"state": state, "code": "synthetic_code"})
        assert result.status_code in {400, 403}
        assert "ticket" not in result.text
        assert (
            client.get(
                "/feishu/callback", params={"state": state, "code": "synthetic_code"}
            ).status_code
            == 400
        )


def test_real_gateway_fixed_official_boundaries_with_mock_transport():
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(200, json={"code": 0, "access_token": "upstream_only"})
        assert request.headers["authorization"] == "Bearer upstream_only"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"tenant_key": "tenant", "open_id": "openid", "email": "ignored"},
            },
        )

    gateway = FeishuGateway(
        "mock_app",
        "mock_secret",
        RESOURCE + "/feishu/callback",
        transport=httpx.MockTransport(handler),
    )
    assert asyncio.run(gateway.identity("mock_code")) == ("tenant", "openid")
    assert calls == [("POST", gateway.TOKEN), ("GET", gateway.USER)]


def test_rolling_quota_persists_but_not_lifetime(tmp_path, monkeypatch):
    import backend.app.mcp_control as module
    from backend.app.mcp_control import ControlStore

    store = ControlStore(tmp_path / "control.db")
    start = module.time.time()
    for _ in range(2):
        store.quota("approved", "business_read", minute=2, day=3)
    with pytest.raises(PermissionError):
        ControlStore(tmp_path / "control.db").quota("approved", "business_read", minute=2, day=3)
    monkeypatch.setattr(module.time, "time", lambda: start + 86401)
    assert store.quota("approved", "business_read", minute=2, day=3)


@pytest.mark.parametrize("boundary", ["client", "redirect", "resource", "expiry"])
def test_code_exchange_binding_and_expiry(tmp_path, boundary):
    app, oauth, _ = setup(tmp_path)
    with TestClient(app, base_url=RESOURCE) as client:
        cid = register(client)
        code = authorization_code(client, cid)
        changes = {}
        if boundary == "client":
            cid = register(client)
        elif boundary == "redirect":
            changes["redirect_uri"] = CALLBACK + "?other=1"
        elif boundary == "resource":
            changes["resource"] = "https://other.example.com"
        else:
            value = oauth.store.get("code", code)
            oauth.store.put("code", code, value, time.time() - 1)
        assert exchange(client, cid, code, **changes).status_code == 400


def test_refresh_no_scope_expansion_resource_binding_and_identity_reauthentication(tmp_path):
    app, oauth, _ = setup(tmp_path)
    with TestClient(app, base_url=RESOURCE) as client:
        cid = register(client)
        tokens = exchange(client, cid, authorization_code(client, cid)).json()
        fields = {
            "grant_type": "refresh_token",
            "client_id": cid,
            "resource": RESOURCE,
            "refresh_token": tokens["refresh_token"],
        }
        assert client.post("/token", data={**fields, "scope": "dealflow.write"}).status_code == 400
        assert (
            client.post(
                "/token", data={**fields, "resource": "https://other.example.com"}
            ).status_code
            == 400
        )
        fresh = client.post("/token", data={**fields, "scope": "dealflow.company.read"})
        assert fresh.status_code == 200 and fresh.json()["scope"] == "dealflow.company.read"
        value = oauth.store.get("access", fresh.json()["access_token"])
        value["authenticated_at"] = time.time() - oauth.UPSTREAM_REAUTH_TTL - 1
        oauth.store.put("access", fresh.json()["access_token"], value, value["expires_at"])
        assert asyncio.run(oauth.load_access_token(fresh.json()["access_token"])) is None
        value = oauth.store.get("refresh", fresh.json()["refresh_token"])
        value["authenticated_at"] = time.time() - oauth.UPSTREAM_REAUTH_TTL - 1
        oauth.store.put("refresh", fresh.json()["refresh_token"], value, value["expires_at"])
        assert (
            client.post(
                "/token", data={**fields, "refresh_token": fresh.json()["refresh_token"]}
            ).status_code
            == 400
        )


def test_concurrent_refresh_replay_commits_chain_revocation(tmp_path):
    from mcp.server.auth.provider import TokenError

    app, oauth, _ = setup(tmp_path)
    with TestClient(app, base_url=RESOURCE) as client:
        cid = register(client)
        tokens = exchange(client, cid, authorization_code(client, cid)).json()
        registered = asyncio.run(oauth.get_client(cid))
        refresh = asyncio.run(oauth.load_refresh_token(registered, tokens["refresh_token"]))

        def rotate(_):
            try:
                return asyncio.run(
                    oauth.exchange_refresh_token(registered, refresh, refresh.scopes)
                )
            except TokenError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(rotate, range(2)))
        assert sum(r is not None for r in results) <= 1
        for result in results:
            if result:
                assert asyncio.run(oauth.load_access_token(result.access_token)) is None


def test_oauth_malformed_duplicate_body_bounds_and_audit_no_credentials(tmp_path):
    app, oauth, _ = setup(tmp_path)
    with TestClient(app, base_url=RESOURCE) as client:
        cid = register(client)
        assert (
            client.get(
                "/authorize", params=list(params(cid).items()) + [("resource", RESOURCE)]
            ).status_code
            == 400
        )
        assert client.post("/revoke", content=b"client_id=%ff\xff").status_code == 400
        assert client.post("/register", content=b"x" * 16385).status_code == 413
        assert client.post("/token", data={"resource": RESOURCE}).status_code == 401
    with oauth.store.transaction() as c:
        rows = list(c.execute("SELECT outcome,operation FROM audit"))
    assert any(r["outcome"] == "http_201" for r in rows)
    assert any(r["outcome"] == "http_400" for r in rows)
    assert not any(r["outcome"] == "reserved" for r in rows)
    assert "client_id=" not in (tmp_path / "control.db").read_bytes().decode(errors="ignore")


def test_current_user_and_client_revocation_are_checked_on_existing_token(tmp_path):
    app, oauth, _ = setup(tmp_path)
    with TestClient(app, base_url=RESOURCE) as client:
        cid = register(client)
        tokens = exchange(client, cid, authorization_code(client, cid)).json()
        oauth.active_user = lambda grant: False
        assert asyncio.run(oauth.load_access_token(tokens["access_token"])) is None
        oauth.active_user = lambda grant: True
        oauth.store.revoke("client", cid)
        assert asyncio.run(oauth.load_access_token(tokens["access_token"])) is None


def test_business_runtime_default_off_before_database_or_secret_access(monkeypatch):
    from scripts.run_business_mcp import build

    monkeypatch.delenv("MCP_BUSINESS_ENABLED", raising=False)
    with pytest.raises(ValueError, match="business_mcp_disabled"):
        build()


@pytest.mark.parametrize("failure", ["redirect", "oversize", "shape", "identity_missing"])
def test_feishu_transport_failure_is_closed_and_never_follows_arbitrary_location(failure):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://evil.example.com"})
        if failure == "oversize":
            return httpx.Response(200, content=b"x" * 65537)
        if failure == "shape":
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(200, json={"access_token": "mock_upstream_only"})
        return httpx.Response(200, json={"code": 0, "data": {"open_id": "unknown"}})

    gateway = FeishuGateway(
        "mock_app",
        "mock_secret",
        RESOURCE + "/feishu/callback",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(PermissionError, match="feishu_identity_unavailable"):
        asyncio.run(gateway.identity("mock_code"))
    assert set(calls) <= {gateway.TOKEN, gateway.USER}


def test_error_redirect_issuer_preserves_exact_registered_query(tmp_path):
    app, oauth, _ = setup(tmp_path)
    callback = CALLBACK + "?fixed=1"
    oauth.redirects = (callback,)
    with TestClient(app, base_url=RESOURCE) as client:
        response = client.post(
            "/register",
            json={
                "redirect_uris": [callback],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "scope": "dealflow.company.read",
            },
        )
        assert response.status_code == 201, response.text
        rejected = client.get(
            "/authorize",
            params={
                **params(response.json()["client_id"]),
                "redirect_uri": callback,
                "scope": "dealflow.write",
            },
            follow_redirects=False,
        )
        assert rejected.status_code == 302
        query = parse_qs(urlsplit(rejected.headers["location"]).query)
        assert query["fixed"] == ["1"] and query["error"] == ["invalid_scope"]
        assert query.get("iss") == [RESOURCE]
        assert oauth.gateway.calls == 0
