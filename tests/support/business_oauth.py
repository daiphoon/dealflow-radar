import base64
import hashlib
import re
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from backend.app.business_mcp import create_business_mcp
from backend.app.business_mcp_auth import BusinessOAuth, FeishuGateway
from backend.app.mcp_control import BUSINESS_SCOPES, ControlStore, Grant

RESOURCE = "https://mcp.example.com"
CALLBACK = "https://chatgpt.example.com/callback"
VERIFIER = "v" * 43


class MockFeishu(FeishuGateway):
    def __init__(self):
        super().__init__("fixture_app", "not_a_real_secret", RESOURCE + "/feishu/callback")
        self.calls, self.tenant, self.open_id, self.fail = (
            0,
            "fixture_tenant",
            "fixture_user",
            False,
        )

    async def identity(self, code):
        self.calls += 1
        if self.fail:
            raise PermissionError("mock_upstream_failure")
        return self.tenant, self.open_id


class StubRead:
    def call(self, grant, scopes, tool, params, **kwargs):
        return {"items": [{"synthetic": True}], "tool": tool}


def setup(tmp_path, service=None, grant=None):
    store = service.store if service else ControlStore(tmp_path / "control.db")
    grant = grant or Grant(
        "approved",
        str(uuid4()),
        str(uuid4()),
        (str(uuid4()),),
        (str(uuid4()),),
        (str(uuid4()),),
        BUSINESS_SCOPES,
        time.time() + 3600,
    )
    gateway = MockFeishu()
    store.approve_identity(gateway.app_id, gateway.tenant, gateway.open_id, grant)
    oauth = BusinessOAuth(
        store, RESOURCE, [CALLBACK], gateway, service.active_user if service else lambda g: True
    )
    app = create_business_mcp(service or StubRead(), oauth)
    return app, oauth, grant


def register(client):
    response = client.post(
        "/register",
        json={
            "redirect_uris": [CALLBACK],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": " ".join(BUSINESS_SCOPES),
        },
    )
    assert response.status_code == 201, response.text
    assert "client_secret" not in response.json()
    return response.json()["client_id"]


def params(client_id):
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
    )
    return {
        "client_id": client_id,
        "redirect_uri": CALLBACK,
        "response_type": "code",
        "state": "outer_state",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": RESOURCE,
        "scope": " ".join(BUSINESS_SCOPES),
    }


def authorization_code(client, client_id):
    auth = client.get("/authorize", params=params(client_id), follow_redirects=False)
    assert auth.status_code == 302, auth.text
    start = client.get(auth.headers["location"], follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    callback = client.get("/feishu/callback", params={"state": state, "code": "synthetic_code"})
    assert callback.status_code == 200, callback.text
    ticket = re.search(r'name="ticket" value="([^"]+)"', callback.text)[1]
    consent = client.post(
        "/feishu/consent", data={"ticket": ticket, "decision": "allow"}, follow_redirects=False
    )
    assert consent.status_code == 302, consent.text
    fields = parse_qs(urlsplit(consent.headers["location"]).query)
    assert fields["state"] == ["outer_state"] and fields["iss"] == [RESOURCE]
    return fields["code"][0]


def exchange(client, client_id, code, **override):
    return client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": CALLBACK,
            "code_verifier": VERIFIER,
            "resource": RESOURCE,
            **override,
        },
    )
