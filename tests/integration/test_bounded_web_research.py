from __future__ import annotations

import hashlib
import io
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
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
    EventEvidence,
    PersonalCompanyRequest,
    RawDocument,
    Role,
    Source,
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
from backend.app.research_outcome import research_result
from backend.app.source_fetcher import TrustedSourceFetcher
from backend.app.web_research_service import (
    SEARCH_GROUPS,
    _candidate_event,
    _event_classification,
    _gap_follow_up_query,
    _identity_fingerprint,
    _initial_coverage,
    _process_gap_follow_up,
    _subject_match,
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


def _unicode_pdf_bytes(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    cid_font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/CIDFontType0"),
            NameObject("/BaseFont"): NameObject("/STSong-Light"),
            NameObject("/CIDSystemInfo"): DictionaryObject(
                {
                    NameObject("/Registry"): TextStringObject("Adobe"),
                    NameObject("/Ordering"): TextStringObject("GB1"),
                    NameObject("/Supplement"): NumberObject(4),
                }
            ),
        }
    )
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/BaseFont"): NameObject("/STSong-Light"),
            NameObject("/Encoding"): NameObject("/UniGB-UCS2-H"),
            NameObject("/DescendantFonts"): ArrayObject([writer._add_object(cid_font)]),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(
        f"BT /F1 12 Tf 72 720 Td <{text.encode('utf-16-be').hex().upper()}> Tj ET".encode()
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": "公司官方披露"})
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class RobotsRecoveryFetcherFactory:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def __call__(self, policy):
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(str(request.url))
            if request.url.path == "/robots.txt":
                if request.url.host == "blocked.example.com":
                    return httpx.Response(
                        200,
                        headers={"content-type": "text/plain"},
                        text="User-agent: *\nDisallow: /article\n",
                    )
                return httpx.Response(404, headers={"content-type": "text/plain"})
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    "<html><head><title>官方融资公告</title>"
                    f'<meta property="article:published_time" content="{RECENT_TIMESTAMP}">'
                    "</head><body><main>"
                    f"{SHARED_COMPANY_NAME}（{DEMO_SHARED_COMPANY_CREDIT_CODE}）完成融资，"
                    "本次融资事项已完成。</main></body></html>"
                ),
            )

        return TrustedSourceFetcher(
            policy,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            allow_private_test_hosts=True,
        )


class OfficialPdfFetcherFactory:
    def __init__(self) -> None:
        self.requests: list[str] = []
        self.body = _unicode_pdf_bytes(
            f"{SHARED_COMPANY_NAME}（{DEMO_SHARED_COMPANY_CREDIT_CODE}）完成融资 2026年9月4日"
        )

    def __call__(self, policy):
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(str(request.url))
            if request.url.path == "/robots.txt":
                return httpx.Response(404, headers={"content-type": "text/plain"})
            if request.url.path.endswith(".pdf"):
                return httpx.Response(
                    200,
                    headers={"content-type": "application/pdf"},
                    content=self.body,
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=f"<title>{SHARED_COMPANY_NAME}</title><main>公司官网</main>",
            )

        return TrustedSourceFetcher(
            policy,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            allow_private_test_hosts=True,
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


class GapFollowUpFetcherFactory:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def __call__(self, policy):
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(str(request.url))
            if request.url.path == "/robots.txt":
                return httpx.Response(404, headers={"content-type": "text/plain"})
            published_meta = (
                ""
                if request.url.path.endswith("no-date")
                else f'<meta property="article:published_time" content="{RECENT_TIMESTAMP}">'
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    f"<html><head><title>{SHARED_COMPANY_NAME}完成融资</title>"
                    f"{published_meta}</head><body><main>"
                    f"{SHARED_COMPANY_NAME}（{DEMO_SHARED_COMPANY_CREDIT_CODE}）已完成融资，"
                    "相关事项已经公告。</main></body></html>"
                ),
            )

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


