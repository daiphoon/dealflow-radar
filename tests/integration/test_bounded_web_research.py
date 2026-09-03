from __future__ import annotations

import hashlib
from datetime import timedelta

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.config import PersonalEntitlementPolicy, WebResearchPolicy
from backend.app.demo import (
    ALPHA_USER_ID,
    BETA_USER_ID,
    DEMO_SHARED_COMPANY_CREDIT_CODE,
    NO_ACCESS_USER_ID,
    demo_uuid,
)
from backend.app.models import (
    PLATFORM_SHARED_SCOPE,
    Company,
    CompanyResearchJob,
    Event,
    PersonalCompanyRequest,
    RawDocument,
    Role,
    User,
    UserRoleAssignment,
    WebSearchCacheEntry,
    utc_now,
)
from backend.app.personal_features import (
    cancel_personal_company_request,
    create_refresh_request,
)
from backend.app.source_fetcher import TrustedSourceFetcher
from backend.app.web_research_service import (
    SEARCH_GROUPS,
    prepare_pending_research_requests,
    run_web_research_worker_once,
)
from backend.app.web_search import MockSearchProvider, SearchResult

ALPHA_HEADERS = {"X-Demo-User-Id": str(ALPHA_USER_ID)}
SHARED_COMPANY_ID = demo_uuid("company-示例星河科技一号有限公司")
SHARED_COMPANY_NAME = "示例星河科技一号有限公司"


def _grant_platform_admin(app: FastAPI) -> None:
    with app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.code == "platform_admin"))
        assert role is not None
        if not session.scalar(
            select(UserRoleAssignment).where(
                UserRoleAssignment.user_id == ALPHA_USER_ID,
                UserRoleAssignment.role_id == role.id,
            )
        ):
            session.add(
                UserRoleAssignment(
                    id=demo_uuid("assignment-alpha-r3-platform-admin"),
                    user_id=ALPHA_USER_ID,
                    role_id=role.id,
                    scope_id=None,
                    valid_until=None,
                )
            )
            session.commit()


def _query(terms: str) -> str:
    return f'"{SHARED_COMPANY_NAME}" {terms}'


def _result(url: str, title: str, snippet: str) -> SearchResult:
    return SearchResult(
        provider_record_id=hashlib.sha256(url.encode()).hexdigest()[:12],
        title=title,
        url=url,
        snippet=snippet,
        source_name="示例公开来源",
        published_at="2026-09-01",
    )


class RecordingFetcherFactory:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def __call__(self, policy):
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(str(request.url))
            if request.url.path == "/robots.txt":
                return httpx.Response(404, headers={"content-type": "text/plain"})
            title = "示例公司完成融资" if request.url.path.endswith("1") else "示例公司中标"
            body = (
                "<html><head><title>"
                f"{title}</title></head><body>{SHARED_COMPANY_NAME}"
                f"（{DEMO_SHARED_COMPANY_CREDIT_CODE}）公告：{title}，相关事项正在推进。"
                "</body></html>"
            ).encode()
            return httpx.Response(200, content=body, headers={"content-type": "text/html"})

        return TrustedSourceFetcher(
            policy,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            allow_private_test_hosts=True,
        )


def _providers() -> tuple[MockSearchProvider, MockSearchProvider]:
    primary = MockSearchProvider(
        "baidu",
        {
            _query(SEARCH_GROUPS[0][1]): [
                _result(
                    "https://news.example.com/article-1",
                    f"{SHARED_COMPANY_NAME}完成融资",
                    f"{SHARED_COMPANY_NAME}披露融资进展。",
                )
            ],
            _query(SEARCH_GROUPS[1][1]): [
                _result(
                    "https://notice.example.com/article-2",
                    f"{SHARED_COMPANY_NAME}中标公告",
                    f"{SHARED_COMPANY_NAME}成为项目中标人。",
                )
            ],
        },
    )
    return primary, MockSearchProvider("bocha")


def _drain(
    app: FastAPI,
    providers: dict[str, MockSearchProvider],
    fetcher_factory: RecordingFetcherFactory,
    *,
    limit: int = 20,
) -> list[str]:
    statuses: list[str] = []
    for _ in range(limit):
        with app.state.session_factory() as session:
            worker = session.get(User, ALPHA_USER_ID)
            assert worker is not None
            result = run_web_research_worker_once(
                session,
                worker,
                providers,
                WebResearchPolicy(),
                fetcher_factory=fetcher_factory,
            )
        statuses.append(result.status)
        if result.status == "idle":
            break
    return statuses


