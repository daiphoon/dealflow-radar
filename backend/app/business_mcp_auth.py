"""复用 MCP SDK 的 OAuth/DCR/PKCE；飞书仅确认身份，不签发本站权限。"""

import html
import json
import secrets
import time
from urllib.parse import urlencode

import httpx
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from backend.app.mcp_control import BUSINESS_SCOPES, digest


class FeishuGateway:
    AUTHORIZATION = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
    TOKEN = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
    USER = "https://open.feishu.cn/open-apis/authen/v1/user_info"

    def __init__(self, app_id, app_secret, callback, *, transport=None):
        self.app_id, self.app_secret, self.callback = app_id, app_secret, callback
        self.transport = transport

    def authorization_url(self, state):
        return (
            self.AUTHORIZATION
            + "?"
            + urlencode(
                {
                    "client_id": self.app_id,
                    "redirect_uri": self.callback,
                    "response_type": "code",
                    "state": state,
                    "scope": "contact:user.base:readonly",
                }
            )
        )

    async def identity(self, code):
        # 固定中国飞书官方端点；禁止重定向、任意 URL 和上游 token 持久化。
        async with httpx.AsyncClient(
            timeout=5, follow_redirects=False, trust_env=False, transport=self.transport
        ) as client:

            async def bounded(method, url, **kwargs):
                async with client.stream(method, url, **kwargs) as response:
                    if response.status_code != 200:
                        raise PermissionError("feishu_identity_unavailable")
                    body = b""
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > 65536:
                            raise PermissionError("feishu_identity_unavailable")
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise PermissionError("feishu_identity_unavailable")
                return result

            try:
                token = await bounded(
                    "POST",
                    self.TOKEN,
                    json={
                        "client_id": self.app_id,
                        "client_secret": self.app_secret,
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": self.callback,
                    },
                )
                if token.get("code", 0) != 0 or not token.get("access_token"):
                    raise PermissionError("feishu_identity_unavailable")
                user = await bounded(
                    "GET", self.USER, headers={"Authorization": "Bearer " + token["access_token"]}
                )
                if user.get("code") != 0:
                    raise PermissionError("feishu_identity_unavailable")
                data = user["data"]
                if (
                    not isinstance(data, dict)
                    or not data.get("open_id")
                    or not data.get("tenant_key")
                ):
                    raise PermissionError("feishu_identity_unavailable")
                return data["tenant_key"], data["open_id"]
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                raise PermissionError("feishu_identity_unavailable") from None