@pytest.mark.parametrize("recorded", [False, True])
def test_ended_research_result_is_owner_only_and_persists(migrated_app, client, recorded):
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        owner = session.get(User, NO_ACCESS_USER_ID)
        request = create_refresh_request(
            session, owner, PersonalEntitlementPolicy(), SHARED_COMPANY_ID
        )
        request_id = request.id
        worker = session.get(User, ALPHA_USER_ID)
        prepare_pending_research_requests(session, worker, WebResearchPolicy())
        row = session.get(PersonalCompanyRequest, request_id)
        job = session.get(CompanyResearchJob, row.research_job_id)
        job.status = row.status = "completed"
        job.current_stage = "completed"
        job.coverage = {
            **(_initial_coverage(WebResearchPolicy()) if recorded else {}),
            "stats": {"quality_gate_passed": 0},
            "completed_at": RECENT_TIMESTAMP,
            "documents": [
                {
                    "status": "failed",
                    "error_code": "robots_disallowed",
                    "url": "https://private.invalid/research",
                    "coverage_category": "contract_commercial",
                }
            ],
        }
        session.commit()
    headers = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}
    for _ in range(2):
        result = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=headers).json()
        assert result["personal_research_result"]["outcome"] == "no_usable_evidence"
        assert result["personal_research_result"]["finished_at"] is not None
        assert "未通过自动读取规则检查" in str(result["personal_research_result"]["limitations"])
        assert "规则文件不可用" in str(result["personal_research_result"]["limitations"])
        assert "private.invalid" not in str(result)
        rows = result["personal_research_result"]["category_coverage"]
        contract = next(item for item in rows if item["category"] == "contract_commercial")
        assert contract["status"] == ("blocked" if recorded else "unknown")
        assert result["investments"] == []
    requests = client.get("/api/v1/me/company-requests", headers=headers).json()
    own = next(item for item in requests if item["id"] == str(request_id))
    assert own["status"] == "completed"
    assert "不代表公司没有" in own["status_message"]
    assert own["research_result"] == result["personal_research_result"]
    for user_id in (ALPHA_USER_ID, BETA_USER_ID):
        other = client.get(
            f"/api/v1/companies/{SHARED_COMPANY_ID}", headers={"X-Demo-User-Id": str(user_id)}
        )
        assert other.status_code == 200
        assert other.json()["personal_research_result"] is None
    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.coalesce(func.sum(UsageLedger.external_calls), 0))) == 0


@pytest.mark.parametrize("failure", [False, True])
def test_group_completion_uses_provider_outcomes_instead_of_existing_events(migrated_app, failure):
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        owner = session.get(User, NO_ACCESS_USER_ID)
        session.get(Company, SHARED_COMPANY_ID).official_website = None
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), SHARED_COMPANY_ID)
        session.commit()
    outcomes = (
        {_query(terms): "provider_unavailable" for _, terms in SEARCH_GROUPS} if failure else {}
    )
    primary = MockSearchProvider("baidu", failures=outcomes)
    fallback = MockSearchProvider("bocha", failures=outcomes)
    fetcher = RecordingFetcherFactory()
    assert _drain(migrated_app, {"baidu": primary, "bocha": fallback}, fetcher)[-1] == "idle"
    assert len(primary.calls) == len(fallback.calls) == 2
    assert fetcher.requests == []
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job.status == "completed"
        result = research_result(job)
        rows = [item for item in result.category_coverage if item.category != "information_quality"]
        assert all(item.status == ("failed" if failure else "no_records") for item in rows)
        assert all((item.search_checked_at is None) == failure for item in rows)
        assert job.coverage["modules"]["information_quality"] == "not_configured"


def test_recorded_coverage_survives_cache_replay_without_new_calls_or_fresh_times(migrated_app):
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        owner = session.get(User, NO_ACCESS_USER_ID)
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), SHARED_COMPANY_ID)
    primary, fallback = _providers()
    fetcher = RecordingFetcherFactory()
    _drain(migrated_app, {"baidu": primary, "bocha": fallback}, fetcher)
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        previous = research_result(job).model_dump(mode="json")
        contract = next(
            item
            for item in previous["category_coverage"]
            if item["category"] == "contract_commercial"
        )
        assert contract["status"] == "evidence_obtained" and contract["evidence_count"] == 1
        assert all(
            item["status"] != "evidence_obtained"
            for item in previous["category_coverage"]
            if item["category"] not in {"contract_commercial", "financing_cap_table"}
        )
        # Replaying the same job emulates recovery; no new request or policy change.
        job.status = "partial"
        job.current_stage = "search:business_capital"
        job.coverage = _initial_coverage(WebResearchPolicy())
        session.scalar(select(PersonalCompanyRequest)).status = "partial"
        session.commit()
    previous_calls = (len(primary.calls), len(fallback.calls), len(fetcher.requests))
    _drain(migrated_app, {"baidu": primary, "bocha": fallback}, fetcher)
    assert (len(primary.calls), len(fallback.calls), len(fetcher.requests)) == previous_calls
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        result = research_result(job)
        contract = next(
            item for item in result.category_coverage if item.category == "contract_commercial"
        )
        assert contract.status == "evidence_obtained" and contract.cache_reused
        assert job.coverage["stats"]["events_created"] == 0
        for old, new in zip(
            previous["category_coverage"],
            result.model_dump(mode="json")["category_coverage"],
            strict=True,
        ):
            assert old["search_checked_at"] == new["search_checked_at"]
            assert old["evidence_checked_at"] == new["evidence_checked_at"]