def test_shared_job_cache_replay_and_evidence_are_deduplicated(
    migrated_app: FastAPI,
    client: TestClient,
) -> None:
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        alpha = session.get(User, ALPHA_USER_ID)
        beta = session.get(User, BETA_USER_ID)
        assert company is not None and alpha is not None and beta is not None
        create_refresh_request(session, alpha, PersonalEntitlementPolicy(), company.id)
        create_refresh_request(session, beta, PersonalEntitlementPolicy(), company.id)

    primary, fallback = _providers()
    fetcher_factory = RecordingFetcherFactory()
    statuses = _drain(
        migrated_app,
        {"baidu": primary, "bocha": fallback},
        fetcher_factory,
    )
    assert statuses[-1] == "idle"
    assert len(primary.calls) == 2
    assert fallback.calls == []
    assert len(fetcher_factory.requests) == 4

    with migrated_app.state.session_factory() as session:
        jobs = list(session.scalars(select(CompanyResearchJob)))
        requests = list(
            session.scalars(
                select(PersonalCompanyRequest).where(
                    PersonalCompanyRequest.owner_user_id.in_((ALPHA_USER_ID, BETA_USER_ID))
                )
            )
        )
        assert len(jobs) == 1
        assert {request.research_job_id for request in requests} == {jobs[0].id}
        assert {request.status for request in requests} == {"completed"}
        assert jobs[0].input_tokens == 0
        assert jobs[0].output_tokens == 0
        assert session.scalar(select(func.count()).select_from(WebSearchCacheEntry)) == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(RawDocument)
                .where(
                    RawDocument.visibility_scope == "system_restricted",
                    RawDocument.canonical_url.like("https://%.example.com/%"),
                )
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(
                    Event.company_id == SHARED_COMPANY_ID,
                    Event.fingerprint_version == "web-v1",
                    Event.status == "candidate",
                    Event.publication_route == "unconfirmed_lead",
                    Event.visibility_scope == PLATFORM_SHARED_SCOPE,
                )
            )
            == 2
        )

        third_user = session.get(User, NO_ACCESS_USER_ID)
        company = session.get(Company, SHARED_COMPANY_ID)
        assert third_user is not None and company is not None
        create_refresh_request(session, third_user, PersonalEntitlementPolicy(), company.id)

    replay_statuses = _drain(
        migrated_app,
        {"baidu": primary, "bocha": fallback},
        fetcher_factory,
    )
    assert replay_statuses[-1] == "idle"
    assert len(primary.calls) == 2
    assert fallback.calls == []
    assert len(fetcher_factory.requests) == 4
    with migrated_app.state.session_factory() as session:
        replay_job = session.scalar(
            select(CompanyResearchJob)
            .where(CompanyResearchJob.created_by_user_id == NO_ACCESS_USER_ID)
            .order_by(CompanyResearchJob.created_at.desc())
        )
        assert replay_job is not None
        assert replay_job.status == "completed"
        assert replay_job.external_calls == 0
        event_count = session.scalar(
            select(func.count()).select_from(Event).where(Event.fingerprint_version == "web-v1")
        )
        assert event_count == 2

    personal_detail = client.get(
        f"/api/v1/companies/{SHARED_COMPANY_ID}",
        headers={"X-Demo-User-Id": str(NO_ACCESS_USER_ID)},
    )
    assert personal_detail.status_code == 200
    detail = personal_detail.json()
    bounded_leads = [
        event
        for event in detail["platform_unconfirmed_leads"]
        if event["publication_policy_version"] == "bounded-web-v1"
    ]
    assert len(bounded_leads) == 2
    assert all(event["evidence"] for event in bounded_leads)
    assert detail["investments"] == []
    assert detail["private_events"] == []