class BusinessOAuth:
    ACCESS_TTL = 900
    REFRESH_TTL = 7 * 86400
    CODE_TTL = 120
    LOGIN_TTL = 600
    UPSTREAM_REAUTH_TTL = 86400

    def __init__(self, store, resource, redirects, gateway, active_user):
        if not resource.startswith("https://") or resource.endswith("/"):
            raise ValueError("canonical_https_resource_required")
        if any(not r.startswith("https://") or "*" in r for r in redirects):
            raise ValueError("exact_https_callback_required")
        self.store, self.resource, self.redirects = store, resource, tuple(redirects)
        self.gateway, self.active_user = gateway, active_user
        if gateway.callback != resource + "/feishu/callback":
            raise ValueError("feishu_callback_mismatch")

    def current_grant(self, value, *, connection=None):
        grant = self.store.grant(value["subject"], connection=connection)
        if (
            not grant
            or grant.version != value["grant_version"]
            or time.time() - value.get("authenticated_at", 0) > self.UPSTREAM_REAUTH_TTL
            or not self.active_user(grant)
        ):
            raise PermissionError("grant_inactive")
        return grant

    async def get_client(self, client_id):
        value = self.store.get("client", client_id)
        return OAuthClientInformationFull(**value) if value else None

    async def register_client(self, client_info):
        if (
            client_info.token_endpoint_auth_method != "none"
            or not client_info.redirect_uris
            or any(str(r) not in self.redirects for r in client_info.redirect_uris)
            or set(client_info.grant_types) - {"authorization_code", "refresh_token"}
            or client_info.response_types != ["code"]
            or not set((client_info.scope or "").split()) <= set(BUSINESS_SCOPES)
        ):
            raise RegistrationError("invalid_client_metadata")
        self.store.put(
            "client",
            client_info.client_id,
            client_info.model_dump(mode="json"),
            time.time() + 365 * 86400,
        )

    async def authorize(self, client, params):
        if (
            params.resource != self.resource
            or not params.scopes
            or not set(params.scopes) <= set(BUSINESS_SCOPES)
            or str(params.redirect_uri) not in self.redirects
        ):
            raise AuthorizeError("invalid_target")
        ticket = secrets.token_urlsafe(32)
        self.store.put(
            "pending",
            ticket,
            {
                "client_id": client.client_id,
                **params.model_dump(mode="json"),
            },
            time.time() + self.LOGIN_TTL,
        )
        return self.resource + "/feishu/start?" + urlencode({"ticket": ticket})

    async def start(self, request):
        value = self.store.get("pending", request.query_params.get("ticket", ""), consume=True)
        if not value:
            return JSONResponse({"error": "invalid_login_state"}, 400)
        state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        value["browser"] = digest(browser)
        self.store.put("feishu_state", state, value, time.time() + self.LOGIN_TTL)
        response = RedirectResponse(self.gateway.authorization_url(state), 302)
        response.set_cookie(
            "mcp_login",
            browser,
            secure=True,
            httponly=True,
            samesite="lax",
            max_age=self.LOGIN_TTL,
            path="/feishu",
        )
        return response

    async def callback(self, request):
        value = self.store.get("feishu_state", request.query_params.get("state", ""), consume=True)
        if not value or value["browser"] != digest(request.cookies.get("mcp_login", "")):
            return JSONResponse({"error": "invalid_login_state"}, 400)
        audit_id, start, outcome = None, time.monotonic(), "denied"
        try:
            if request.query_params.get("error") or not request.query_params.get("code"):
                raise PermissionError("feishu_identity_unavailable")
            audit_id = self.store.quota(
                digest(value["client_id"]), "feishu_identity", minute=10, day=100
            )
            tenant, open_id = await self.gateway.identity(request.query_params["code"])
            outcome = "identity_verified"
            grant = self.store.mapped_grant(self.gateway.app_id, tenant, open_id)
            if (
                not grant
                or not set(value["scopes"]) <= set(grant.scopes)
                or not self.active_user(grant)
            ):
                raise PermissionError("identity_not_authorized")
            value.update(
                subject=grant.id, grant_version=grant.version, authenticated_at=time.time()
            )
            consent = secrets.token_urlsafe(32)
            self.store.put("consent", consent, value, time.time() + self.CODE_TTL)
            scopes = html.escape("、".join(value["scopes"]))
            return HTMLResponse(
                '<!doctype html><meta charset="utf-8"><title>原始股雷达授权</title>'
                "<h1>允许这个已登记客户端读取获准资料？</h1>"
                "<p>仅限已批准的公司、来源和已有报告；不能研究、生成或修改。</p>"
                "<p>客户端：" + html.escape(value["client_id"]) + "</p><p>" + scopes + "</p>"
                '<form method="post" action="/feishu/consent">'
                '<input type="hidden" name="ticket" value="' + consent + '">'
                '<button name="decision" value="allow">同意只读</button>'
                '<button name="decision" value="deny">拒绝</button></form>',
                headers={
                    "Cache-Control": "no-store",
                    "Content-Security-Policy": (
                        "default-src 'none'; form-action 'self'; frame-ancestors 'none'"
                    ),
                },
            )
        except PermissionError:
            return JSONResponse({"error": "identity_not_authorized"}, 403)
        finally:
            if audit_id is not None:
                self.store.finish(audit_id, outcome, time.monotonic() - start)

    async def consent(self, request):
        form = await request.form()
        value = self.store.get("consent", form.get("ticket", ""), consume=True)
        if not value or value["browser"] != digest(request.cookies.get("mcp_login", "")):
            return JSONResponse({"error": "invalid_consent"}, 400)
        try:
            self.current_grant(value)
        except PermissionError:
            return JSONResponse({"error": "identity_not_authorized"}, 403)
        fields = {"state": value.get("state"), "iss": self.resource}
        if form.get("decision") == "allow":
            code = secrets.token_urlsafe(32)
            value["expires_at"] = time.time() + self.CODE_TTL
            self.store.put("code", code, value, value["expires_at"])
            fields["code"] = code
        else:
            fields["error"] = "access_denied"
        response = RedirectResponse(
            construct_redirect_uri(
                value["redirect_uri"], **{k: v for k, v in fields.items() if v is not None}
            ),
            302,
        )
        response.delete_cookie("mcp_login", path="/feishu")
        return response

    async def load_authorization_code(self, client, authorization_code):
        value = self.store.get("code", authorization_code)
        if not value or value["client_id"] != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code,
            **{
                k: value[k]
                for k in (
                    "scopes",
                    "expires_at",
                    "client_id",
                    "code_challenge",
                    "redirect_uri",
                    "redirect_uri_provided_explicitly",
                    "resource",
                    "subject",
                )
            },
        )

    def _issue(self, client_id, value, scopes, *, family, connection):
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        shared = {
            "client_id": client_id,
            "subject": value["subject"],
            "grant_version": value["grant_version"],
            "resource": self.resource,
            "scopes": scopes,
            "family": family,
            "authenticated_at": value["authenticated_at"],
        }
        self.store.put(
            "access",
            access,
            {**shared, "expires_at": int(time.time() + self.ACCESS_TTL)},
            time.time() + self.ACCESS_TTL,
            connection=connection,
        )
        self.store.put(
            "refresh",
            refresh,
            {**shared, "expires_at": int(time.time() + self.REFRESH_TTL)},
            time.time() + self.REFRESH_TTL,
            connection=connection,
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=self.ACCESS_TTL,
            refresh_token=refresh,
            scope=" ".join(scopes),
        )

    async def exchange_authorization_code(self, client, authorization_code):
        # 原子消费阻止两个已通过 SDK PKCE 校验的请求重复交换同一 code。
        try:
            with self.store.transaction() as c:
                value = self.store.get("code", authorization_code.code, consume=True, connection=c)
                if not value or value["client_id"] != client.client_id:
                    raise PermissionError("invalid_grant")
                self.current_grant(value, connection=c)
                return self._issue(
                    client.client_id,
                    value,
                    value["scopes"],
                    family=secrets.token_urlsafe(24),
                    connection=c,
                )
        except PermissionError:
            # SDK TokenError 是 frozen dataclass，不能穿过 generator contextmanager。
            raise TokenError("invalid_grant") from None

    async def load_refresh_token(self, client, refresh_token):
        value = self.store.get("refresh", refresh_token)
        if not value:
            used = self.store.get("refresh", refresh_token, include_used=True)
            if used and used["client_id"] == client.client_id:
                self.store.put("revoked_family", used["family"], {}, time.time() + self.REFRESH_TTL)
            return None
        if (
            value["client_id"] != client.client_id
            or value["resource"] != self.resource
            or self.store.get("revoked_family", value["family"]) is not None
        ):
            return None
        return RefreshToken(
            token=refresh_token,
            **{k: value[k] for k in ("client_id", "scopes", "expires_at", "resource", "subject")},
        )

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        with self.store.transaction() as c:
            value = self.store.get("refresh", refresh_token.token, consume=True, connection=c)
            if value is None:
                reused = self.store.get(
                    "refresh", refresh_token.token, include_used=True, connection=c
                )
                if reused and reused["client_id"] == client.client_id:
                    self.store.put(
                        "revoked_family",
                        reused["family"],
                        {},
                        time.time() + self.REFRESH_TTL,
                        connection=c,
                    )
        # 消费或重放撤销已提交；不能因随后 TokenError 回滚撤销证据。
        if value is None:
            raise TokenError("invalid_grant")
        try:
            with self.store.transaction() as c:
                if (
                    value["client_id"] != client.client_id
                    or self.store.get("revoked_family", value["family"], connection=c) is not None
                ):
                    raise PermissionError("invalid_grant")
                grant = self.current_grant(value, connection=c)
                if not set(scopes) <= set(value["scopes"]) & set(grant.scopes):
                    raise PermissionError("invalid_scope")
                return self._issue(
                    client.client_id, value, scopes, family=value["family"], connection=c
                )
        except PermissionError as error:
            raise TokenError(
                "invalid_scope" if str(error) == "invalid_scope" else "invalid_grant"
            ) from None

    async def load_access_token(self, token):
        value = self.store.get("access", token)
        if (
            not value
            or value["resource"] != self.resource
            or self.store.get("revoked_family", value["family"]) is not None
            or await self.get_client(value["client_id"]) is None
        ):
            return None
        try:
            self.current_grant(value)
        except PermissionError:
            return None
        return AccessToken(
            token=token,
            claims={"grant_version": value["grant_version"]},
            **{k: value[k] for k in ("client_id", "scopes", "expires_at", "resource", "subject")},
        )

    async def revoke_token(self, token):
        kind = "access" if isinstance(token, AccessToken) else "refresh"
        value = self.store.get(kind, token.token, include_used=True)
        if value:
            self.store.put("revoked_family", value["family"], {}, time.time() + self.REFRESH_TTL)