def test_cached_listing_reprocess_is_idempotent_and_not_a_recent_confirmed_change(
    migrated_app, client
):
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        owner = session.get(User, NO_ACCESS_USER_ID)
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), SHARED_COMPANY_ID)
    primary, fallback = _providers()
    factory = RecordingFetcherFactory()
    _drain(migrated_app, {"baidu": primary, "bocha": fallback}, factory)
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        document = session.scalar(
            select(RawDocument).where(
                RawDocument.canonical_url == "https://news.example.com/article-1"
            )
        )
        # Synthetic cached body reproduces the observed wording, not real company data.
        document.payload = {
            **document.payload,
            "excerpt": f"2020年4月16日，{SHARED_COMPANY_NAME}成功在 港交所主板 上市。",
            "_source_verification": {
                **document.payload["_source_verification"],
                "checked_at": "2026-09-06T02:53:31.618272+00:00",
            },
        }
        original_payload = dict(document.payload)
        document.title = "示例上市历史转载"
        source = session.get(Source, document.source_id)
        event, created, quality = _candidate_event(
            session, company, document, source, WebResearchPolicy(), new_document=False
        )
        assert created and quality.eligible
        assert event.occurred_at.year == 2020
        assert event.status == "candidate" and event.publication_route == "unconfirmed_lead"
        evidence = session.scalar(select(EventEvidence).where(EventEvidence.event_id == event.id))
        expected_check_time = datetime.fromisoformat("2026-09-06T02:53:31.618272+00:00")
        if session.get_bind().dialect.name == "sqlite":
            # SQLite DateTime does not retain timezone metadata; preserve every time digit.
            expected_check_time = expected_check_time.replace(tzinfo=None)
        assert evidence.display_url_checked_at == expected_check_time
        event_id = event.id
        original_owner = document.owner_tenant_id
        session.commit()
        replayed, created_again, _ = _candidate_event(
            session, company, document, source, WebResearchPolicy(), new_document=False
        )
        assert replayed.id == event_id and not created_again
        assert document.visibility_scope == "system_restricted"
        assert document.owner_tenant_id == original_owner
        assert document.payload == original_payload
        assert evidence.display_url_checked_at == expected_check_time
        session.commit()
    assert len(primary.calls) == 2 and fallback.calls == [] and len(factory.requests) == 4
    headers = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}
    detail = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=headers).json()
    event = next(
        item for item in detail["platform_unconfirmed_leads"] if item["id"] == str(event_id)
    )
    assert event["occurred_at"].startswith("2020-04-16")
    assert event["evidence"] and detail["investments"] == []
    assert event["evidence"][0]["url_checked_at"].startswith("2026-09-06T02:53:31.618272")
    assert all(item["id"] != str(event_id) for item in detail["events"])


def test_identity_matching_normalizes_unicode_without_changing_cache_fingerprint() -> None:
    company = Company(
        id=demo_uuid("company-r3-2-unicode-identity"),
        tenant_id=None,
        credit_code="91441900MA52D3F379",
        legal_name="卧安机器人（深圳）股份有限公司",
        registered_region="广东省深圳市",
        identity_status="verified",
        identity_verification_basis="official_government",
        visibility_scope=PLATFORM_SHARED_SCOPE,
    )
    result = SearchResult(
        provider_record_id="unicode-equivalent",
        title="卧安机器人(深圳)股份有限公司通过上市聆讯",
        url="https://www.stcn.com/article/detail/unicode-equivalent.html",
        snippet="卧安机器人(深圳)股份有限公司披露最新进展。",
        source_name="示例公开来源",
        published_at=RECENT_DATE,
    )
    similar_company = SearchResult(
        **{
            **result.to_dict(),
            "provider_record_id": "different-region",
            "title": "卧安机器人(上海)股份有限公司通过上市聆讯",
            "snippet": "卧安机器人(上海)股份有限公司披露最新进展。",
        }
    )

    legacy_fingerprint = hashlib.sha256(
        "|".join(
            (
                str(company.id),
                "".join(company.legal_name.split()).casefold(),
                company.credit_code or "",
                "".join((company.registered_region or "").split()).casefold(),
            )
        ).encode()
    ).hexdigest()

    assert _identity_fingerprint(company) == legacy_fingerprint
    assert _subject_match(company, result) is True
    assert _subject_match(company, similar_company) is False