def test_cancel_after_primary_stops_fallback_and_persists_status(
    migrated_app: FastAPI,
    client: TestClient,
) -> None:
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        request_out = create_refresh_request(
            session,
            owner,
            PersonalEntitlementPolicy(),
            company.id,
        )
        request_id = request_out.id

    class CancellingProvider(MockSearchProvider):
        def search(self, request):
            response = super().search(request)
            with migrated_app.state.session_factory() as cancelling_session:
                owner = cancelling_session.get(User, NO_ACCESS_USER_ID)
                assert owner is not None
                cancel_personal_company_request(
                    cancelling_session,
                    owner,
                    request_id,
                    reason="用户发现公司输入错误",
                )
            return response

    query = _query(SEARCH_GROUPS[0][1])
    primary = CancellingProvider(
        "baidu",
        {
            query: [
                _result(
                    "https://news.example.com/cancelled",
                    f"{SHARED_COMPANY_NAME}融资线索",
                    f"{SHARED_COMPANY_NAME}融资线索。",
                )
            ]
        },
    )
    fallback = MockSearchProvider("bocha")
    with migrated_app.state.session_factory() as session:
        worker = session.get(User, ALPHA_USER_ID)
        assert worker is not None
        result = run_web_research_worker_once(
            session,
            worker,
            {"baidu": primary, "bocha": fallback},
            WebResearchPolicy(),
            fetcher_factory=RecordingFetcherFactory(),
        )
    assert result.status == "cancelled"
    assert len(primary.calls) == 1
    assert fallback.calls == []
    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(WebSearchCacheEntry)) == 1
        request = session.get(PersonalCompanyRequest, request_id)
        assert request is not None and request.status == "cancelled"

    response = client.get(
        "/api/v1/me/company-requests",
        headers={"X-Demo-User-Id": str(NO_ACCESS_USER_ID)},
    )
    assert response.status_code == 200
    restored = next(item for item in response.json() if item["id"] == str(request_id))
    assert restored["status"] == "cancelled"
    assert restored["cancellation_reason"] == "用户发现公司输入错误"


def test_bocha_is_called_only_when_baidu_has_no_subject_match(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)

    query = _query(SEARCH_GROUPS[0][1])
    primary = MockSearchProvider(
        "baidu",
        {
            query: [
                _result(
                    "https://news.example.com/unrelated",
                    "另一家公司的融资消息",
                    "该页面与目标工商主体无关。",
                )
            ]
        },
    )
    fallback = MockSearchProvider(
        "bocha",
        {
            query: [
                _result(
                    "https://notice.example.com/matched",
                    f"{SHARED_COMPANY_NAME}中标公告",
                    f"{SHARED_COMPANY_NAME}成为中标候选人。",
                )
            ]
        },
    )
    with migrated_app.state.session_factory() as session:
        worker = session.get(User, ALPHA_USER_ID)
        assert worker is not None
        result = run_web_research_worker_once(
            session,
            worker,
            {"baidu": primary, "bocha": fallback},
            WebResearchPolicy(),
            fetcher_factory=RecordingFetcherFactory(),
        )
    assert result.status == "partial"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        candidates = job.coverage["candidates"]
        assert [candidate["url"] for candidate in candidates] == [
            "https://notice.example.com/matched"
        ]
        assert candidates[0]["discovered_by"] == ["bocha"]
        assert (
            session.scalar(
                select(func.count()).select_from(Event).where(Event.fingerprint_version == "web-v1")
            )
            == 0
        )


