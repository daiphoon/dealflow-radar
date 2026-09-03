from __future__ import annotations

import hashlib
from datetime import timedelta

import httpx
import pytest
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
    UsageLedger,
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
from backend.app.web_search import MockSearchProvider, SearchProviderError, SearchResult

ALPHA_HEADERS = {"X-Demo-User-Id": str(ALPHA_USER_ID)}
SHARED_COMPANY_ID = demo_uuid("company-示例星河科技一号有限公司")
SHARED_COMPANY_NAME = "示例星河科技一号有限公司"
RECENT_DATE = utc_now().date().isoformat()
RECENT_TIMESTAMP = utc_now().replace(hour=8, minute=0, second=0, microsecond=0).isoformat()


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
        published_at=RECENT_DATE,
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
                f'{title}</title><meta property="article:published_time" '
                f'content="{RECENT_TIMESTAMP}"></head><body>'
                "<nav>融资排行 产品导航</nav><main>"
                f"{SHARED_COMPANY_NAME}（{DEMO_SHARED_COMPANY_CREDIT_CODE}）公告："
                f"{title}，相关事项正在推进。"
                "</main><aside>PaperPass 完成 A 轮融资。</aside></body></html>"
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
    policy: WebResearchPolicy | None = None,
) -> list[str]:
    statuses: list[str] = []
    active_policy = policy or WebResearchPolicy()
    for _ in range(limit):
        with app.state.session_factory() as session:
            worker = session.get(User, ALPHA_USER_ID)
            assert worker is not None
            result = run_web_research_worker_once(
                session,
                worker,
                providers,
                active_policy,
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
        policy=WebResearchPolicy(version="bounded-web-v1"),
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
        bounded_events = list(
            session.scalars(
                select(Event).where(
                    Event.company_id == SHARED_COMPANY_ID,
                    Event.fingerprint_version == "web-v1",
                )
            )
        )
        assert all(event.occurred_at is None for event in bounded_events)
        assert all(event.published_at is not None for event in bounded_events)
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
        assert replay_job.coverage["policy_version"] == "bounded-web-v2"
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
        if event["publication_policy_version"] == "bounded-web-quality-v2"
    ]
    assert len(bounded_leads) == 2
    assert all(event["evidence"] for event in bounded_leads)
    evidence_excerpts = [item["excerpt"] for event in bounded_leads for item in event["evidence"]]
    assert all("PaperPass" not in excerpt for excerpt in evidence_excerpts)
    assert detail["investments"] == []
    assert detail["private_events"] == []