@pytest.mark.parametrize(
    "statement",
    (
        "已向港交所递表",
        "已向交易所提交上市申请",
        "已通过港交所聆讯",
        "境外上市及全流通申请已获备案",
        "已向港交所递交招股书",
        "已启动招股",
        "已开始公开发售",
        "已正式上市",
    ),
)
def test_precise_listing_transitions_are_classified_as_changes(statement: str) -> None:
    assert (
        _event_classification("", f"卧安机器人（深圳）股份有限公司{statement}。")
        == "exit_liquidity"
    )


def test_static_listing_profile_is_not_classified_as_a_change() -> None:
    assert (
        _event_classification(
            "",
            "卧安机器人（深圳）股份有限公司是一家关注未上市市场的机器人企业。",
        )
        is None
    )
    assert (
        _event_classification(
            "",
            "卧安机器人（深圳）股份有限公司为拟上市企业，产品近日已获业务备案。",
        )
        is None
    )


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


def test_one_job_reuses_robots_rules_for_multiple_pages_on_the_same_origin(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    first_query = _query(SEARCH_GROUPS[0][1])
    second_query = _query(SEARCH_GROUPS[1][1])
    primary = MockSearchProvider(
        "baidu",
        {
            first_query: [
                _result(
                    "https://news.example.com/article-1",
                    f"{SHARED_COMPANY_NAME}完成融资",
                    f"{SHARED_COMPANY_NAME}披露融资进展。",
                ),
                _result(
                    "https://news.example.com/article-2",
                    f"{SHARED_COMPANY_NAME}中标",
                    f"{SHARED_COMPANY_NAME}披露中标进展。",
                ),
            ],
            second_query: [],
        },
    )
    fallback = MockSearchProvider("bocha")
    with migrated_app.state.session_factory() as session:
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert owner is not None
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), SHARED_COMPANY_ID)

    fetcher = RecordingFetcherFactory()
    statuses = _drain(
        migrated_app,
        {"baidu": primary, "bocha": fallback},
        fetcher,
        policy=WebResearchPolicy(
            max_fetch_requests_per_job=3,
            max_documents_per_job=2,
        ),
    )

    assert statuses[-1] == "idle"
    assert fetcher.requests[0] == "https://news.example.com/robots.txt"
    assert set(fetcher.requests[1:]) == {
        "https://news.example.com/article-1",
        "https://news.example.com/article-2",
    }
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        assert job.coverage["stats"]["fetch_calls"] == 3
        assert (
            sum(item["status"] in {"created", "reused"} for item in job.coverage["documents"]) == 2
        )
        assert "_robots_rule_cache" not in job.coverage


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
        assert replay_job.coverage["policy_version"] == "bounded-web-v3"
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
        if event["publication_policy_version"] == "bounded-web-quality-v4"
    ]
    assert len(bounded_leads) == 2
    assert all(event["evidence"] for event in bounded_leads)
    evidence_excerpts = [item["excerpt"] for event in bounded_leads for item in event["evidence"]]
    assert all("PaperPass" not in excerpt for excerpt in evidence_excerpts)
    assert detail["investments"] == []
    assert detail["private_events"] == []


def test_valid_fallback_cache_is_fused_without_calling_fallback_provider(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    query = _query(SEARCH_GROUPS[0][1])
    shared_url = "https://news.example.com/cache-fused"
    cached_result = _result(
        shared_url,
        f"{SHARED_COMPANY_NAME}完成融资",
        f"{SHARED_COMPANY_NAME}披露融资进展。",
    )
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        session.add(
            WebSearchCacheEntry(
                company_id=company.id,
                provider_code="bocha",
                query_kind=SEARCH_GROUPS[0][0],
                query_text=query,
                query_hash=hashlib.sha256(query.encode()).hexdigest(),
                identity_fingerprint=_identity_fingerprint(company),
                response_hash="cached-fallback-response",
                results=[cached_result.to_dict()],
                fetched_at=utc_now(),
                expires_at=utc_now() + timedelta(days=14),
            )
        )
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)
        session.commit()

    primary = MockSearchProvider("baidu", {query: [cached_result]})
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
    assert len(primary.calls) == 1
    assert fallback.calls == []
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        providers = job.coverage["search_groups"][SEARCH_GROUPS[0][0]]["providers"]
        assert providers["bocha"]["status"] == "cache_fused"
        assert job.coverage["stats"]["search_cache_hits"] == 1
        assert job.coverage["stats"]["search_calls"] == 1
        assert job.coverage["candidates"][0]["discovered_by"] == ["baidu", "bocha"]


