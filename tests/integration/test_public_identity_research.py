"""主体查证只使用本地 HTTP fixture，不调用真实搜索或模型。"""

import hashlib
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.config import WebResearchPolicy
from backend.app.demo import ALPHA_USER_ID, BETA_USER_ID, DEMO_SHARED_COMPANY_CREDIT_CODE
from backend.app.identity_research import corroborated, identity_evidence, run_identity_step
from backend.app.models import (
    Company,
    CompanyResearchJob,
    Event,
    IdentityResearchState,
    PersonalCompanyRequest,
    Role,
    UsageLedger,
    User,
    UserRoleAssignment,
)
from backend.app.source_fetcher import TrustedSourceFetcher
from backend.app.web_research_service import (
    inspect_web_research_queue,
    prepare_pending_research_requests,
)
from backend.app.web_search import (
    BaiduSearchProvider,
    MockSearchProvider,
    SearchRequest,
    SearchResult,
)

NAME = "示例独立主体核对有限公司"
# A checksum-valid synthetic identifier; never presented as a real business.
CODE = DEMO_SHARED_COMPANY_CREDIT_CODE
PAIR_URL = "https://registry.example.org/company"
GOV_URL = "https://agency.example.gov.cn/notice"


def setup_request(app):
    with app.state.session_factory() as session:
        # Free a fixture code without changing any real data.
        company = session.scalar(select(Company).where(Company.credit_code == CODE))
        company.credit_code = None
        role = session.scalar(select(Role).where(Role.code == "platform_admin"))
        session.add(UserRoleAssignment(user_id=ALPHA_USER_ID, role_id=role.id))
        request = PersonalCompanyRequest(
            owner_user_id=BETA_USER_ID,
            request_type="inclusion",
            requested_name=NAME,
            requested_credit_code=CODE,
            target_key=f"credit:{CODE}",
            status="pending",
        )
        session.add(request)
        session.commit()
        return request.id


def providers():
    records = [
        SearchResult(None, f"{NAME} 主体信息", url, "搜索摘要不是证据", "示例来源", None)
        for url in (PAIR_URL, GOV_URL)
    ]
    return {
        "baidu": MockSearchProvider("baidu", {f'"{NAME}" {CODE}': records}),
        "bocha": MockSearchProvider("bocha"),
    }