def test_admin_links_only_exact_verified_shared_identity(
    migrated_app: FastAPI,
    client: TestClient,
) -> None:
    _grant_platform_admin(migrated_app)
    new_credit_code = "91310000MA1K00008X"
    with migrated_app.state.session_factory() as session:
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert owner is not None
        request = PersonalCompanyRequest(
            owner_user_id=owner.id,
            request_type="inclusion",
            company_id=None,
            requested_name="示例新收录公司有限公司",
            requested_credit_code=new_credit_code,
            target_key=f"credit:{new_credit_code}",
            status="pending",
        )
        company = Company(
            tenant_id=None,
            credit_code=new_credit_code,
            legal_name="示例新收录公司有限公司",
            registered_region="虚构省甲市",
            identity_status="verified",
            identity_verification_basis="official_government",
            visibility_scope="public",
        )
        session.add_all([request, company])
        session.commit()
        request_id = request.id
        company_id = company.id

    approved = client.post(
        f"/api/v1/platform/company-requests/{request_id}/approve-research",
        headers=ALPHA_HEADERS,
        json={"reason": "已核对工商全称和信用代码"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "research_queued"
    assert approved.json()["company_id"] == str(company_id)

    with migrated_app.state.session_factory() as session:
        request = session.get(PersonalCompanyRequest, request_id)
        assert request is not None
        assert request.resolved_credit_code == new_credit_code
        assert request.research_job_id is None


def test_queued_wait_time_does_not_consume_execution_budget(migrated_app: FastAPI) -> None:
    _grant_platform_admin(migrated_app)
    primary, fallback = _providers()
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        worker = session.get(User, ALPHA_USER_ID)
        assert company is not None and owner is not None and worker is not None
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)
        prepare_pending_research_requests(session, worker, WebResearchPolicy())
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        job.created_at = utc_now() - timedelta(hours=1)
        session.commit()

    with migrated_app.state.session_factory() as session:
        worker = session.get(User, ALPHA_USER_ID)
        assert worker is not None
        result = run_web_research_worker_once(
            session,
            worker,
            {"baidu": primary, "bocha": fallback},
            WebResearchPolicy(max_elapsed_seconds=1),
            fetcher_factory=RecordingFetcherFactory(),
        )
    assert result.status == "partial"
    assert result.stage == f"search:{SEARCH_GROUPS[1][0]}"
    assert len(primary.calls) == 1
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        assert "started_at" in job.coverage
        assert job.coverage.get("stop_reason") is None


def test_cross_company_document_cache_requires_current_identity_mention(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    second_company = Company(
        tenant_id=None,
        credit_code="91310000MA1K00009R",
        legal_name="示例远海科技二号有限公司",
        registered_region="虚构省乙市",
        identity_status="verified",
        identity_verification_basis="official_government",
        visibility_scope="public",
    )
    with migrated_app.state.session_factory() as session:
        alpha_company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert alpha_company is not None and owner is not None
        session.add(second_company)
        session.commit()
        second_company_id = second_company.id
        second_company_name = second_company.legal_name
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), alpha_company.id)

    shared_url = "https://news.example.com/shared-page"
    primary = MockSearchProvider(
        "baidu",
        {
            _query(SEARCH_GROUPS[0][1]): [
                _result(
                    shared_url,
                    f"{SHARED_COMPANY_NAME}完成融资",
                    f"{SHARED_COMPANY_NAME}披露融资进展。",
                )
            ],
            _query(SEARCH_GROUPS[1][1]): [],
        },
    )
    fallback = MockSearchProvider("bocha")
    fetcher = RecordingFetcherFactory()
    _drain(migrated_app, {"baidu": primary, "bocha": fallback}, fetcher)

    def second_query(terms: str) -> str:
        return f'"{second_company_name}" {terms}'

    second_result = SearchResult(
        provider_record_id="second-company-result",
        title=f"{second_company_name}完成融资",
        url=shared_url,
        snippet=f"{second_company_name}披露融资进展。",
        source_name="示例公开来源",
        published_at="2026-09-01",
    )
    primary.responses[second_query(SEARCH_GROUPS[0][1])] = [second_result]
    primary.responses[second_query(SEARCH_GROUPS[1][1])] = []
    with migrated_app.state.session_factory() as session:
        owner = session.get(User, BETA_USER_ID)
        assert owner is not None
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), second_company_id)
    requests_before = len(fetcher.requests)
    _drain(migrated_app, {"baidu": primary, "bocha": fallback}, fetcher)

    assert len(fetcher.requests) == requests_before + 2
    with migrated_app.state.session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(
                    Event.company_id == second_company_id,
                    Event.fingerprint_version == "web-v1",
                )
            )
            == 0
        )


