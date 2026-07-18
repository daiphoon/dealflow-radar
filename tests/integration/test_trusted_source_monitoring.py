from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.config import SourceMonitoringPolicy
from backend.app.demo import (
    ALPHA_TENANT_ID,
    ALPHA_USER_ID,
    BETA_TENANT_ID,
    BETA_USER_ID,
    NO_ACCESS_USER_ID,
    demo_uuid,
)
from backend.app.models import (
    CandidateDocument,
    Company,
    Event,
    Role,
    SourceCheckRun,
    TrustedSource,
    UsageLedger,
    User,
    UserRoleAssignment,
)
from backend.app.source_fetcher import TrustedSourceFetcher
from backend.app.source_monitoring import run_trusted_source_worker_once

ALPHA_HEADERS = {"X-Demo-User-Id": str(ALPHA_USER_ID)}
BETA_HEADERS = {"X-Demo-User-Id": str(BETA_USER_ID)}
NO_ACCESS_HEADERS = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}
PUBLIC_ADDRESS = {"93.184.216.34"}
VERIFIED_COMPANY_ID = demo_uuid("company-示例星河科技一号有限公司")


def _grant_platform_admin(app: FastAPI, user_id=ALPHA_USER_ID) -> None:
    with app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.code == "platform_admin"))
        assert role is not None
        if not session.scalar(
            select(func.count())
            .select_from(UserRoleAssignment)
            .where(
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.role_id == role.id,
            )
        ):
            session.add(
                UserRoleAssignment(
                    id=uuid4(),
                    user_id=user_id,
                    role_id=role.id,
                    scope_id=None,
                    valid_until=None,
                )
            )
            session.commit()