def fetcher(policy):
    def respond(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nAllow: /", headers={"content-type": "text/plain"}
            )
        body = NAME if request.url.host.endswith("gov.cn") else f"{NAME} 统一社会信用代码：{CODE}"
        return httpx.Response(
            200,
            text=f"<html><main>{body}</main></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )

    return TrustedSourceFetcher(
        policy,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        resolver=lambda host, port: ["93.184.216.34"],
    )


def step(app, request_id, searches, factory=fetcher):
    with app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        result = run_identity_step(session, user, searches, WebResearchPolicy(), factory)
        request = session.get(PersonalCompanyRequest, request_id)
        return result, request.status, request.company_id


def test_identity_corrobation_requires_body_pair_and_independent_government():
    pair = identity_evidence(NAME, CODE, f"{NAME} 统一社会信用代码：{CODE}", PAIR_URL)
    official = identity_evidence(NAME, CODE, NAME, GOV_URL)
    assert pair["pair_match"]
    assert not corroborated([pair])
    assert not corroborated([official])
    assert corroborated([pair, official])
    assert not corroborated([pair, {**official, "host": "registry.example.org"}])
    assert not corroborated([pair, official, {"conflict": True}])
    assert not identity_evidence(NAME, CODE, "其他公司 统一社会信用代码：" + CODE, PAIR_URL)[
        "pair_match"
    ]


def test_identity_search_does_not_limit_registration_evidence_to_last_year():
    provider = BaiduSearchProvider(
        api_key="fixture", timeout_seconds=2, max_response_bytes=10000, user_agent="fixture"
    )
    assert "search_recency_filter" not in provider._request_payload(
        SearchRequest(NAME, recent_only=False)
    )
    assert provider._request_payload(SearchRequest(NAME))["search_recency_filter"] == "year"


def test_identity_locates_later_labelled_fields_and_does_not_hide_conflict():
    prefix = NAME + " 网站导航 " * 1000
    pair = f"{NAME} 统一社会信用代码：{CODE}"
    result = identity_evidence(NAME, CODE, prefix + pair, PAIR_URL)
    assert result["pair_match"] and len(result["excerpt"]) < 1500
    assert CODE.lower() in result["excerpt"]
    conflict = identity_evidence(
        NAME,
        CODE,
        prefix + pair + " 无关段落 " * 300 + NAME + " 统一社会信用代码：91310000MABNKADB72",
        PAIR_URL,
    )
    assert conflict["pair_match"] and conflict["conflict"]
    distant = identity_evidence(NAME, CODE, prefix + "统一社会信用代码：" + CODE, PAIR_URL)
    assert not distant["pair_match"]


def test_identity_reads_beyond_first_excerpt_and_through_safe_redirect(migrated_app):
    request_id = setup_request(migrated_app)
    searches = providers()
    observed = []

    def factory(policy):
        def respond(request):
            observed.append(str(request.url))
            if request.url.path == "/robots.txt":
                return httpx.Response(
                    200, text="User-agent: *\nAllow: /", headers={"content-type": "text/plain"}
                )
            if request.url.host.endswith("gov.cn"):
                body = "其他公示资料 " * 800 + NAME
            elif request.url.path == "/company":
                return httpx.Response(302, headers={"location": "/registry"})
            else:
                body = NAME + "<main>导航内容 " * 400 + "</main>" * 400
                body += f"<footer>{NAME} 统一社会信用代码：{CODE}</footer>"
            return httpx.Response(200, text=body, headers={"content-type": "text/html"})

        return TrustedSourceFetcher(
            policy,
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            resolver=lambda host, port: ["93.184.216.34"],
        )

    for _ in range(4):
        _, status, company_id = step(migrated_app, request_id, searches, factory)
    assert status == "research_queued" and company_id
    assert "https://registry.example.org/registry" in observed
    with migrated_app.state.session_factory() as session:
        progress = session.get(IdentityResearchState, request_id).progress
        assert progress["fetch_calls"] == 5
        assert all(len(item["excerpt"]) < 1500 for item in progress["evidence"])
        assert sum(item["pair_match"] for item in progress["evidence"]) == 1


def test_complementary_searches_and_quality_fallback_are_bounded(migrated_app):
    request_id = setup_request(migrated_app)
    searches = {code: MockSearchProvider(code) for code in ("baidu", "bocha")}
    # Nonempty unusable discovery must still permit a different provider/query.
    records = [SearchResult(None, "无主体正文", "https://noise.example.org/page", "", "", None)]
    searches["baidu"] = MockSearchProvider("baidu", {f'"{NAME}" {CODE}': records})
    for _ in range(10):
        _, status, _ = step(migrated_app, request_id, searches)
        if status == "in_review":
            break
    assert status == "in_review"
    calls = searches["baidu"].calls + searches["bocha"].calls
    assert 4 <= len(calls) <= 6
    assert all(
        len({call.query for call in provider.calls}) == len(provider.calls)
        for provider in searches.values()
    )
    assert any(call.query == f'"{NAME}" site:gov.cn' for call in calls)
    assert any(call.query == f'"{CODE}"' for call in calls)
    assert searches["bocha"].calls


def test_exhausted_fetch_budget_never_buys_another_search(migrated_app):
    request_id = setup_request(migrated_app)
    searches = providers()
    step(migrated_app, request_id, searches)
    with migrated_app.state.session_factory() as session:
        state = session.get(IdentityResearchState, request_id)
        state.progress = {**state.progress, "candidates": [], "fetch_calls": 24}
        session.commit()
    _, status, company_id = step(migrated_app, request_id, searches)
    assert status == "in_review" and company_id is None
    assert len(searches["baidu"].calls) == 1


def test_legacy_inflight_request_continues_without_resetting_spent_budget(migrated_app):
    request_id = setup_request(migrated_app)
    searches = providers()
    step(migrated_app, request_id, searches)
    with migrated_app.state.session_factory() as session:
        state = session.get(IdentityResearchState, request_id)
        state.progress = {
            **state.progress,
            "policy_version": "public-identity-v1",
            "candidates": [],
            "fetch_calls": 8,
            "downloaded_bytes": 10000,
        }
        session.commit()
    _, status, _ = step(migrated_app, request_id, searches)
    assert status == "identity_queued"
    assert searches["baidu"].calls[-1].query == f'"{NAME}" site:gov.cn'
    with migrated_app.state.session_factory() as session:
        progress = session.get(IdentityResearchState, request_id).progress
        assert progress["search_calls"] == 2
        assert progress["fetch_calls"] == 8
        assert progress["downloaded_bytes"] == 10000
        assert progress["policy_version"] == "public-identity-v2"
        assert progress["previous_policy_versions"] == ["public-identity-v1"]


def test_other_company_search_results_are_not_fetched(migrated_app):
    request_id = setup_request(migrated_app)
    searches = {
        "baidu": MockSearchProvider(
            "baidu",
            {
                f'"{NAME}" {CODE}': [
                    SearchResult(
                        None,
                        "91310000MABNKADB72",
                        "https://other.example.org",
                        "另一家公司",
                        "",
                        None,
                    )
                ]
            },
        ),
        "bocha": MockSearchProvider("bocha"),
    }
    step(migrated_app, request_id, searches)
    with migrated_app.state.session_factory() as session:
        progress = session.get(IdentityResearchState, request_id).progress
        assert progress["candidates"] == []
        assert progress["responses"][0]["rejected_subject_results"] == 1
        assert progress.get("fetch_calls", 0) == 0
        assert progress["primary_failed"] is True


def test_legacy_other_credit_code_candidate_is_skipped_at_zero_cost(migrated_app):
    request_id = setup_request(migrated_app)
    searches = providers()
    step(migrated_app, request_id, searches)
    with migrated_app.state.session_factory() as session:
        state = session.get(IdentityResearchState, request_id)
        state.progress = {
            **state.progress,
            "candidates": [{"title": "91310000MABNKADB72", "url": "https://other.example.org"}],
        }
        session.commit()
    result, status, company_id = step(migrated_app, request_id, searches)
    assert result.external_calls == 0 and status == "identity_queued" and company_id is None
    with migrated_app.state.session_factory() as session:
        progress = session.get(IdentityResearchState, request_id).progress
        assert progress["candidate_index"] == 1
        assert progress["failures"][-1]["code"] == "discovery_subject_mismatch"
        assert len(searches["baidu"].calls) == 1


def test_resumable_identity_reserves_business_budget_and_preserves_usage(migrated_app):
    request_id = setup_request(migrated_app)
    searches = providers()
    for _ in range(4):
        result, status, company_id = step(migrated_app, request_id, searches)
    with migrated_app.state.session_factory() as session:
        assert status == "research_queued", session.get(IdentityResearchState, request_id).progress
    assert company_id
    assert len(searches["baidu"].calls) == 1
    assert searches["baidu"].calls[0].recent_only is False
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, company_id)
        assert company.identity_verification_basis == "public_crosscheck"
        assert company.tenant_id is None
        state = session.get(IdentityResearchState, request_id)
        assert len(state.progress["evidence"]) == 2
        before = session.scalar(select(func.count()).select_from(Event))
        assert (
            prepare_pending_research_requests(
                session, session.get(User, ALPHA_USER_ID), WebResearchPolicy()
            )
            == 1
        )
        job = session.scalar(
            select(CompanyResearchJob).where(CompanyResearchJob.company_id == company_id)
        )
        assert job.coverage["stats"]["search_calls"] == 0
        assert job.coverage["stats"]["fetch_calls"] == 0
        assert job.coverage["identity_usage"]["search_calls"] == 1
        assert job.coverage["identity_usage"]["fetch_calls"] == 4
        assert session.scalar(select(func.count()).select_from(Event)) == before
        assert session.scalar(select(func.sum(UsageLedger.input_tokens))) == 0
        assert session.scalar(select(func.sum(UsageLedger.external_calls))) == 5
    with TestClient(migrated_app) as client:
        response = client.get(
            f"/api/v1/companies/{company_id}", headers={"X-Demo-User-Id": str(BETA_USER_ID)}
        )
        assert response.status_code == 200
        assert "public_crosscheck" in response.text
        assert response.json()["private_events"] == []
        assert response.json()["unconfirmed_leads"] == []
        assert PAIR_URL not in response.text  # Identity research notes are not public evidence.