def test_severe_negative_stays_unconfirmed_and_never_publishes(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)

    query = _query(SEARCH_GROUPS[0][1])
    primary = MockSearchProvider(
        "baidu",
        {
            query: [
                _result(
                    "https://news.example.com/severe",
                    f"{SHARED_COMPANY_NAME}破产线索",
                    f"{SHARED_COMPANY_NAME}出现破产线索。",
                )
            ]
        },
    )

    class SevereFetcherFactory(RecordingFetcherFactory):
        def __call__(self, policy):
            def handler(request: httpx.Request) -> httpx.Response:
                self.requests.append(str(request.url))
                if request.url.path == "/robots.txt":
                    return httpx.Response(404, headers={"content-type": "text/plain"})
                body = (
                    f"<html><head><title>{SHARED_COMPANY_NAME}破产线索</title></head>"
                    f"<body>{SHARED_COMPANY_NAME}（{DEMO_SHARED_COMPANY_CREDIT_CODE}）"
                    "出现破产清算相关公开线索。</body></html>"
                ).encode()
                return httpx.Response(200, content=body, headers={"content-type": "text/html"})

            return TrustedSourceFetcher(
                policy,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                allow_private_test_hosts=True,
            )

    _drain(
        migrated_app,
        {"baidu": primary, "bocha": MockSearchProvider("bocha")},
        SevereFetcherFactory(),
    )
    with migrated_app.state.session_factory() as session:
        event = session.scalar(
            select(Event).where(
                Event.company_id == SHARED_COMPANY_ID,
                Event.fingerprint_version == "web-v1",
            )
        )
        assert event is not None
        assert event.status == "candidate"
        assert event.publication_route == "unconfirmed_lead"
        assert event.risk_severity == "high"
        assert "serious_negative_requires_review" in event.publication_reasons
        assert event.status != "published"


def test_unverified_or_private_company_cannot_enter_research(migrated_app: FastAPI) -> None:
    _grant_platform_admin(migrated_app)
    unverified = Company(
        tenant_id=None,
        credit_code="91310000MA1K00010M",
        legal_name="示例待核验公司有限公司",
        registered_region="虚构省丙市",
        identity_status="unresolved",
        identity_verification_basis=None,
        visibility_scope="public",
    )
    with migrated_app.state.session_factory() as session:
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert owner is not None
        session.add(unverified)
        session.flush()
        request = PersonalCompanyRequest(
            owner_user_id=owner.id,
            request_type="inclusion",
            company_id=unverified.id,
            requested_name=unverified.legal_name,
            requested_credit_code=unverified.credit_code,
            target_key=f"credit:{unverified.credit_code}",
            status="research_queued",
        )
        session.add(request)
        session.commit()
        request_id = request.id

    primary = MockSearchProvider("baidu")
    fallback = MockSearchProvider("bocha")
    with migrated_app.state.session_factory() as session:
        worker = session.get(User, ALPHA_USER_ID)
        assert worker is not None
        result = run_web_research_worker_once(
            session,
            worker,
            {"baidu": primary, "bocha": fallback},
            WebResearchPolicy(),
            fetcher_factory=RecordingFetcherFactory(),
        )
    assert result.status == "idle"
    assert primary.calls == []
    assert fallback.calls == []
    with migrated_app.state.session_factory() as session:
        request = session.get(PersonalCompanyRequest, request_id)
        assert request is not None
        assert request.status == "failed"
        assert request.last_error_code == "company_not_verified_for_shared_research"
        assert session.scalar(select(func.count()).select_from(CompanyResearchJob)) == 0


def test_daily_search_budget_checkpoints_instead_of_calling_again(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)

    primary, fallback = _providers()
    policy = WebResearchPolicy(daily_search_call_limit=1, monthly_search_call_limit=1)
    with migrated_app.state.session_factory() as session:
        worker = session.get(User, ALPHA_USER_ID)
        assert worker is not None
        first = run_web_research_worker_once(
            session,
            worker,
            {"baidu": primary, "bocha": fallback},
            policy,
            fetcher_factory=RecordingFetcherFactory(),
        )
    assert first.status == "partial"
    assert len(primary.calls) == 1

    with migrated_app.state.session_factory() as session:
        worker = session.get(User, ALPHA_USER_ID)
        assert worker is not None
        deferred = run_web_research_worker_once(
            session,
            worker,
            {"baidu": primary, "bocha": fallback},
            policy,
            fetcher_factory=RecordingFetcherFactory(),
        )
    assert deferred.status == "budget_deferred"
    assert len(primary.calls) == 1
    assert fallback.calls == []
    with migrated_app.state.session_factory() as session:
        request = session.scalar(select(PersonalCompanyRequest))
        job = session.scalar(select(CompanyResearchJob))
        assert request is not None and job is not None
        assert request.status == "budget_deferred"
        assert job.current_stage == f"search:{SEARCH_GROUPS[1][0]}"
