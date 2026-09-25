import hashlib
import time
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from backend.app.diagnostic_mcp import create_mcp_app
from backend.app.diagnostics import TOOLS, DiagnosticService, Principal, build_diagnostic_engine
from scripts.bootstrap_diagnostics import bootstrap
from tests.integration import test_curated_import as curated

database = curated.database


@pytest.fixture
def diagnostic(database):
    if not database.postgres:
        pytest.skip("diagnostic permission validation requires PostgreSQL")
    url = database.owner.url.render_as_string(hide_password=False)
    role = "diag_" + uuid4().hex[:12]
    bootstrap(url, role, "isolated_diagnostic_fixture")
    reader_url = (
        make_url(url)
        .set(username=role, password="isolated_diagnostic_fixture")
        .render_as_string(hide_password=False)
    )
    engine = build_diagnostic_engine(reader_url)
    with database.owner.connect() as c:
        ids = (
            c.execute(
                text(
                    "SELECT id FROM companies WHERE tenant_id IS NULL "
                    "AND visibility_scope='public' LIMIT 3"
                )
            )
            .scalars()
            .all()
        )
    principal = Principal("fixture", frozenset(ids), time.time() + 300)
    service = DiagnosticService(engine, b"fixture_cursor_key_32_characters_only", max_calls=30)
    try:
        yield service, principal
    finally:
        engine.dispose()
        with database.owner.begin() as c:
            c.execute(text(f'DROP OWNED BY "{role}" CASCADE'))
            c.execute(text(f'DROP OWNED BY "{role}_views" CASCADE'))
            c.execute(text(f'DROP ROLE "{role}"'))
            c.execute(text(f'DROP ROLE "{role}_views"'))


def test_role_enforces_readonly_and_scoped_views(diagnostic):
    service, principal = diagnostic
    result = service.call(principal, "find_company", {"query": "示例", "limit": 1})
    assert len(result["items"]) == 1
    assert result["truncated"] and result["next_cursor"]
    other = Principal("other", principal.company_ids, principal.expires_at)
    with pytest.raises(ValueError, match="invalid_cursor"):
        service.call(other, "find_company", {"query": "示例", "cursor": result["next_cursor"]})
    for statement in (
        "UPDATE public.companies SET legal_name='bad'",
        "CREATE TABLE public.bad(id int)",
        "SELECT * FROM public.users",
        "SELECT * FROM public.raw_documents",
        "SELECT * FROM public.investments",
    ):
        with pytest.raises(DBAPIError), service.transaction(principal) as c:
            c.execute(text(statement))
    with service.engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM diagnostic.companies")) == 0
    with service.transaction(Principal("empty", frozenset(), principal.expires_at)) as c:
        assert c.scalar(text("SELECT count(*) FROM diagnostic.companies")) == 0
    with pytest.raises(PermissionError):
        service.call(principal, "get_company_diagnostic", {"company_id": str(uuid4())})
    with pytest.raises(ValueError):
        service.call(principal, "find_company", {"query": "x", "sql": "SELECT 1"})


def test_timeout_releases_connection_and_limits(diagnostic):
    service, principal = diagnostic
    with pytest.raises(DBAPIError), service.transaction(principal) as c:
        c.execute(text("SELECT pg_sleep(4)"))
    assert service.call(principal, "find_company", {"query": "示例"})["items"]
    service.max_calls = 1
    with pytest.raises(PermissionError, match="budget"):
        service.call(principal, "find_company", {"query": "示例"})


def test_streamable_http_auth_tools_and_revocation(diagnostic):
    from fastapi.testclient import TestClient

    service, principal = diagnostic
    token = "local-test-only-token"
    credentials = {hashlib.sha256(token.encode()).hexdigest(): principal}
    app = create_mcp_app(service, lambda: credentials)
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json, text/event-stream",
        "Host": "localhost",
    }
    with TestClient(app) as client:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        assert client.post("/mcp", json=payload).status_code == 401
        response = client.post("/mcp", json=payload, headers=headers)
        assert response.status_code == 200, response.text
        assert {t["name"] for t in response.json()["result"]["tools"]} == set(TOOLS)
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "find_company", "arguments": {"query": "示例"}},
            },
            headers=headers,
        )
        assert response.status_code == 200
        assert not response.json()["result"].get("isError", False), response.text
        assert "diagnostic_schema_version" in response.text, response.text
        credentials.clear()
        assert client.post("/mcp", json=payload, headers=headers).status_code == 401