def test_robots_blocked_candidate_uses_one_bounded_original_source_recovery(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    blocked_title = f"{SHARED_COMPANY_NAME}完成融资"
    group_query = _query(SEARCH_GROUPS[0][1])
    recovery_query = f'"{SHARED_COMPANY_NAME}" "{blocked_title}" 原文 公告'
    blocked_url = "https://blocked.example.com/article/financing"
    recovered_url = "https://notice.gov.cn/company/financing"
    primary = MockSearchProvider(
        "baidu",
        {
            group_query: [
                _result(
                    blocked_url,
                    blocked_title,
                    f"{SHARED_COMPANY_NAME}披露已完成融资。",
                )
            ],
            recovery_query: [
                _result(
                    recovered_url,
                    f"{SHARED_COMPANY_NAME}完成融资的官方公告",
                    f"{SHARED_COMPANY_NAME}披露已完成融资。",
                )
            ],
        },
    )
    fallback = MockSearchProvider("bocha")
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)

    fetcher = RobotsRecoveryFetcherFactory()
    statuses = _drain(
        migrated_app,
        {"baidu": primary, "bocha": fallback},
        fetcher,
        policy=WebResearchPolicy(max_candidate_urls=1, max_documents_per_job=1),
    )
    assert statuses[-1] == "idle"
    assert [call.query for call in primary.calls].count(recovery_query) == 1
    assert all("blocked.example.com/article" not in url for url in fetcher.requests)
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        assert job.coverage["source_recovery"]["status"] == "completed"
        assert job.coverage["source_recovery"]["attempts"] == 1
        assert job.coverage["source_recovery"]["candidate_count_added"] == 1
        assert job.coverage["gap_follow_up"]["status"] == "skipped"
        assert (
            job.coverage["gap_follow_up"]["reason"] == "source_recovery_consumed_follow_up_budget"
        )
        blocked = next(item for item in job.coverage["candidates"] if item["url"] == blocked_url)
        assert blocked["source_access_status"] == "robots_blocked"
        assert (
            session.scalar(
                select(func.count())
                .select_from(RawDocument)
                .where(RawDocument.canonical_url == blocked_url)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(RawDocument)
                .where(RawDocument.canonical_url == recovered_url)
            )
            == 1
        )


def test_one_material_evidence_gap_can_trigger_one_targeted_follow_up(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    first_url = "https://news.example.com/no-date"
    recovered_url = "https://notice.example.com/official-financing"
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        follow_up_query = _gap_follow_up_query(company, "financing_cap_table")
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)

    primary = MockSearchProvider(
        "baidu",
        {
            _query(SEARCH_GROUPS[0][1]): [
                _result(
                    first_url,
                    f"{SHARED_COMPANY_NAME}完成融资",
                    f"{SHARED_COMPANY_NAME}已完成融资。",
                )
            ],
            _query(SEARCH_GROUPS[1][1]): [],
            follow_up_query: [
                _result(
                    recovered_url,
                    f"{SHARED_COMPANY_NAME}完成融资公告",
                    f"{SHARED_COMPANY_NAME}已完成融资。",
                )
            ],
        },
    )
    fallback = MockSearchProvider("bocha")
    fetcher = GapFollowUpFetcherFactory()

    statuses = _drain(
        migrated_app,
        {"baidu": primary, "bocha": fallback},
        fetcher,
    )

    assert statuses[-1] == "idle"
    assert [call.query for call in primary.calls].count(follow_up_query) == 1
    assert len(primary.calls) == 3
    assert len(fallback.calls) == 1
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        assert job.coverage["stats"]["search_calls"] == 4
        assert job.external_calls == 8
        assert job.coverage["gap_follow_up"]["status"] == "completed"
        assert job.coverage["gap_follow_up"]["attempts"] == 1
        assert job.coverage["gap_follow_up"]["event_type"] == "financing_cap_table"
        assert job.coverage["gap_follow_up"]["candidate_count_added"] == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(
                    Event.company_id == SHARED_COMPANY_ID,
                    Event.publication_policy_version == "bounded-web-quality-v4",
                )
            )
            == 1
        )
        first_document = session.scalar(
            select(RawDocument).where(RawDocument.canonical_url == first_url)
        )
        assert first_document is not None
        assert first_document.payload["quality_gate"]["status"] == "internal_only"
        assert "missing_reliable_published_at" in first_document.payload["quality_gate"]["reasons"]
        assert (
            session.scalar(
                select(func.count()).select_from(Event).where(Event.fingerprint_version == "web-v1")
            )
            == 1
        )