def _create_source(
    client: TestClient,
    *,
    company_id=VERIFIED_COMPANY_ID,
    source_type: str = "single_page",
    start_url: str = "https://example.com/news",
    list_path_prefix: str | None = None,
) -> dict[str, object]:
    response = client.post(
        "/api/v1/trusted-sources",
        headers=ALPHA_HEADERS,
        json={
            "company_id": str(company_id),
            "name": "受控官网来源",
            "source_type": source_type,
            "root_domain": "example.com",
            "start_url": start_url,
            "list_path_prefix": list_path_prefix,
            "access_basis": "企业官网公开页面，低频人工触发检查",
            "license_status": "public_access",
            "check_frequency_minutes": 10080,
            "content_retention_policy": "minimal_excerpt",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _queue(client: TestClient, source_id: str, *, dry_run: bool) -> dict[str, object]:
    response = client.post(
        f"/api/v1/trusted-sources/{source_id}/runs",
        headers=ALPHA_HEADERS,
        json={"dry_run": dry_run},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _worker_user(app: FastAPI, user_id=ALPHA_USER_ID) -> User:
    with app.state.session_factory() as session:
        user = session.get(User, user_id)
        assert user is not None
        session.expunge(user)
        return user


def _fetcher_factory(handler):
    def factory(policy: SourceMonitoringPolicy) -> TrustedSourceFetcher:
        return TrustedSourceFetcher(
            policy,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            resolver=lambda _host, _port: PUBLIC_ADDRESS,
        )

    return factory


def _robots_or_html(
    body: str,
    *,
    etag: str = '"v1"',
    status: int = 200,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                text="User-agent: *\nAllow: /\n",
            )
        return httpx.Response(
            status,
            headers={"content-type": "text/html", "etag": etag},
            text=body,
        )

    return handler


def test_source_configuration_is_tenant_private_and_rejects_unsafe_urls(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    source = _create_source(client)
    assert source["company_identity_status"] == "verified"
    assert source["list_path_prefix"] is None

    list_source = _create_source(
        client,
        source_type="list_page",
        start_url="https://example.com/list",
        list_path_prefix="/Companynews/",
    )
    assert list_source["list_path_prefix"] == "/Companynews"
    with migrated_app.state.session_factory() as session:
        source_row = session.get(TrustedSource, UUID(str(list_source["id"])))
        assert source_row is not None
        source_row.last_etag = '"old-list"'
        source_row.last_modified = "Thu, 16 Jul 2026 08:00:00 GMT"
        source_row.last_content_hash = "a" * 64
        session.commit()
    updated = client.patch(
        f"/api/v1/trusted-sources/{list_source['id']}",
        headers=ALPHA_HEADERS,
        json={"list_path_prefix": "/news/detail"},
    )
    assert updated.status_code == 200
    assert updated.json()["list_path_prefix"] == "/news/detail"
    with migrated_app.state.session_factory() as session:
        source_row = session.get(TrustedSource, UUID(str(list_source["id"])))
        assert source_row is not None
        assert source_row.last_etag is None
        assert source_row.last_modified is None
        assert source_row.last_content_hash is None

    invalid_prefix = client.post(
        "/api/v1/trusted-sources",
        headers=ALPHA_HEADERS,
        json={
            "company_id": str(VERIFIED_COMPANY_ID),
            "name": "不安全列表范围",
            "source_type": "list_page",
            "root_domain": "example.com",
            "start_url": "https://example.com/unsafe-list",
            "list_path_prefix": "/../private",
            "access_basis": "企业官网公开页面，低频人工触发检查",
            "license_status": "public_access",
            "content_retention_policy": "metadata_only",
        },
    )
    assert invalid_prefix.status_code == 422
    assert source["visibility_scope"] == "organization_private"

    personal = client.get("/api/v1/trusted-sources", headers=NO_ACCESS_HEADERS)
    personal_candidates = client.get("/api/v1/candidate-documents", headers=NO_ACCESS_HEADERS)
    beta = client.get("/api/v1/trusted-sources", headers=BETA_HEADERS)
    assert personal.status_code == 403
    assert personal_candidates.status_code == 403
    assert beta.status_code == 403

    for unsafe_url, root_domain in (
        ("http://example.com/news", "example.com"),
        ("https://localhost/news", "localhost"),
        ("https://10.0.0.1/news", "10.0.0.1"),
        ("https://evil.example/news", "example.com"),
    ):
        response = client.post(
            "/api/v1/trusted-sources",
            headers=ALPHA_HEADERS,
            json={
                "company_id": str(VERIFIED_COMPANY_ID),
                "name": "不安全来源",
                "source_type": "single_page",
                "root_domain": root_domain,
                "start_url": unsafe_url,
                "access_basis": "测试必须拒绝",
                "license_status": "public_access",
            },
        )
        assert response.status_code == 422

    unclear_excerpt = client.post(
        "/api/v1/trusted-sources",
        headers=ALPHA_HEADERS,
        json={
            "company_id": str(VERIFIED_COMPANY_ID),
            "name": "许可不明来源",
            "source_type": "single_page",
            "root_domain": "example.com",
            "start_url": "https://example.com/license-unclear",
            "access_basis": "公开可访问但复用许可尚未确认",
            "license_status": "unclear",
            "content_retention_policy": "minimal_excerpt",
        },
    )
    assert unclear_excerpt.status_code == 422


def test_dry_run_never_uses_network_or_creates_candidates(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    source = _create_source(client)
    queued = _queue(client, str(source["id"]), dry_run=True)
    assert queued["status"] == "queued"

    with migrated_app.state.session_factory() as session:
        result = run_trusted_source_worker_once(
            session,
            _worker_user(migrated_app),
            migrated_app.state.settings,
            fetcher_factory=lambda _policy: (_ for _ in ()).throw(
                AssertionError("dry-run must not construct a fetcher")
            ),
        )
    assert result.status == "dry_run_completed"
    assert result.external_calls == 0
    with migrated_app.state.session_factory() as session:
        run = session.get(SourceCheckRun, result.run_id)
        ledger = session.scalar(
            select(UsageLedger).where(UsageLedger.operation == "trusted_source_check")
        )
        assert run is not None and run.request_log == []
        assert ledger is not None and ledger.external_calls == 0
        assert ledger.input_tokens == 0 and ledger.output_tokens == 0
        assert ledger.estimated_cost == Decimal("0")
        assert session.scalar(select(func.count()).select_from(CandidateDocument)) == 0


def test_real_run_fails_closed_when_external_switches_are_disabled(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    source = _create_source(client)
    _queue(client, str(source["id"]), dry_run=False)
    with migrated_app.state.session_factory() as session:
        result = run_trusted_source_worker_once(
            session,
            _worker_user(migrated_app),
            migrated_app.state.settings,
            fetcher_factory=lambda _policy: (_ for _ in ()).throw(
                AssertionError("disabled run must not construct the supplied fetcher")
            ),
        )
    assert result.status == "failed"
    assert result.error_code == "external_calls_disabled"
    assert result.external_calls == 0
    with migrated_app.state.session_factory() as session:
        run = session.get(SourceCheckRun, result.run_id)
        assert run is not None
        assert run.request_count == 0
        assert run.paid_api_calls == 0
        assert run.input_tokens == 0 and run.output_tokens == 0
        assert run.estimated_cost == Decimal("0")
        assert session.scalar(select(func.count()).select_from(CandidateDocument)) == 0


def test_new_unchanged_changed_and_handoff_do_not_create_events(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    source = _create_source(client)
    settings = replace(
        migrated_app.state.settings,
        external_calls_enabled=True,
        trusted_source_calls_enabled=True,
        source_monitoring_policy=replace(
            migrated_app.state.settings.source_monitoring_policy,
            min_request_interval_ms=0,
            retry_limit=0,
        ),
    )
    user = _worker_user(migrated_app)
    with migrated_app.state.session_factory() as session:
        event_count = session.scalar(select(func.count()).select_from(Event))

    _queue(client, str(source["id"]), dry_run=False)
    with migrated_app.state.session_factory() as session:
        first = run_trusted_source_worker_once(
            session,
            user,
            settings,
            now=datetime(2026, 7, 17, 8, 0, tzinfo=UTC),
            fetcher_factory=_fetcher_factory(
                _robots_or_html("<title>首条官网动态</title><p>公开内容一</p>")
            ),
        )
    assert first.status == "completed"
    assert first.new_count == 1
    assert first.external_calls == 2

    def unchanged_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, headers={"content-type": "text/plain"})
        assert request.headers["if-none-match"] == '"v1"'
        return httpx.Response(304, headers={"etag": '"v1"'})

    _queue(client, str(source["id"]), dry_run=False)
    with migrated_app.state.session_factory() as session:
        second = run_trusted_source_worker_once(
            session,
            user,
            settings,
            now=datetime(2026, 7, 17, 9, 0, tzinfo=UTC),
            fetcher_factory=_fetcher_factory(unchanged_handler),
        )
    assert second.status == "completed"
    assert second.unchanged_count == 1

    _queue(client, str(source["id"]), dry_run=False)
    with migrated_app.state.session_factory() as session:
        third = run_trusted_source_worker_once(
            session,
            user,
            settings,
            now=datetime(2026, 7, 17, 10, 0, tzinfo=UTC),
            fetcher_factory=_fetcher_factory(
                _robots_or_html("<title>首条官网动态更新</title><p>公开内容二</p>", etag='"v2"')
            ),
        )
    assert third.status == "completed"
    assert third.changed_count == 1

    candidates = client.get("/api/v1/candidate-documents", headers=ALPHA_HEADERS)
    assert candidates.status_code == 200
    assert len(candidates.json()) == 2
    changed = next(item for item in candidates.json() if item["change_type"] == "changed")
    decision = client.post(
        f"/api/v1/candidate-documents/{changed['id']}/decision",
        headers=ALPHA_HEADERS,
        json={"decision": "worth_research", "reason": "需要进入现有人工研究流程判断"},
    )
    assert decision.status_code == 200
    assert decision.json()["event_created"] is False
    assert decision.json()["shared_fact_created"] is False
    assert decision.json()["handoff_payload"]["workflow"] == "manual_research_import"

    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Event)) == event_count
        rows = list(session.scalars(select(CandidateDocument)))
        assert len(rows) == 2
        assert any(row.previous_candidate_id is not None for row in rows)
        assert session.scalar(select(func.sum(UsageLedger.input_tokens))) == 0
        assert session.scalar(select(func.sum(UsageLedger.output_tokens))) == 0
        assert session.scalar(select(func.sum(UsageLedger.estimated_cost))) == Decimal("0")


def test_404_is_audited_without_candidate_or_fact(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    source = _create_source(client, start_url="https://example.com/missing")
    settings = replace(
        migrated_app.state.settings,
        external_calls_enabled=True,
        trusted_source_calls_enabled=True,
        source_monitoring_policy=replace(
            migrated_app.state.settings.source_monitoring_policy,
            min_request_interval_ms=0,
            retry_limit=0,
        ),
    )
    _queue(client, str(source["id"]), dry_run=False)
    with migrated_app.state.session_factory() as session:
        result = run_trusted_source_worker_once(
            session,
            _worker_user(migrated_app),
            settings,
            fetcher_factory=_fetcher_factory(_robots_or_html("not found", status=404)),
        )
    assert result.status == "failed"
    assert result.error_code == "http_not_found"
    assert result.external_calls == 2
    with migrated_app.state.session_factory() as session:
        source_row = session.get(TrustedSource, UUID(str(source["id"])))
        assert source_row is not None
        assert source_row.last_http_status == 404
        assert source_row.consecutive_failures == 1
        assert session.scalar(select(func.count()).select_from(CandidateDocument)) == 0
        assert session.scalar(select(func.count()).select_from(Event)) == 0


@pytest.mark.parametrize("decision", ["irrelevant", "duplicate", "source_unavailable"])
def test_manual_non_handoff_candidate_decisions_do_not_create_events(
    client: TestClient,
    migrated_app: FastAPI,
    decision: str,
) -> None:
    _grant_platform_admin(migrated_app)
    source = _create_source(client)
    settings = replace(
        migrated_app.state.settings,
        external_calls_enabled=True,
        trusted_source_calls_enabled=True,
        source_monitoring_policy=replace(
            migrated_app.state.settings.source_monitoring_policy,
            min_request_interval_ms=0,
            retry_limit=0,
        ),
    )
    _queue(client, str(source["id"]), dry_run=False)
    with migrated_app.state.session_factory() as session:
        result = run_trusted_source_worker_once(
            session,
            _worker_user(migrated_app),
            settings,
            fetcher_factory=_fetcher_factory(
                _robots_or_html("<title>待判断页面</title><p>公开内容</p>")
            ),
        )
    assert result.new_count == 1
    candidate = client.get("/api/v1/candidate-documents", headers=ALPHA_HEADERS).json()[0]
    response = client.post(
        f"/api/v1/candidate-documents/{candidate['id']}/decision",
        headers=ALPHA_HEADERS,
        json={"decision": decision, "reason": "管理员记录本次候选判断结果"},
    )
    assert response.status_code == 200
    assert response.json()["processing_status"] == decision
    assert response.json()["event_created"] is False
    assert response.json()["shared_fact_created"] is False
    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Event)) == 0


def test_same_content_from_two_list_links_creates_one_candidate(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    source = _create_source(
        client,
        source_type="list_page",
        start_url="https://example.com/news/list",
    )
    settings = replace(
        migrated_app.state.settings,
        external_calls_enabled=True,
        trusted_source_calls_enabled=True,
        source_monitoring_policy=replace(
            migrated_app.state.settings.source_monitoring_policy,
            min_request_interval_ms=0,
            retry_limit=0,
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, headers={"content-type": "text/plain"})
        if request.url.path == "/news/list":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    '<title>动态列表</title><a href="/news/item-1">第一页</a>'
                    '<a href="/news/item-2">镜像页</a>'
                ),
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<title>相同正文</title><p>完全相同的公开内容</p>",
        )

    _queue(client, str(source["id"]), dry_run=False)
    with migrated_app.state.session_factory() as session:
        result = run_trusted_source_worker_once(
            session,
            _worker_user(migrated_app),
            settings,
            fetcher_factory=_fetcher_factory(handler),
        )
    assert result.status == "completed"
    assert result.new_count == 1
    assert result.duplicate_count == 1
    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CandidateDocument)) == 1


def test_platform_admin_role_does_not_cross_tenant_source_scope(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    _grant_platform_admin(migrated_app, BETA_USER_ID)
    source = _create_source(client)

    beta_list = client.get("/api/v1/trusted-sources", headers=BETA_HEADERS)
    assert beta_list.status_code == 200
    assert beta_list.json() == []
    beta_queue = client.post(
        f"/api/v1/trusted-sources/{source['id']}/runs",
        headers=BETA_HEADERS,
        json={"dry_run": True},
    )
    assert beta_queue.status_code == 404

    with migrated_app.state.session_factory() as session:
        beta_company = Company(
            tenant_id=BETA_TENANT_ID,
            legal_name="乙租户私有监测公司",
            credit_code=None,
            registered_region="虚构地区",
            official_website="https://beta.example.invalid",
            identity_status="verified",
            visibility_scope="tenant",
        )
        session.add(beta_company)
        session.commit()
        beta_company_id = beta_company.id
    cross_tenant_create = client.post(
        "/api/v1/trusted-sources",
        headers=ALPHA_HEADERS,
        json={
            "company_id": str(beta_company_id),
            "name": "禁止跨租户来源",
            "source_type": "single_page",
            "root_domain": "example.com",
            "start_url": "https://example.com/cross-tenant",
            "access_basis": "跨租户测试",
            "license_status": "public_access",
        },
    )
    assert cross_tenant_create.status_code == 404


def test_unresolved_company_candidate_stays_unresolved_and_private(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        company = Company(
            tenant_id=ALPHA_TENANT_ID,
            legal_name="身份待消歧测试公司",
            credit_code=None,
            registered_region=None,
            official_website=None,
            identity_status="unresolved",
            visibility_scope="tenant",
        )
        session.add(company)
        session.commit()
        company_id = company.id
    source = _create_source(
        client,
        company_id=company_id,
        start_url="https://example.com/unresolved",
    )
    settings = replace(
        migrated_app.state.settings,
        external_calls_enabled=True,
        trusted_source_calls_enabled=True,
        source_monitoring_policy=replace(
            migrated_app.state.settings.source_monitoring_policy,
            min_request_interval_ms=0,
            retry_limit=0,
        ),
    )
    _queue(client, str(source["id"]), dry_run=False)
    with migrated_app.state.session_factory() as session:
        result = run_trusted_source_worker_once(
            session,
            _worker_user(migrated_app),
            settings,
            fetcher_factory=_fetcher_factory(
                _robots_or_html("<title>歧义公司页面</title><p>只形成候选</p>")
            ),
        )
    assert result.new_count == 1
    candidates = client.get("/api/v1/candidate-documents", headers=ALPHA_HEADERS).json()
    candidate = next(item for item in candidates if item["company_id"] == str(company_id))
    assert candidate["identity_status_at_discovery"] == "unresolved"
    assert candidate["visibility_scope"] == "organization_private"
    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Event)) == 0
