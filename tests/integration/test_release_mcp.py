import asyncio
import hashlib
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx2
import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.app.config import PersonalEntitlementPolicy, WebResearchPolicy
from backend.app.diagnostic_mcp import create_mcp_app
from backend.app.diagnostics import TOOLS, Deadline
from backend.app.models import CompanyResearchJob
from backend.app.personal_features import create_refresh_request
from backend.app.web_research_service import prepare_pending_research_requests
from tests.integration import test_curated_import as curated
from tests.integration.test_diagnostic_service import diagnostic as diagnostic
from tests.integration.test_incremental_research import initial
from tests.integration.test_release_migration import snapshot
from tests.integration.test_research_matter_storage import ingest

database = curated.database


def test_all_sdk_tools_cursor_boundaries_and_business_content_unchanged(
    diagnostic, database, tmp_path
):
    service, principal = diagnostic
    service.max_calls, service.export_bytes = 200, 10_000_000
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as s:
        user, company = initial(s, tmp_path)
        event, _, _, _ = ingest(
            s, user, company, "示例山海完成1亿元A轮融资。", "https://example.com/sdk"
        )
        create_refresh_request(s, user, PersonalEntitlementPolicy(), company.id)
        user = curated.enter(s)
        prepare_pending_research_requests(
            s, user, WebResearchPolicy(incremental_research_enabled=True)
        )
        curated.enter(s)
        job = s.scalar(
            select(CompanyResearchJob).where(CompanyResearchJob.company_id == company.id)
        )
        job.coverage = {
            "matter_dispositions": [
                {"stage": "extraction", "outcome": "accepted"},
                {"stage": "merge", "outcome": "linked"},
            ]
        }
        job_id, event_id, company_id = job.id, event.id, company.id
        s.commit()
    principal = replace(principal, company_ids=principal.company_ids | {company_id})
    before = snapshot(database.owner)
    token = "synthetic-sdk-closeout"
    credentials = {hashlib.sha256(token.encode()).hexdigest(): principal}
    app = create_mcp_app(service, lambda: credentials)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
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
                    f"http://127.0.0.1:{sock.getsockname()[1]}/mcp", http_client=http
                ) as streams:
                    async with ClientSession(streams[0], streams[1]) as client:
                        await client.initialize()
                        arguments = {
                            "find_company": {"query": "示例"},
                            "get_company_diagnostic": {"company_id": str(company_id)},
                            "get_run_trace": {"run_id": str(job_id)},
                            "get_event_lineage": {"event_id": str(event_id)},
                            "get_diagnostic_summary": {},
                        }
                        for tool in TOOLS:
                            result = await client.call_tool(tool, arguments[tool] | {"limit": 1})
                            assert not result.is_error
                            body = json.loads(result.content[0].text)
                            assert body["items"], (tool, body)
                            assert body["stage_timestamps"] == "not_recorded"
                            cursor = body["next_cursor"]
                            if cursor:
                                for bad in (
                                    cursor + "x",
                                    service.cursor(
                                        {
                                            **service.page(principal, tool, arguments[tool])[1],
                                            "as_of": (
                                                datetime.now(UTC) - timedelta(hours=1)
                                            ).isoformat(),
                                        }
                                    ),
                                ):
                                    rejected = await client.call_tool(
                                        tool, arguments[tool] | {"cursor": bad}
                                    )
                                    assert "diagnostic_request_denied" in rejected.content[0].text
                        for tool, arg in [
                            ("get_company_diagnostic", "company_id"),
                            ("get_run_trace", "run_id"),
                            ("get_event_lineage", "event_id"),
                        ]:
                            rejected = await client.call_tool(tool, {arg: str(uuid4())})
                            assert "diagnostic_request_denied" in rejected.content[0].text
                        first = service.call(
                            principal, "find_company", {"query": "示例", "limit": 1}
                        )
                        cursor = first["next_cursor"]
                        for actor, tool, params in [
                            (replace(principal, id="other"), "find_company", {"query": "示例"}),
                            (principal, "find_company", {"query": "different"}),
                            (principal, "get_diagnostic_summary", {}),
                        ]:
                            with pytest.raises(ValueError, match="invalid_cursor"):
                                service.call(actor, tool, params | {"cursor": cursor})
                        credentials.clear()

        asyncio.run(check())
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()
    assert snapshot(database.owner) == before


def test_deadline_and_cancel_stop_next_query_release_pool_slots_and_reservation(
    diagnostic, monkeypatch
):
    service, principal = diagnostic
    entered = threading.Event()
    completed = []

    def slow(c, *args):
        entered.set()
        c.execute(text("SELECT pg_sleep(0.3)"))
        completed.append("first")
        c.execute(text("SELECT pg_sleep(1)"))
        completed.append("second")
        return [], {}

    monkeypatch.setattr(service, "read", slow)
    for cancel in (False, True):
        entered.clear()
        completed.clear()
        deadline = Deadline(seconds=0.15 if not cancel else 2)
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                service.call, principal, "find_company", {"query": "示例"}, deadline=deadline
            )
            assert entered.wait(1)
            if cancel:
                deadline.cancelled.set()
            with pytest.raises((TimeoutError, DBAPIError)):
                future.result(timeout=2)
        assert time.monotonic() - started < 2
        assert completed == []
        assert service.engine.pool.checkedout() == 0
        assert service.usage[principal.key()][1] == 0
        assert service.slots.acquire(blocking=False)
        assert service.slots.acquire(blocking=False)
        service.slots.release()
        service.slots.release()