def test_gap_follow_up_attempt_is_checkpointed_before_provider_call(
    migrated_app: FastAPI,
) -> None:
    class CrashingProvider(MockSearchProvider):
        code = "baidu"

        def __init__(self) -> None:
            self.calls = 0

        def search(self, request):
            self.calls += 1
            raise RuntimeError("simulated worker interruption")

    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        user = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and user is not None
        request_out = create_refresh_request(
            session,
            user,
            PersonalEntitlementPolicy(),
            company.id,
        )
        job = CompanyResearchJob(
            company_id=company.id,
            created_by_user_id=user.id,
            status="partial",
            current_stage="gap_follow_up",
            policy_version="bounded-web-v3",
            coverage={
                "source_recovery": {"status": "not_requested", "attempts": 0},
                "gap_follow_up": {
                    "status": "pending",
                    "attempts": 0,
                    "event_type": "financing_cap_table",
                    "source_url": "https://news.example.test/original",
                    "reason": "missing_reliable_published_at",
                },
                "candidates": [],
                "candidate_index": 0,
                "documents": [],
                "stats": {"search_calls": 0, "search_cache_hits": 0},
            },
        )
        session.add(job)
        session.flush()
        request = session.get(PersonalCompanyRequest, request_out.id)
        assert request is not None
        request.research_job_id = job.id
        request.status = "partial"
        session.commit()
        job_id = job.id

    crashing = CrashingProvider()
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        user = session.get(User, NO_ACCESS_USER_ID)
        job = session.get(CompanyResearchJob, job_id)
        assert company is not None and user is not None and job is not None
        with pytest.raises(RuntimeError, match="simulated worker interruption"):
            _process_gap_follow_up(
                session,
                user,
                job,
                company,
                WebResearchPolicy(),
                {"baidu": crashing, "bocha": MockSearchProvider("bocha")},
            )
    assert crashing.calls == 1

    replacement = MockSearchProvider("baidu")
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        user = session.get(User, NO_ACCESS_USER_ID)
        job = session.get(CompanyResearchJob, job_id)
        assert company is not None and user is not None and job is not None
        assert job.coverage["gap_follow_up"]["status"] == "running"
        assert job.coverage["gap_follow_up"]["attempts"] == 1
        result = _process_gap_follow_up(
            session,
            user,
            job,
            company,
            WebResearchPolicy(),
            {"baidu": replacement, "bocha": MockSearchProvider("bocha")},
        )
    assert result.stage == "finalize"
    assert replacement.calls == []


def test_verified_company_official_pdf_can_supply_bounded_evidence(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    pdf_url = "https://www.example-company.cn/notices/financing.pdf"
    query = _query(SEARCH_GROUPS[0][1])
    primary = MockSearchProvider(
        "baidu",
        {
            query: [
                _result(
                    pdf_url,
                    f"{SHARED_COMPANY_NAME}完成融资公告",
                    f"{SHARED_COMPANY_NAME}披露已完成融资。",
                )
            ]
        },
    )
    fallback = MockSearchProvider("bocha")
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        company.official_website = "https://www.example-company.cn"
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)
        session.commit()

    fetcher = OfficialPdfFetcherFactory()
    statuses = _drain(
        migrated_app,
        {"baidu": primary, "bocha": fallback},
        fetcher,
    )
    assert statuses[-1] == "idle"
    with migrated_app.state.session_factory() as session:
        document = session.scalar(select(RawDocument).where(RawDocument.canonical_url == pdf_url))
        assert document is not None
        extraction = document.payload["content_extraction"]
        assert extraction["document_format"] == "pdf"
        assert extraction["page_count"] == 1
        assert document.payload["retention"] == "minimal_excerpt"
        assert (
            session.scalar(
                select(func.count()).select_from(Event).where(Event.fingerprint_version == "web-v1")
            )
            == 1
        )