def test_persistent_budgets_bytes_concurrency_and_expiry(diagnostic, tmp_path, caplog):
    service, principal = diagnostic
    service.budget_path = tmp_path / "budget.json"
    service.max_calls = 1
    service.call(principal, "find_company", {"query": "示例"})
    restarted = DiagnosticService(
        service.engine, service.cursor_key, max_calls=1, budget_path=service.budget_path
    )
    with pytest.raises(PermissionError, match="budget"):
        restarted.call(principal, "find_company", {"query": "示例"})
    with pytest.raises(PermissionError, match="principal"):
        service.call(Principal("expired", principal.company_ids, 1), "find_company", {"query": "x"})
    service.slots.acquire()
    service.slots.acquire()
    try:
        with pytest.raises(ValueError, match="concurrency"):
            service.call(principal, "find_company", {"query": "x"})
    finally:
        service.slots.release()
        service.slots.release()
    tiny = DiagnosticService(service.engine, service.cursor_key, max_bytes=100)
    with pytest.raises(ValueError, match="byte_limit"):
        tiny.call(principal, "find_company", {"query": "示例"})
    assert "local-test-only-token" not in caplog.text


def test_real_sdk_client_over_loopback_http(diagnostic):
    import asyncio
    import socket
    import threading

    import httpx2
    import uvicorn
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    service, principal = diagnostic
    token = "fixture-sdk-only"
    app = create_mcp_app(service, lambda: {hashlib.sha256(token.encode()).hexdigest(): principal})
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.01)
        assert server.started

        async def check():
            async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + token}) as http:
                async with streamable_http_client(
                    f"http://127.0.0.1:{port}/mcp", http_client=http
                ) as streams:
                    async with ClientSession(streams[0], streams[1]) as client:
                        await client.initialize()
                        assert {t.name for t in (await client.list_tools()).tools} == set(TOOLS)
                        result = await client.call_tool("find_company", {"query": "示例"})
                        assert not result.is_error

        asyncio.run(check())
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()


def test_simulated_tunnel_reads_no_business_writes_and_disconnect_keeps_api(diagnostic, database):
    from fastapi.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from backend.app.config import Settings
    from backend.app.main import create_app

    service, principal = diagnostic
    token = "synthetic-tunnel-token"
    mcp = create_mcp_app(service, lambda: {hashlib.sha256(token.encode()).hexdigest(): principal})
    site = create_app(
        Settings(
            database_url=database.app.url.render_as_string(hide_password=False),
            app_mode="demo",
            external_calls_enabled=False,
            paid_api_calls_enabled=False,
            auto_refresh_enabled=False,
        )
    )
    active = True
    tables = (
        "company_research_jobs",
        "personal_event_view_receipts",
        "personal_company_reports",
        "usage_ledger",
        "refresh_jobs",
    )

    def counts():
        with database.owner.connect() as c:
            return {table: c.scalar(text(f"SELECT count(*) FROM {table}")) for table in tables}

    before = counts()
    with TestClient(mcp) as internal, TestClient(site) as api:

        async def forward(request):
            if not active:
                return JSONResponse({"error": "tunnel_disconnected"}, status_code=503)
            result = internal.post(
                "/mcp",
                json=await request.json(),
                headers={
                    "Authorization": request.headers.get("authorization", ""),
                    "Host": "localhost",
                    "Accept": "application/json, text/event-stream",
                },
            )
            return JSONResponse(result.json(), status_code=result.status_code)

        proxy = Starlette(routes=[Route("/mcp", forward, methods=["POST"])])
        with TestClient(proxy) as external:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "find_company", "arguments": {"query": "示例"}},
            }
            assert external.post("/mcp", json=payload).status_code == 401
            result = external.post(
                "/mcp", json=payload, headers={"Authorization": "Bearer " + token}
            )
            assert result.status_code == 200 and "diagnostic_schema_version" in result.text
            active = False
            assert external.post("/mcp", json=payload).status_code == 503
            assert api.get("/health").status_code == 200
    assert counts() == before
    site.state.engine.dispose()
