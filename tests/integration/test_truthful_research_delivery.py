"""M2-R 行为回归：只运行虚构身份与 Mock，不触发真实研究。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.config import WebResearchPolicy
from backend.app.database import set_request_context
from backend.app.demo import ALPHA_USER_ID, NO_ACCESS_USER_ID
from backend.app.models import CompanyResearchJob, PersonalUsageRecord, User
from backend.app.personal_features import create_personal_company_report, create_refresh_request
from backend.app.research_plan import TOPICS
from backend.app.source_fetcher import TrustedSourceFetcher
from backend.app.web_research_service import (
    prepare_pending_research_requests,
    run_web_research_worker_once,
)
from backend.app.web_search import MockSearchProvider
from tests.integration.test_bounded_web_research import SHARED_COMPANY_ID, _grant_platform_admin
from tests.integration.test_report_lifecycle_recovery import apps as apps
from tests.integration.test_report_lifecycle_recovery import database as database


def _queue(app):
    _grant_platform_admin(app)
    with app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        create_refresh_request(
            session,
            user,
            app.state.settings.personal_entitlement_policy,
            company_id=SHARED_COMPANY_ID,
        )


def test_manual_refresh_plans_all_eight_categories(migrated_app):
    _queue(migrated_app)
    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        prepare_pending_research_requests(
            session,
            user,
            WebResearchPolicy(incremental_research_enabled=True, topic_planning_enabled=True),
        )
        job = session.scalar(select(CompanyResearchJob))
        assert {g.get("topic_category") for g in job.coverage["search_groups"].values()} == set(
            TOPICS
        )
        assert job.coverage["research_scope"] == "bounded_full_scope_refresh"


@pytest.mark.network_gate
def test_non_public_dns_stops_before_search_and_voids_research_credit(migrated_app):
    _queue(migrated_app)
    primary = MockSearchProvider("baidu")
    fallback = MockSearchProvider("bocha")
    with migrated_app.state.session_factory() as session:
        result = run_web_research_worker_once(
            session,
            session.get(User, ALPHA_USER_ID),
            {"baidu": primary, "bocha": fallback},
            WebResearchPolicy(),
            fetcher_factory=lambda policy: TrustedSourceFetcher(
                policy,
                resolver=lambda *_: ["198.18.0.22"],
            ),
        )
        assert primary.calls == fallback.calls == []
        assert result.error_code == "execution_environment_non_public_dns"
        usage = session.scalar(
            select(PersonalUsageRecord).where(
                PersonalUsageRecord.operation == "company_request",
            )
        )
        assert usage.void_reason == "research_network_preflight_failed"


def test_initial_material_does_not_claim_online_research_complete(migrated_app):
    with migrated_app.state.session_factory() as session:
        result = create_personal_company_report(
            session,
            session.get(User, NO_ACCESS_USER_ID),
            migrated_app.state.settings.personal_entitlement_policy,
            migrated_app.state.settings.refresh_policy,
            SHARED_COMPANY_ID,
            idempotency_key="d" * 64,
        )
        assert "本次研究状态" in result.markdown
        assert "尚未开始" in result.markdown
        assert "未检查" in result.markdown


@pytest.mark.parametrize("limit,expected,completed_count", [(4, "partial", 2), (16, "complete", 8)])
def test_bounded_eight_category_worker_terminal_and_no_implicit_retry(
    migrated_app, limit, expected, completed_count
):
    _queue(migrated_app)
    policy = WebResearchPolicy(
        incremental_research_enabled=True,
        topic_planning_enabled=True,
        max_search_calls_per_job=limit,
    )
    providers = {code: MockSearchProvider(code) for code in ("baidu", "bocha")}
    with migrated_app.state.session_factory() as session:
        for _ in range(24):
            result = run_web_research_worker_once(
                session, session.get(User, ALPHA_USER_ID), providers, policy
            )
            if result.status in {"completed", "failed", "budget_deferred"}:
                break
        job = session.scalar(select(CompanyResearchJob))
        assert job.coverage["completion"]["status"] == expected
        rows = job.coverage["completion"]["categories"]
        assert len(rows) == 8 and all(r["planned"] for r in rows)
        assert sum(r["status"] == "completed" for r in rows) == completed_count
        assert sum(len(p.calls) for p in providers.values()) == limit
        run_web_research_worker_once(session, session.get(User, ALPHA_USER_ID), providers, policy)
        assert sum(len(p.calls) for p in providers.values()) == limit


@pytest.mark.network_gate
def test_rls_admin_preflight_does_not_charge_another_request_owner(apps):
    owner, app = apps
    _grant_platform_admin(owner)
    with app.state.session_factory() as session:
        user = session.get(User, NO_ACCESS_USER_ID)
        set_request_context(session, user.id, user.tenant_id)
        create_refresh_request(
            session, user, app.state.settings.personal_entitlement_policy, SHARED_COMPANY_ID
        )
    primary, fallback = MockSearchProvider("baidu"), MockSearchProvider("bocha")
    with app.state.session_factory() as session:
        result = run_web_research_worker_once(
            session,
            session.get(User, ALPHA_USER_ID),
            {"baidu": primary, "bocha": fallback},
            WebResearchPolicy(),
            fetcher_factory=lambda policy: TrustedSourceFetcher(
                policy, resolver=lambda *_: ["198.18.0.1"]
            ),
        )
        assert result.error_code == "execution_environment_non_public_dns"
        assert not primary.calls and not fallback.calls
    with TestClient(app) as client:
        headers = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}
        request = client.get("/api/v1/me/company-requests", headers=headers).json()[0]
        assert request["last_error_code"] == "network_environment_blocked"
        assert request["research_result"]["completion"]["status"] == "not_run"
        assert "198.18" not in str(request) and "execution_environment" not in str(request)
        usage = client.get("/api/v1/me/usage", headers=headers).json()
        assert usage["company_requests"]["used"] == 0
        assert usage["daily_company_requests"]["used"] == 1