def test_non_authoritative_pdf_is_not_downloaded_or_used_as_evidence(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    pdf_url = "https://media.example.com/financing.pdf"
    primary = MockSearchProvider(
        "baidu",
        {
            _query(SEARCH_GROUPS[0][1]): [
                _result(
                    pdf_url,
                    f"{SHARED_COMPANY_NAME}完成融资",
                    f"{SHARED_COMPANY_NAME}披露已完成融资。",
                )
            ]
        },
    )
    fallback = MockSearchProvider("bocha")
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)

    fetcher = RecordingFetcherFactory()
    statuses = _drain(
        migrated_app,
        {"baidu": primary, "bocha": fallback},
        fetcher,
    )
    assert statuses[-1] == "idle"
    assert fetcher.requests == []
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        candidate = next(item for item in job.coverage["candidates"] if item["url"] == pdf_url)
        assert candidate["source_access_status"] == "authorized_manual_import_required"
        assert candidate["source_access_reason"] == "pdf_source_not_authoritative"
        assert (
            session.scalar(
                select(func.count())
                .select_from(RawDocument)
                .where(RawDocument.canonical_url == pdf_url)
            )
            == 0
        )


def test_unicode_equivalent_identity_replays_existing_search_cache_without_provider_calls(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    full_width_name = "卧安机器人（深圳）股份有限公司"
    half_width_name = "卧安机器人(深圳)股份有限公司"
    url = "https://www.stcn.com/article/detail/nfkc-cache-replay.html"
    now = utc_now()

    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        company.legal_name = full_width_name
        company.credit_code = "91441900MA52D3F379"
        session.flush()
        identity_fingerprint = _identity_fingerprint(company)
        cached_result = SearchResult(
            provider_record_id="nfkc-cache-replay",
            title=f"{half_width_name}完成融资",
            url=url,
            snippet=f"{half_width_name}披露融资进展。",
            source_name="示例公开来源",
            published_at=RECENT_DATE,
        )
        for group_code, terms in SEARCH_GROUPS:
            query = f'"{full_width_name}" {terms}'
            session.add(
                WebSearchCacheEntry(
                    company_id=company.id,
                    provider_code="baidu",
                    query_kind=group_code,
                    query_text=query,
                    query_hash=hashlib.sha256(query.encode()).hexdigest(),
                    identity_fingerprint=identity_fingerprint,
                    response_hash=hashlib.sha256(f"{group_code}:{url}".encode()).hexdigest(),
                    results=[cached_result.to_dict()],
                    fetched_at=now,
                    expires_at=now + timedelta(days=14),
                )
            )
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)
        session.commit()

    class UnicodeFetcherFactory:
        def __init__(self) -> None:
            self.requests: list[str] = []

        def __call__(self, policy):
            def handler(request: httpx.Request) -> httpx.Response:
                self.requests.append(str(request.url))
                if request.url.path == "/robots.txt":
                    return httpx.Response(404, headers={"content-type": "text/plain"})
                return httpx.Response(
                    200,
                    headers={"content-type": "text/html"},
                    text=(
                        f"<html><head><title>{half_width_name}完成融资</title>"
                        '<meta property="article:published_time" '
                        f'content="{RECENT_TIMESTAMP}"></head><body><main>'
                        f"{half_width_name}已完成融资，相关事项已公告。"
                        "</main></body></html>"
                    ),
                )

            return TrustedSourceFetcher(
                policy,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                allow_private_test_hosts=True,
            )

    primary = MockSearchProvider("baidu")
    fallback = MockSearchProvider("bocha")
    fetcher = UnicodeFetcherFactory()
    statuses = _drain(
        migrated_app,
        {"baidu": primary, "bocha": fallback},
        fetcher,
    )

    assert statuses[-1] == "idle"
    assert primary.calls == []
    assert fallback.calls == []
    assert len(fetcher.requests) == 2
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        assert job.coverage["stats"]["search_calls"] == 0
        assert job.coverage["stats"]["search_cache_hits"] == 2
        for group_code, _ in SEARCH_GROUPS:
            provider = job.coverage["search_groups"][group_code]["providers"]["baidu"]
            assert provider["status"] == "cache_hit"
            assert provider["filter_reasons"] == {"qualified": 1}
        assert (
            session.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(
                    UsageLedger.provider.in_(("web_search_baidu", "web_search_bocha")),
                    UsageLedger.external_calls > 0,
                )
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(
                    Event.company_id == SHARED_COMPANY_ID,
                    Event.fingerprint_version == "web-v1",
                )
            )
            == 1
        )


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
            item["publication_policy_version"] == "bounded-web-quality-v4"
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
        providers = job.coverage["search_groups"][SEARCH_GROUPS[0][0]]["providers"]
        assert providers["baidu"]["filter_reasons"] == {"subject_mismatch": 1}
        assert providers["bocha"]["filter_reasons"] == {"qualified": 1}
        assert (
            session.scalar(
                select(func.count()).select_from(Event).where(Event.fingerprint_version == "web-v1")
            )
            == 0
        )