@pytest.mark.parametrize(
    ("page_title", "page_body", "expected_reason"),
    [
        (
            f"{SHARED_COMPANY_NAME}企业资料",
            f'<meta property="article:published_time" content="{RECENT_TIMESTAMP}">'
            f"</head><body><main><h1>{SHARED_COMPANY_NAME}企业资料</h1>"
            f"<p>{SHARED_COMPANY_NAME}成立于虚构年份，页面展示企业基本资料。</p>"
            '<section class="related news"><p>PaperPass 完成 A 轮融资。</p></section>'
            "</main></body></html>",
            "no_material_change_signal",
        ),
        (
            f"{SHARED_COMPANY_NAME}企业资料",
            f"</head><body><main><p>{SHARED_COMPANY_NAME}"
            "完成融资，相关事项已经公告。</p></main></body></html>",
            "missing_reliable_published_at",
        ),
        (
            f"{SHARED_COMPANY_NAME}企业资料",
            f'<meta property="article:published_time" content="{RECENT_TIMESTAMP}">'
            f"</head><body><main><p>{SHARED_COMPANY_NAME}是一家虚构测试企业。</p>"
            "<p>PaperPass 完成 A 轮融资。</p></main></body></html>",
            "subject_not_in_change_passage",
        ),
        (
            f"{SHARED_COMPANY_NAME}完成 A 轮融资",
            f'<meta property="article:published_time" content="{RECENT_TIMESTAMP}">'
            f"</head><body><main><p>{SHARED_COMPANY_NAME}是一家虚构测试企业，"
            "页面正文未披露新变化。</p></main></body></html>",
            "no_material_change_signal",
        ),
    ],
)
def test_low_quality_pages_remain_internal_and_are_not_user_visible(
    migrated_app: FastAPI,
    client: TestClient,
    page_title: str,
    page_body: str,
    expected_reason: str,
) -> None:
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)

    url = "https://noise.example.com/company-profile"
    primary = MockSearchProvider(
        "baidu",
        {
            _query(SEARCH_GROUPS[0][1]): [
                _result(
                    url,
                    f"{SHARED_COMPANY_NAME}企业资料",
                    f"{SHARED_COMPANY_NAME}公开资料。",
                )
            ],
            _query(SEARCH_GROUPS[1][1]): [],
        },
    )

    class LowQualityFetcherFactory(RecordingFetcherFactory):
        def __call__(self, policy):
            def handler(request: httpx.Request) -> httpx.Response:
                self.requests.append(str(request.url))
                if request.url.path == "/robots.txt":
                    return httpx.Response(404, headers={"content-type": "text/plain"})
                return httpx.Response(
                    200,
                    headers={"content-type": "text/html"},
                    text=(f"<html><head><title>{page_title}</title>{page_body}"),
                )

            return TrustedSourceFetcher(
                policy,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                allow_private_test_hosts=True,
            )

    _drain(
        migrated_app,
        {"baidu": primary, "bocha": MockSearchProvider("bocha")},
        LowQualityFetcherFactory(),
    )

    with migrated_app.state.session_factory() as session:
        document = session.scalar(select(RawDocument).where(RawDocument.canonical_url == url))
        assert document is not None
        assert document.visibility_scope == "system_restricted"
        assert document.payload["quality_gate"]["status"] == "internal_only"
        assert expected_reason in document.payload["quality_gate"]["reasons"]
        assert (
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(
                    Event.company_id == SHARED_COMPANY_ID,
                    Event.fingerprint_version == "web-v1",
                )
            )
            == 0
        )
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        assert job.coverage["stats"]["internal_candidates"] == 1
        assert job.coverage["stats"]["quality_gate_passed"] == 0

    for user_id in (NO_ACCESS_USER_ID, BETA_USER_ID):
        response = client.get(
            f"/api/v1/companies/{SHARED_COMPANY_ID}",
            headers={"X-Demo-User-Id": str(user_id)},
        )
        assert response.status_code == 200
        assert not any(
            item["publication_policy_version"] == "bounded-web-quality-v2"
            for item in response.json()["platform_unconfirmed_leads"]
        )


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