def test_cancel_before_start_has_zero_calls(migrated_app):
    request_id = setup_request(migrated_app)
    with migrated_app.state.session_factory() as session:
        request = session.get(PersonalCompanyRequest, request_id)
        request.status = "cancel_requested"
        session.commit()
    searches = providers()
    # status alone must be sufficient, even if a legacy caller did not set the timestamp.
    _, status, company_id = step(migrated_app, request_id, searches)
    assert status == "cancelled"
    assert company_id is None
    assert not searches["baidu"].calls


def test_identity_dry_run_discloses_both_budgets_without_mutation(migrated_app):
    request_id = setup_request(migrated_app)
    with migrated_app.state.session_factory() as session:
        result = inspect_web_research_queue(
            session, session.get(User, ALPHA_USER_ID), WebResearchPolicy()
        )
        assert result["identity_budget"] == {
            "max_search_calls": 6,
            "max_fetch_requests": 24,
            "max_download_bytes": 4_000_000,
        }
        assert result["new_company_max_search_calls"] == 10
        assert result["external_calls"] == 0
        assert session.get(IdentityResearchState, request_id) is None
        assert session.get(PersonalCompanyRequest, request_id).status == "pending"


def test_cancel_during_search_preserves_evidence_and_stops(migrated_app):
    request_id = setup_request(migrated_app)
    searches = providers()
    original = searches["baidu"].search

    def cancelling_search(query):
        with migrated_app.state.session_factory() as session:
            request = session.get(PersonalCompanyRequest, request_id)
            request.status = "cancel_requested"
            session.commit()
        return original(query)

    searches["baidu"].search = cancelling_search
    _, status, company_id = step(migrated_app, request_id, searches)
    assert status == "cancelled"
    assert company_id is None
    with migrated_app.state.session_factory() as session:
        assert len(session.get(IdentityResearchState, request_id).progress["candidates"]) == 2
        assert session.get(PersonalCompanyRequest, request_id).external_calls == 1