def test_source_ranking_prefers_government_regulatory_official_and_reviewed_media(
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
                _result(
                    "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0903/example.pdf",
                    f"{SHARED_COMPANY_NAME}上市备案公告",
                    f"{SHARED_COMPANY_NAME}披露上市备案进展。",
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
            "regulatory_disclosure",
            "company_official",
            "trusted_media_article",
            "locatable_source_page",
        ]
        assert all("qcc.com" not in item["url"] for item in candidates)
        provider_state = job.coverage["search_groups"][SEARCH_GROUPS[0][0]]["providers"]
        assert provider_state["baidu"]["filter_reasons"] == {
            "blocked_or_unsafe_url": 1,
            "profile_or_listing": 1,
            "qualified": 5,
        }
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
        assert providers["baidu"]["filter_reasons"] == {"profile_or_listing": 1}
        assert providers["bocha"]["reason"] == "insufficient_qualified_subject_results"
        assert [item["source_tier"] for item in job.coverage["candidates"]] == [
            "trusted_media_article"
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
        assert [item["url"] for item in candidates] == [
            "https://www.stcn.com/article/detail/recent.html"
        ]
        assert candidates[0]["search_date_status"] == "recent"
        providers = job.coverage["search_groups"][SEARCH_GROUPS[0][0]]["providers"]
        assert providers["baidu"]["qualified_subject_results"] == 0
        assert providers["baidu"]["filter_reasons"] == {"published_at_old": 1}
        assert providers["bocha"]["reason"] == "insufficient_qualified_subject_results"


def test_old_and_profile_search_results_never_reach_fetch_queue(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        owner = session.get(User, NO_ACCESS_USER_ID)
        assert company is not None and owner is not None
        company.official_website = None
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), company.id)
        session.commit()

    old_result = _result(
        "https://news.example.com/company/history",
        f"{SHARED_COMPANY_NAME}企业历史资料",
        f"{SHARED_COMPANY_NAME}企业历史资料。",
    )
    old_result = SearchResult(
        **{
            **old_result.to_dict(),
            "published_at": (utc_now() - timedelta(days=366)).date().isoformat(),
        }
    )
    blocked_profile_result = _result(
        "https://www.aiqicha.com/company_detail_95055071027653",
        f"{SHARED_COMPANY_NAME}企业资料",
        f"{SHARED_COMPANY_NAME}企业资料。",
    )
    primary = MockSearchProvider(
        "baidu",
        {
            _query(SEARCH_GROUPS[0][1]): [old_result, blocked_profile_result],
            _query(SEARCH_GROUPS[1][1]): [old_result, blocked_profile_result],
        },
    )
    fallback = MockSearchProvider("bocha")
    fetcher = RecordingFetcherFactory()

    _drain(migrated_app, {"baidu": primary, "bocha": fallback}, fetcher)

    assert fetcher.requests == []
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        assert job.coverage["candidates"] == []
        assert job.coverage["stats"]["fetch_calls"] == 0
        for group_code, _ in SEARCH_GROUPS:
            providers = job.coverage["search_groups"][group_code]["providers"]
            assert providers["baidu"]["subject_results"] == 2
            assert providers["baidu"]["qualified_subject_results"] == 0


def test_verified_official_website_is_seeded_without_an_extra_search_call(
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

    primary = MockSearchProvider("baidu")
    fallback = MockSearchProvider("bocha")
    for _ in SEARCH_GROUPS:
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

    assert len(primary.calls) == len(SEARCH_GROUPS)
    assert len(fallback.calls) == len(SEARCH_GROUPS)
    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job is not None
        assert job.current_stage == "fetch"
        candidates = job.coverage["candidates"]
        assert len(candidates) == 1
        assert candidates[0]["url"] == "https://www.example-company.cn/"
        assert candidates[0]["source_tier"] == "company_official"
        assert candidates[0]["discovered_by"] == ["verified_company_identity"]


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

    class FailingPrimary(MockSearchProvider):
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
            "task_limit": 4,
            "task_baseline": 0,
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