def test_source_ranking_prefers_government_official_and_reviewed_media(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        company.official_website = "https://www.example-company.cn"
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)
        session.commit()

    query = _query(SEARCH_GROUPS[0][1])
    primary = MockSearchProvider(
        "baidu",
        {
            query: [
                _result(
                    "https://www.qcc.com/firm/private-profile.html",
                    f"{SHARED_COMPANY_NAME}企业资料",
                    f"{SHARED_COMPANY_NAME}企业资料。",
                ),
                _result(
                    "https://www.newseed.cn/project/example",
                    f"{SHARED_COMPANY_NAME}完成融资",
                    f"{SHARED_COMPANY_NAME}披露融资进展。",
                ),
                _result(
                    "https://news.other.example/article/1",
                    f"{SHARED_COMPANY_NAME}完成融资",
                    f"{SHARED_COMPANY_NAME}披露融资进展。",
                ),
                _result(
                    "https://www.stcn.com/article/detail/1.html",
                    f"{SHARED_COMPANY_NAME}完成融资",
                    f"{SHARED_COMPANY_NAME}披露融资进展。",
                ),
                _result(
                    "https://news.example-company.cn/news/1",
                    f"{SHARED_COMPANY_NAME}完成融资",
                    f"{SHARED_COMPANY_NAME}披露融资进展。",
                ),
                _result(
                    "https://notice.example.gov.cn/notices/1",
                    f"{SHARED_COMPANY_NAME}完成融资",
                    f"{SHARED_COMPANY_NAME}披露融资进展。",
                ),
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
    assert result.status == "partial"
    assert fallback.calls == []

    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        candidates = job.coverage["candidates"]
        assert [item["source_tier"] for item in candidates] == [
            "government",
            "company_official",
            "trusted_media_article",
            "locatable_source_page",
            "profile_or_listing",
        ]
        assert all("qcc.com" not in item["url"] for item in candidates)
        provider_state = job.coverage["search_groups"][SEARCH_GROUPS[0][0]]["providers"]
        assert provider_state["bocha"] == {
            "status": "not_called",
            "reason": "primary_has_qualified_subject_results",
        }


def test_cross_group_duplicate_keeps_the_better_dated_candidate(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)

    url = "https://www.stcn.com/article/detail/repeated.html"
    recent = _result(
        url,
        f"{SHARED_COMPANY_NAME}完成融资",
        f"{SHARED_COMPANY_NAME}披露融资进展。",
    )
    undated = SearchResult(
        **{
            **recent.to_dict(),
            "title": f"{SHARED_COMPANY_NAME}企业资料",
            "published_at": None,
        }
    )
    primary = MockSearchProvider(
        "baidu",
        {
            _query(SEARCH_GROUPS[0][1]): [recent],
            _query(SEARCH_GROUPS[1][1]): [undated],
        },
    )
    fallback = MockSearchProvider("bocha")
    for _ in range(2):
        with migrated_app.state.session_factory() as session:
            worker = session.get(User, ALPHA_USER_ID)
            assert worker is not None
            run_web_research_worker_once(
                session,
                worker,
                {"baidu": primary, "bocha": fallback},
                WebResearchPolicy(),
                fetcher_factory=RecordingFetcherFactory(),
            )

    assert len(primary.calls) == 2
    assert fallback.calls == []
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        candidates = job.coverage["candidates"]
        assert len(candidates) == 1
        assert candidates[0]["title"].endswith("完成融资")
        assert candidates[0]["published_at"] == RECENT_DATE
        assert candidates[0]["search_date_status"] == "recent"


def test_profile_only_primary_results_trigger_conditional_fallback(
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
                    "https://www.newseed.cn/project/example",
                    f"{SHARED_COMPANY_NAME}企业资料",
                    f"{SHARED_COMPANY_NAME}企业资料。",
                )
            ]
        },
    )
    fallback = MockSearchProvider(
        "bocha",
        {
            query: [
                _result(
                    "https://www.stcn.com/article/detail/2.html",
                    f"{SHARED_COMPANY_NAME}完成融资",
                    f"{SHARED_COMPANY_NAME}披露融资进展。",
                )
            ]
        },
    )
    with migrated_app.state.session_factory() as session:
        worker = session.get(User, ALPHA_USER_ID)
        assert worker is not None
        run_web_research_worker_once(
            session,
            worker,
            {"baidu": primary, "bocha": fallback},
            WebResearchPolicy(),
            fetcher_factory=RecordingFetcherFactory(),
        )

    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        providers = job.coverage["search_groups"][SEARCH_GROUPS[0][0]]["providers"]
        assert providers["baidu"]["subject_results"] == 1
        assert providers["baidu"]["qualified_subject_results"] == 0
        assert providers["bocha"]["reason"] == "insufficient_qualified_subject_results"
        assert [item["source_tier"] for item in job.coverage["candidates"]] == [
            "trusted_media_article",
            "profile_or_listing",
        ]