def test_owner_cannot_run_identity_worker(migrated_app):
    setup_request(migrated_app)
    with migrated_app.state.session_factory() as session:
        with pytest.raises(RuntimeError, match="platform_admin"):
            run_identity_step(
                session, session.get(User, BETA_USER_ID), providers(), WebResearchPolicy()
            )


def test_downgrade_refuses_to_delete_identity_audit(migrated_app):
    request_id = setup_request(migrated_app)
    step(migrated_app, request_id, providers())
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with pytest.raises(RuntimeError, match="Identity research evidence exists"):
        command.downgrade(config, "0026")
    with migrated_app.state.session_factory() as session:
        assert session.get(IdentityResearchState, request_id).progress["search_calls"] == 1


def test_search_budget_stops_before_next_call(migrated_app):
    request_id = setup_request(migrated_app)
    searches = providers()
    step(migrated_app, request_id, searches)
    with migrated_app.state.session_factory() as session:
        state = session.get(IdentityResearchState, request_id)
        state.progress = {**state.progress, "candidates": []}
        session.commit()
        result = run_identity_step(
            session,
            session.get(User, ALPHA_USER_ID),
            searches,
            replace(WebResearchPolicy(), daily_search_call_limit=1),
        )
        assert result.status == "budget_deferred"
    assert len(searches["baidu"].calls) == 1


def test_repeated_request_reuses_collected_candidates_without_new_search(migrated_app):
    first_id = setup_request(migrated_app)
    searches = providers()
    step(migrated_app, first_id, searches)
    with migrated_app.state.session_factory() as session:
        first = session.get(PersonalCompanyRequest, first_id)
        first.status = "cancelled"
        second = PersonalCompanyRequest(
            owner_user_id=ALPHA_USER_ID,
            request_type="inclusion",
            requested_name=NAME,
            requested_credit_code=CODE,
            target_key=f"credit:{CODE}",
            status="pending",
        )
        session.add(second)
        session.commit()
        second_id = second.id
    step(migrated_app, second_id, searches)
    assert len(searches["baidu"].calls) == 1
    with migrated_app.state.session_factory() as session:
        assert session.get(PersonalCompanyRequest, second_id).cache_hits == 1
        assert session.get(IdentityResearchState, second_id).progress["reused_evidence"] is True


@pytest.mark.parametrize("mode", ["missing", "interrupted", "conflict", "invalid"])
def test_no_evidence_or_conflict_never_creates_company(migrated_app, mode):
    request_id = setup_request(migrated_app)
    with migrated_app.state.session_factory() as session:
        request = session.get(PersonalCompanyRequest, request_id)
        progress = {"input_hash": hashlib.sha256(f"{NAME.casefold()}|{CODE}".encode()).hexdigest()}
        if mode == "invalid":
            request.requested_credit_code = "123"
        elif mode == "missing":
            progress["search_calls"] = 6
        elif mode == "interrupted":
            progress["in_flight"] = "search:0"
        else:
            progress["evidence"] = [{"conflict": True}]
        session.add(IdentityResearchState(request_id=request_id, progress=progress))
        session.commit()
    searches = providers()
    _, status, company_id = step(migrated_app, request_id, searches)
    assert status in {"needs_input", "in_review"}
    assert company_id is None
    assert not searches["baidu"].calls