def test_explicitly_old_primary_result_triggers_recent_fallback(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)

    query = _query(SEARCH_GROUPS[0][1])
    old_result = _result(
        "https://news.example.com/article/old",
        f"{SHARED_COMPANY_NAME}完成融资",
        f"{SHARED_COMPANY_NAME}披露融资进展。",
    )
    old_result = SearchResult(
        **{
            **old_result.to_dict(),
            "published_at": (utc_now() - timedelta(days=366)).date().isoformat(),
        }
    )
    primary = MockSearchProvider("baidu", {query: [old_result]})
    fallback = MockSearchProvider(
        "bocha",
        {
            query: [
                _result(
                    "https://www.stcn.com/article/detail/recent.html",
                    f"{SHARED_COMPANY_NAME}完成融资",
                    f"{SHARED_COMPANY_NAME}披露融资进展。",
                )
            ]
        },
    )
    with migrated_app.state.session_factory() as session:
        worker = session.get(User, ALPHA_USER_ID)
        assert worker is not None
        run_web_research_worker_once(
            session,
            worker,
            {"baidu": primary, "bocha": fallback},
            WebResearchPolicy(),
            fetcher_factory=RecordingFetcherFactory(),
        )

    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        candidates = job.coverage["candidates"]
        assert candidates[0]["url"].endswith("/recent.html")
        assert candidates[0]["search_date_status"] == "recent"
        assert candidates[-1]["search_date_status"] == "old"
        providers = job.coverage["search_groups"][SEARCH_GROUPS[0][0]]["providers"]
        assert providers["baidu"]["qualified_subject_results"] == 0
        assert providers["bocha"]["reason"] == "insufficient_qualified_subject_results"


@pytest.mark.parametrize(
    ("published_offset_days", "expected_reason"),
    [
        (-366, "published_before_recent_window"),
        (2, "published_at_in_future"),
    ],
)
def test_non_recent_page_remains_internal_and_cache_replay_uses_zero_calls(
    migrated_app: FastAPI,
    published_offset_days: int,
    expected_reason: str,
) -> None:
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)

    url = "https://notice.example.gov.cn/notices/recency"
    result = _result(
        url,
        f"{SHARED_COMPANY_NAME}完成融资",
        f"{SHARED_COMPANY_NAME}披露融资进展。",
    )
    primary = MockSearchProvider(
        "baidu",
        {_query(terms): [result] for _, terms in SEARCH_GROUPS},
    )
    fallback = MockSearchProvider("bocha")
    published_at = (utc_now() + timedelta(days=published_offset_days)).replace(microsecond=0)

    class RecencyFetcherFactory(RecordingFetcherFactory):
        def __call__(self, policy):
            def handler(request: httpx.Request) -> httpx.Response:
                self.requests.append(str(request.url))
                if request.url.path == "/robots.txt":
                    return httpx.Response(404, headers={"content-type": "text/plain"})
                return httpx.Response(
                    200,
                    headers={"content-type": "text/html"},
                    text=(
                        f"<html><head><title>{SHARED_COMPANY_NAME}完成融资</title>"
                        '<meta property="article:published_time" '
                        f'content="{published_at.isoformat()}"></head><body><main>'
                        f"{SHARED_COMPANY_NAME}（{DEMO_SHARED_COMPANY_CREDIT_CODE}）"
                        "完成融资，相关事项已经公告。"
                        "</main></body></html>"
                    ),
                )

            return TrustedSourceFetcher(
                policy,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                allow_private_test_hosts=True,
            )

    fetcher = RecencyFetcherFactory()
    _drain(migrated_app, {"baidu": primary, "bocha": fallback}, fetcher)
    assert len(primary.calls) == 2
    assert fallback.calls == []
    assert len(fetcher.requests) == 2

    with migrated_app.state.session_factory() as session:
        document = session.scalar(select(RawDocument).where(RawDocument.canonical_url == url))
        assert document is not None
        quality = document.payload["quality_gate"]
        assert quality["date_basis"] == "source_published_at"
        assert expected_reason in quality["reasons"]
        assert (
            session.scalar(
                select(func.count()).select_from(Event).where(Event.fingerprint_version == "web-v1")
            )
            == 0
        )
        second_user = session.get(User, BETA_USER_ID)
        company = session.get(Company, SHARED_COMPANY_ID)
        assert second_user is not None and company is not None
        create_refresh_request(session, second_user, PersonalEntitlementPolicy(), company.id)

    _drain(migrated_app, {"baidu": primary, "bocha": fallback}, fetcher)
    assert len(primary.calls) == 2
    assert fallback.calls == []
    assert len(fetcher.requests) == 2
    with migrated_app.state.session_factory() as session:
        replay_job = session.scalar(
            select(CompanyResearchJob)
            .where(CompanyResearchJob.created_by_user_id == BETA_USER_ID)
            .order_by(CompanyResearchJob.created_at.desc())
        )
        assert replay_job is not None
        assert replay_job.external_calls == 0
        assert replay_job.coverage["stats"]["internal_candidates"] == 1
        assert (
            session.scalar(
                select(func.count()).select_from(Event).where(Event.fingerprint_version == "web-v1")
            )
            == 0
        )


def test_primary_failure_diagnostic_is_safe_and_fallback_reason_is_explicit(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)

    class FailingPrimary:
        code = "baidu"

        def __init__(self) -> None:
            self.calls = []

        def search(self, request):
            self.calls.append(request)
            raise SearchProviderError(
                "authentication_failed",
                "safe diagnostic",
                external_calls=1,
                http_status=401,
            )

    query = _query(SEARCH_GROUPS[0][1])
    primary = FailingPrimary()
    fallback = MockSearchProvider(
        "bocha",
        {
            query: [
                _result(
                    "https://www.stcn.com/article/detail/3.html",
                    f"{SHARED_COMPANY_NAME}完成融资",
                    f"{SHARED_COMPANY_NAME}披露融资进展。",
                )
            ]
        },
    )
    with migrated_app.state.session_factory() as session:
        worker = session.get(User, ALPHA_USER_ID)
        assert worker is not None
        run_web_research_worker_once(
            session,
            worker,
            {"baidu": primary, "bocha": fallback},
            WebResearchPolicy(),
            fetcher_factory=RecordingFetcherFactory(),
        )

    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        providers = job.coverage["search_groups"][SEARCH_GROUPS[0][0]]["providers"]
        assert providers["baidu"] == {
            "status": "failed",
            "error_code": "authentication_failed",
            "http_status": 401,
            "subject_results": 0,
            "qualified_subject_results": 0,
        }
        assert providers["bocha"]["reason"] == "primary_failed"
        usage = session.scalar(
            select(UsageLedger).where(UsageLedger.provider == "web_search_baidu")
        )
        assert usage is not None
        assert usage.metrics == {
            "query_kind": SEARCH_GROUPS[0][0],
            "status": "failed",
            "error_code": "authentication_failed",
            "http_status": 401,
        }
        assert "query" not in usage.metrics


def test_pause_between_single_steps_does_not_consume_worker_elapsed_budget(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        worker = session.get(User, ALPHA_USER_ID)
        assert company is not None and owner is not None and worker is not None
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)
        prepare_pending_research_requests(session, worker, WebResearchPolicy())
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        coverage = dict(job.coverage)
        coverage["started_at"] = (utc_now() - timedelta(hours=1)).isoformat()
        job.coverage = coverage
        job.status = "partial"
        session.commit()

    primary = MockSearchProvider("baidu", {_query(SEARCH_GROUPS[0][1]): []})
    with migrated_app.state.session_factory() as session:
        worker = session.get(User, ALPHA_USER_ID)
        assert worker is not None
        result = run_web_research_worker_once(
            session,
            worker,
            {"baidu": primary, "bocha": MockSearchProvider("bocha")},
            WebResearchPolicy(),
            fetcher_factory=RecordingFetcherFactory(),
        )

    assert result.status == "partial"
    assert result.stage == f"search:{SEARCH_GROUPS[1][0]}"
    assert len(primary.calls) == 1


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
        published_at=RECENT_DATE,
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
                    f"<html><head><title>{SHARED_COMPANY_NAME}破产线索</title>"
                    '<meta property="article:published_time" '
                    f'content="{RECENT_TIMESTAMP}"></head>'
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
