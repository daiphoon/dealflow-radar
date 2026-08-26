from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.config import OnDemandResearchPolicy, PersonalEntitlementPolicy
from backend.app.demo import ALPHA_USER_ID, BETA_USER_ID, NO_ACCESS_USER_ID
from backend.app.models import (
    SYSTEM_RESTRICTED_SCOPE,
    Company,
    CompanyResearchJob,
    CompanySnapshot,
    Event,
    EventEvidence,
    OfficialIdentityVerification,
    PersonalCompanyReport,
    PersonalCompanyRequest,
    PersonalQuotaIncreaseRequest,
    PersonalUsageRecord,
    RawDocument,
    Role,
    UsageLedger,
    User,
    UserRoleAssignment,
)
from backend.app.on_demand_research import run_on_demand_worker_once
from backend.app.tianyancha import (
    TianyanchaIdentityLookupResult,
    TianyanchaProviderError,
    TianyanchaResearchModuleCode,
    TianyanchaResearchModuleResult,
    TianyanchaResearchRecord,
)

PERSONAL_HEADERS = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}
ALPHA_HEADERS = {"X-Demo-User-Id": str(ALPHA_USER_ID)}
BETA_HEADERS = {"X-Demo-User-Id": str(BETA_USER_ID)}
NEW_COMPANY_NAME = "示例按需研究科技有限公司"
NEW_COMPANY_CODE = "913100001234567896"


class _IdentityProvider:
    code = "mock_tianyancha_identity"

    def __init__(self, *, cache_hit: bool = False) -> None:
        self.external_calls = 0
        self.cache_hits = 0
        self.cache_hit = cache_hit

    def lookup_identity(
        self,
        *,
        company_name: str | None,
        credit_code: str | None,
    ) -> TianyanchaIdentityLookupResult:
        if self.cache_hit:
            self.cache_hits += 1
        else:
            self.external_calls += 1 if credit_code else 2
        return TianyanchaIdentityLookupResult(
            query_text=credit_code or company_name or "",
            legal_name=NEW_COMPANY_NAME,
            credit_code=NEW_COMPANY_CODE,
            registered_region="上海市/浦东新区",
            registration_status="存续",
            registration_authority="上海市市场监督管理局",
            provider_company_id="mock-company-001",
            canonical_url="https://www.tianyancha.com/company/mock-company-001",
            checked_at=datetime(2026, 8, 26, 9, 0, tzinfo=UTC),
            response_hash="a" * 64,
            candidate_count=1,
        )

    def lookup_cached_identity(
        self,
        *,
        company_name: str | None,
        credit_code: str | None,
    ) -> TianyanchaIdentityLookupResult | None:
        if not self.cache_hit:
            return None
        return self.lookup_identity(company_name=company_name, credit_code=credit_code)


class _CallbackIdentityProvider(_IdentityProvider):
    def __init__(self, callback: Callable[[], None], *, cache_hit: bool = False) -> None:
        super().__init__(cache_hit=cache_hit)
        self.callback = callback

    def lookup_identity(
        self,
        *,
        company_name: str | None,
        credit_code: str | None,
    ) -> TianyanchaIdentityLookupResult:
        result = super().lookup_identity(
            company_name=company_name,
            credit_code=credit_code,
        )
        self.callback()
        return result


class _ResearchProvider(_IdentityProvider):
    def __init__(
        self,
        *,
        cached_modules: bool = False,
        checked_at: datetime | None = None,
    ) -> None:
        super().__init__()
        self.cached_modules = cached_modules
        self.checked_at = checked_at or datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        self.research_calls: list[str] = []

    def _module_result(
        self,
        module_code: TianyanchaResearchModuleCode,
    ) -> TianyanchaResearchModuleResult:
        definitions: dict[str, tuple[str, str, str, str, str]] = {
            "company_base": (
                "governance_people",
                "licensed_company_base_overview",
                "工商与股东基础资料已更新",
                "授权来源返回了工商与股东基础记录。",
                "verified_fact",
            ),
            "risk": (
                "legal_compliance",
                "licensed_risk_overview",
                "司法与合规风险待核实线索",
                "授权来源返回了风险记录，仍需核对详情及后续状态。",
                "unconfirmed_lead",
            ),
            "intellectual_property": (
                "product_technology",
                "licensed_intellectual_property_overview",
                "知识产权资料已更新",
                "授权来源返回了知识产权记录。",
                "verified_fact",
            ),
            "history": (
                "governance_people",
                "licensed_history_overview",
                "历史变更资料已更新",
                "授权来源返回了历史变更记录。",
                "verified_fact",
            ),
            "executive": (
                "governance_people",
                "licensed_executive_overview",
                "董监高与人员待核实线索",
                "授权来源返回了人员风险记录，仍需核对同名与任职关系。",
                "unconfirmed_lead",
            ),
        }
        if module_code == "operation":
            return TianyanchaResearchModuleResult(
                module_code=module_code,
                tool_name="mock_operation_overview",
                checked_at=self.checked_at,
                response_hash="4" * 64,
                records=[],
                no_reliable_data=True,
                warnings=[],
            )
        event_type, subtype, title, summary, classification = definitions[module_code]
        is_lead = classification == "unconfirmed_lead"
        record = TianyanchaResearchRecord(
            external_record_id=f"mock-{module_code}-record-1",
            title=title,
            summary=summary,
            event_type=event_type,
            event_subtype=subtype,
            direction="negative" if is_lead else "neutral",
            materiality_score=75 if is_lead else 45,
            risk_severity="high" if is_lead else "none",
            confidence_score=Decimal("0.800" if is_lead else "0.950"),
            facts=[{"name": "来源记录数", "value": "1", "unit": "条"}],
            uncertainties=["需人工核对"] if is_lead else [],
            occurred_at=datetime(2026, 8, 20, 0, 0, tzinfo=UTC),
            published_on=date(2026, 8, 21),
            canonical_url="https://www.tianyancha.com/company/mock-company-001",
            evidence_excerpt=summary,
            classification=classification,
            classification_reasons=[
                "licensed_source_risk_record_requires_review"
                if is_lead
                else "licensed_structured_routine_fact"
            ],
        )
        return TianyanchaResearchModuleResult(
            module_code=module_code,
            tool_name=f"mock_{module_code}_overview",
            checked_at=self.checked_at,
            response_hash={
                "company_base": "0",
                "risk": "1",
                "intellectual_property": "2",
                "history": "3",
                "executive": "5",
            }[module_code]
            * 64,
            records=[record],
            no_reliable_data=False,
            warnings=[],
        )

    def lookup_cached_research_module(
        self,
        *,
        module_code: TianyanchaResearchModuleCode,
        legal_name: str,
        credit_code: str,
        provider_company_id: str | None,
    ) -> TianyanchaResearchModuleResult | None:
        del legal_name, credit_code, provider_company_id
        if not self.cached_modules:
            return None
        self.cache_hits += 1
        return self._module_result(module_code)

    def lookup_research_module(
        self,
        *,
        module_code: TianyanchaResearchModuleCode,
        legal_name: str,
        credit_code: str,
        provider_company_id: str | None,
    ) -> TianyanchaResearchModuleResult:
        del legal_name, credit_code, provider_company_id
        self.external_calls += 1
        self.research_calls.append(module_code)
        return self._module_result(module_code)


class _CallbackResearchProvider(_ResearchProvider):
    def __init__(self, callback: Callable[[], None]) -> None:
        super().__init__()
        self.callback = callback

    def lookup_research_module(
        self,
        *,
        module_code: TianyanchaResearchModuleCode,
        legal_name: str,
        credit_code: str,
        provider_company_id: str | None,
    ) -> TianyanchaResearchModuleResult:
        result = super().lookup_research_module(
            module_code=module_code,
            legal_name=legal_name,
            credit_code=credit_code,
            provider_company_id=provider_company_id,
        )
        self.callback()
        return result


class _FailingResearchProvider(_ResearchProvider):
    def __init__(self, failed_module: TianyanchaResearchModuleCode) -> None:
        super().__init__()
        self.failed_module = failed_module

    def lookup_research_module(
        self,
        *,
        module_code: TianyanchaResearchModuleCode,
        legal_name: str,
        credit_code: str,
        provider_company_id: str | None,
    ) -> TianyanchaResearchModuleResult:
        if module_code == self.failed_module:
            self.external_calls += 1
            self.research_calls.append(module_code)
            raise TianyanchaProviderError("mock module failure")
        return super().lookup_research_module(
            module_code=module_code,
            legal_name=legal_name,
            credit_code=credit_code,
            provider_company_id=provider_company_id,
        )


def _enable_on_demand(app: FastAPI, *, daily: int = 10, monthly: int = 30) -> None:
    app.state.settings = replace(
        app.state.settings,
        on_demand_research_enabled=True,
        personal_entitlement_policy=PersonalEntitlementPolicy(
            monthly_search_limit=100,
            watchlist_company_limit=20,
            monthly_report_limit=10,
            daily_request_limit=daily,
            monthly_request_limit=monthly,
            request_cooldown_hours=24,
        ),
    )


def _grant_platform_admin(app: FastAPI) -> None:
    with app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.code == "platform_admin"))
        assert role is not None
        existing = session.scalar(
            select(UserRoleAssignment).where(
                UserRoleAssignment.user_id == ALPHA_USER_ID,
                UserRoleAssignment.role_id == role.id,
            )
        )
        if existing is None:
            session.add(
                UserRoleAssignment(
                    id=uuid4(),
                    user_id=ALPHA_USER_ID,
                    role_id=role.id,
                    scope_id=None,
                    valid_until=None,
                )
            )
            session.commit()


def _run_worker(
    app: FastAPI,
    provider: _IdentityProvider,
    *,
    retry_limit: int = 0,
    research_calls_enabled: bool = False,
):
    with app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        return run_on_demand_worker_once(
            session,
            user,
            provider,
            app.state.settings.on_demand_research_policy,
            provider_retry_limit=retry_limit,
            research_calls_enabled=research_calls_enabled,
        )


def _prepare_new_company_research(
    client: TestClient,
    app: FastAPI,
    provider: _IdentityProvider,
) -> tuple[str, UUID, UUID]:
    created = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=PERSONAL_HEADERS,
        json={"company_name": NEW_COMPANY_NAME, "credit_code": NEW_COMPANY_CODE},
    )
    assert created.status_code == 200
    assert _run_worker(app, provider).outcome == "verified_candidate"
    confirmed = client.post(
        f"/api/v1/me/company-requests/{created.json()['id']}/confirm",
        headers=PERSONAL_HEADERS,
    )
    assert confirmed.status_code == 200
    prepared = _run_worker(app, provider)
    assert prepared.outcome == "research_job_queued"
    assert prepared.company_id is not None
    assert prepared.research_job_id is not None
    return created.json()["id"], prepared.company_id, prepared.research_job_id


def test_identity_confirmation_creates_one_shared_company_and_reuses_one_job(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _enable_on_demand(migrated_app)
    _grant_platform_admin(migrated_app)
    provider = _IdentityProvider()

    created = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=PERSONAL_HEADERS,
        json={"company_name": NEW_COMPANY_NAME, "credit_code": NEW_COMPANY_CODE},
    )
    assert created.status_code == 200
    assert created.json()["status"] == "identity_queued"
    assert created.json()["can_cancel"] is True
    assert provider.external_calls == 0

    with migrated_app.state.session_factory() as session:
        company = session.scalar(select(Company).where(Company.credit_code == NEW_COMPANY_CODE))
        assert company is None

    identity_result = _run_worker(migrated_app, provider)
    assert identity_result.outcome == "verified_candidate"
    assert identity_result.external_calls == 1
    pending_confirmation = client.get(
        "/api/v1/me/company-requests",
        headers=PERSONAL_HEADERS,
    ).json()[0]
    assert pending_confirmation["status"] == "awaiting_confirmation"
    assert pending_confirmation["resolved_legal_name"] == NEW_COMPANY_NAME
    assert pending_confirmation["resolved_credit_code"] == NEW_COMPANY_CODE
    assert pending_confirmation["can_confirm"] is True
    assert (
        client.post(
            f"/api/v1/me/company-requests/{created.json()['id']}/confirm",
            headers=BETA_HEADERS,
        ).status_code
        == 404
    )

    confirmed = client.post(
        f"/api/v1/me/company-requests/{created.json()['id']}/confirm",
        headers=PERSONAL_HEADERS,
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "research_queued"

    prepared = _run_worker(migrated_app, provider)
    assert prepared.outcome == "research_job_queued"
    assert prepared.company_id is not None
    assert prepared.research_job_id is not None

    with migrated_app.state.session_factory() as session:
        company = session.scalar(select(Company).where(Company.credit_code == NEW_COMPANY_CODE))
        assert company is not None
        assert company.id == prepared.company_id
        assert company.tenant_id is None
        assert company.visibility_scope == "public"
        assert company.identity_status == "verified"
        assert session.scalar(select(func.count()).select_from(CompanyResearchJob)) == 1
        assert session.scalar(select(func.count()).select_from(OfficialIdentityVerification)) == 1
        identity_document = session.scalar(
            select(RawDocument).where(RawDocument.visibility_scope == SYSTEM_RESTRICTED_SCOPE)
        )
        assert identity_document is not None
        assert identity_document.owner_user_id is None
        assert identity_document.owner_tenant_id is None
        assert session.scalar(select(func.count()).select_from(Event)) == 0
        assert session.scalar(select(func.count()).select_from(PersonalCompanyReport)) == 0
        usage = session.scalar(
            select(UsageLedger).where(UsageLedger.operation == "on_demand_identity_lookup")
        )
        assert usage is not None
        assert usage.external_calls == 1
        assert usage.input_tokens == usage.output_tokens == 0

    search = client.get(
        "/api/v1/companies/search",
        params={"q": NEW_COMPANY_CODE},
        headers=PERSONAL_HEADERS,
    )
    assert search.status_code == 200
    assert [item["id"] for item in search.json()] == [str(prepared.company_id)]

    refresh = client.post(
        f"/api/v1/me/company-requests/refresh/{prepared.company_id}",
        headers=BETA_HEADERS,
    )
    assert refresh.status_code == 200
    assert refresh.json()["status"] == "research_queued"
    reused_job = _run_worker(migrated_app, provider)
    assert reused_job.outcome == "research_job_reused"
    assert reused_job.research_job_id == prepared.research_job_id

    first_cancel = client.post(
        f"/api/v1/me/company-requests/{created.json()['id']}/cancel",
        headers=PERSONAL_HEADERS,
        json={"reason": "测试共享任务仍有另一名请求者"},
    )
    assert first_cancel.status_code == 200
    assert first_cancel.json()["status"] == "cancelled"
    with migrated_app.state.session_factory() as session:
        job = session.get(CompanyResearchJob, prepared.research_job_id)
        assert job is not None
        assert job.status == "queued"

    second_cancel = client.post(
        f"/api/v1/me/company-requests/{refresh.json()['id']}/cancel",
        headers=BETA_HEADERS,
        json={"reason": "测试最后一名请求者取消"},
    )
    assert second_cancel.status_code == 200
    assert second_cancel.json()["status"] == "cancelled"
    with migrated_app.state.session_factory() as session:
        job = session.get(CompanyResearchJob, prepared.research_job_id)
        assert job is not None
        assert job.status == "queued"

    orphaned = _run_worker(migrated_app, provider)
    assert orphaned.outcome == "orphaned_research_job_cancelled"
    with migrated_app.state.session_factory() as session:
        job = session.get(CompanyResearchJob, prepared.research_job_id)
        assert job is not None
        assert job.status == "cancelled"
        assert job.cancelled_at is not None


def test_cancel_before_external_call_refunds_monthly_but_not_daily_and_keeps_cooldown(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _enable_on_demand(migrated_app)
    _grant_platform_admin(migrated_app)
    created = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=PERSONAL_HEADERS,
        json={"company_name": "示例待取消研究有限公司"},
    )
    assert created.status_code == 200

    forbidden = client.post(
        f"/api/v1/me/company-requests/{created.json()['id']}/cancel",
        headers=BETA_HEADERS,
        json={},
    )
    assert forbidden.status_code == 404
    cancelled = client.post(
        f"/api/v1/me/company-requests/{created.json()['id']}/cancel",
        headers=PERSONAL_HEADERS,
        json={"reason": "发现主体输入错误"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancellation_reason"] == "发现主体输入错误"

    usage = client.get("/api/v1/me/usage", headers=PERSONAL_HEADERS).json()
    assert usage["daily_company_requests"]["used"] == 1
    assert usage["company_requests"]["used"] == 0

    repeated = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=PERSONAL_HEADERS,
        json={"company_name": "示例待取消研究有限公司"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == created.json()["id"]
    assert repeated.json()["reused"] is True
    usage_after = client.get("/api/v1/me/usage", headers=PERSONAL_HEADERS).json()
    assert usage_after["daily_company_requests"]["used"] == 1
    assert usage_after["company_requests"]["used"] == 0

    with migrated_app.state.session_factory() as session:
        record = session.scalar(
            select(PersonalUsageRecord).where(
                PersonalUsageRecord.resource_id == UUID(created.json()["id"])
            )
        )
        assert record is not None
        assert record.voided_at is not None
        assert record.void_reason == "cancelled_before_external_call"


def test_cancel_during_identity_call_finishes_current_call_and_keeps_monthly_usage(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _enable_on_demand(migrated_app)
    _grant_platform_admin(migrated_app)
    created = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=PERSONAL_HEADERS,
        json={"company_name": NEW_COMPANY_NAME, "credit_code": NEW_COMPANY_CODE},
    )
    assert created.status_code == 200
    cancellation_statuses: list[str] = []

    def cancel_in_flight() -> None:
        response = client.post(
            f"/api/v1/me/company-requests/{created.json()['id']}/cancel",
            headers=PERSONAL_HEADERS,
            json={"reason": "身份查询已开始后取消"},
        )
        assert response.status_code == 200
        cancellation_statuses.append(response.json()["status"])

    result = _run_worker(migrated_app, _CallbackIdentityProvider(cancel_in_flight))

    assert cancellation_statuses == ["cancel_requested"]
    assert result.outcome == "cancelled_after_identity"
    assert result.external_calls == 1
    request = client.get("/api/v1/me/company-requests", headers=PERSONAL_HEADERS).json()[0]
    assert request["status"] == "cancelled"
    assert request["external_calls"] == 0
    assert request["cache_hits"] == 0
    platform_request = client.get(
        "/api/v1/platform/company-requests",
        headers=ALPHA_HEADERS,
    ).json()[0]
    assert platform_request["external_calls"] == 1
    usage = client.get("/api/v1/me/usage", headers=PERSONAL_HEADERS).json()
    assert usage["daily_company_requests"]["used"] == 1
    assert usage["company_requests"]["used"] == 1

    with migrated_app.state.session_factory() as session:
        record = session.scalar(
            select(PersonalUsageRecord).where(
                PersonalUsageRecord.resource_id == UUID(created.json()["id"])
            )
        )
        assert record is not None
        assert record.voided_at is None
        assert (
            session.scalar(select(Company).where(Company.credit_code == NEW_COMPANY_CODE)) is None
        )
        assert session.scalar(select(func.count()).select_from(CompanyResearchJob)) == 0


def test_cancel_during_cached_identity_lookup_refunds_monthly_usage(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _enable_on_demand(migrated_app)
    _grant_platform_admin(migrated_app)
    created = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=PERSONAL_HEADERS,
        json={"company_name": NEW_COMPANY_NAME, "credit_code": NEW_COMPANY_CODE},
    )
    assert created.status_code == 200

    def cancel_in_flight() -> None:
        response = client.post(
            f"/api/v1/me/company-requests/{created.json()['id']}/cancel",
            headers=PERSONAL_HEADERS,
            json={"reason": "缓存读取期间取消"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "cancel_requested"

    result = _run_worker(
        migrated_app,
        _CallbackIdentityProvider(cancel_in_flight, cache_hit=True),
    )

    assert result.outcome == "cancelled_after_identity"
    assert result.external_calls == 0
    assert result.cache_hits == 1
    personal_request = client.get(
        "/api/v1/me/company-requests",
        headers=PERSONAL_HEADERS,
    ).json()[0]
    assert personal_request["external_calls"] == 0
    assert personal_request["cache_hits"] == 0
    platform_request = client.get(
        "/api/v1/platform/company-requests",
        headers=ALPHA_HEADERS,
    ).json()[0]
    assert platform_request["cache_hits"] == 1
    usage = client.get("/api/v1/me/usage", headers=PERSONAL_HEADERS).json()
    assert usage["daily_company_requests"]["used"] == 1
    assert usage["company_requests"]["used"] == 0


def test_temporary_quota_requires_platform_decision_and_expires(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _enable_on_demand(migrated_app, daily=1, monthly=1)
    first = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=PERSONAL_HEADERS,
        json={"company_name": "示例额度公司甲有限公司"},
    )
    assert first.status_code == 200
    blocked = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=PERSONAL_HEADERS,
        json={"company_name": "示例额度公司乙有限公司"},
    )
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["feature"] == "daily_company_request"

    requested = client.post(
        "/api/v1/me/quota-increase-requests",
        headers=PERSONAL_HEADERS,
        json={
            "requested_daily_extra": 1,
            "requested_monthly_extra": 2,
            "reason": "测试期间需要核验另一家公司",
        },
    )
    assert requested.status_code == 200
    repeated = client.post(
        "/api/v1/me/quota-increase-requests",
        headers=PERSONAL_HEADERS,
        json={
            "requested_daily_extra": 20,
            "requested_monthly_extra": 20,
            "reason": "重复申请不得替换原申请",
        },
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == requested.json()["id"]
    assert (
        client.get("/api/v1/platform/quota-increase-requests", headers=ALPHA_HEADERS).status_code
        == 403
    )

    _grant_platform_admin(migrated_app)
    queue = client.get(
        "/api/v1/platform/quota-increase-requests",
        headers=ALPHA_HEADERS,
    )
    assert queue.status_code == 200
    assert [row["id"] for row in queue.json()] == [requested.json()["id"]]
    approved = client.patch(
        f"/api/v1/platform/quota-increase-requests/{requested.json()['id']}",
        headers=ALPHA_HEADERS,
        json={
            "status": "approved",
            "approved_daily_extra": 1,
            "approved_monthly_extra": 2,
            "effective_until": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "reason": "仅在本次测试窗口临时增加",
        },
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    usage = client.get("/api/v1/me/usage", headers=PERSONAL_HEADERS).json()
    assert usage["daily_company_requests"]["limit"] == 2
    assert usage["company_requests"]["limit"] == 3

    second = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=PERSONAL_HEADERS,
        json={"company_name": "示例额度公司乙有限公司"},
    )
    assert second.status_code == 200
    with migrated_app.state.session_factory() as session:
        quota_request = session.scalar(select(PersonalQuotaIncreaseRequest))
        assert quota_request is not None
        quota_request.effective_until = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()

    expired_usage = client.get("/api/v1/me/usage", headers=PERSONAL_HEADERS).json()
    assert expired_usage["daily_company_requests"]["limit"] == 1
    assert expired_usage["company_requests"]["limit"] == 1
    quota_history = client.get(
        "/api/v1/me/quota-increase-requests",
        headers=PERSONAL_HEADERS,
    )
    assert quota_history.status_code == 200
    assert quota_history.json()[0]["status"] == "expired"


def test_provider_budget_defers_before_any_call(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _enable_on_demand(migrated_app)
    _grant_platform_admin(migrated_app)
    migrated_app.state.settings = replace(
        migrated_app.state.settings,
        on_demand_research_policy=OnDemandResearchPolicy(
            provider_daily_call_limit=2,
            provider_monthly_call_limit=10,
            provider_reserve_percent=50,
        ),
    )
    created = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=PERSONAL_HEADERS,
        json={"company_name": NEW_COMPANY_NAME, "credit_code": NEW_COMPANY_CODE},
    )
    assert created.status_code == 200
    provider = _IdentityProvider()

    result = _run_worker(migrated_app, provider, retry_limit=1)

    assert result.outcome == "provider_daily_limit"
    assert provider.external_calls == 0
    request = client.get("/api/v1/me/company-requests", headers=PERSONAL_HEADERS).json()[0]
    assert request["status"] == "budget_deferred"
    assert request["last_error_code"] == "provider_daily_limit"
    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 0


def test_fresh_identity_cache_is_used_even_when_provider_budget_is_exhausted(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _enable_on_demand(migrated_app)
    _grant_platform_admin(migrated_app)
    migrated_app.state.settings = replace(
        migrated_app.state.settings,
        on_demand_research_policy=OnDemandResearchPolicy(
            provider_daily_call_limit=2,
            provider_monthly_call_limit=10,
            provider_reserve_percent=50,
        ),
    )
    created = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=PERSONAL_HEADERS,
        json={"company_name": NEW_COMPANY_NAME, "credit_code": NEW_COMPANY_CODE},
    )
    assert created.status_code == 200
    provider = _IdentityProvider(cache_hit=True)

    result = _run_worker(migrated_app, provider, retry_limit=1)

    assert result.outcome == "verified_candidate"
    assert result.external_calls == 0
    assert result.cache_hits == 1
    request = client.get("/api/v1/me/company-requests", headers=PERSONAL_HEADERS).json()[0]
    assert request["status"] == "awaiting_confirmation"


def test_existing_private_company_is_not_silently_promoted(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _enable_on_demand(migrated_app)
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        beta_user = session.get(User, BETA_USER_ID)
        assert beta_user is not None
        private_company = Company(
            tenant_id=beta_user.tenant_id,
            legal_name=NEW_COMPANY_NAME,
            credit_code=NEW_COMPANY_CODE,
            registered_region="上海市/浦东新区",
            identity_status="verified",
            identity_verification_basis="licensed_business_data",
            visibility_scope="tenant",
        )
        session.add(private_company)
        session.commit()
        private_company_id = private_company.id

    created = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=PERSONAL_HEADERS,
        json={"credit_code": NEW_COMPANY_CODE},
    )
    assert created.status_code == 200
    provider = _IdentityProvider()
    assert _run_worker(migrated_app, provider).outcome == "verified_candidate"
    confirmed = client.post(
        f"/api/v1/me/company-requests/{created.json()['id']}/confirm",
        headers=PERSONAL_HEADERS,
    )
    assert confirmed.status_code == 200

    prepared = _run_worker(migrated_app, provider)

    assert prepared.outcome == "existing_private_company_requires_admin"
    request = client.get("/api/v1/me/company-requests", headers=PERSONAL_HEADERS).json()[0]
    assert request["status"] == "in_review"
    assert request["last_error_code"] == "manual_review_required"
    assert request["status_message"] == "该申请需要进一步核验，处理完成后会更新结果。"
    platform_request = client.get(
        "/api/v1/platform/company-requests",
        headers=ALPHA_HEADERS,
    ).json()[0]
    assert platform_request["last_error_code"] == "existing_private_company_requires_admin"
    assert "受限档案" in platform_request["status_message"]
    legacy_decision = client.patch(
        f"/api/v1/platform/company-requests/{created.json()['id']}",
        headers=ALPHA_HEADERS,
        json={"status": "completed", "reason": "不得用旧入口伪造目录合并"},
    )
    assert legacy_decision.status_code == 409
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, private_company_id)
        assert company is not None
        assert company.tenant_id is not None
        assert company.visibility_scope == "tenant"
        assert session.scalar(select(func.count()).select_from(CompanyResearchJob)) == 0
        assert session.scalar(select(func.count()).select_from(RawDocument)) == 0


def test_six_module_research_separates_verified_facts_leads_and_reuses_cache(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _enable_on_demand(migrated_app)
    _grant_platform_admin(migrated_app)
    provider = _ResearchProvider()
    _, company_id, research_job_id = _prepare_new_company_research(
        client,
        migrated_app,
        provider,
    )

    outcomes = [
        _run_worker(
            migrated_app,
            provider,
            research_calls_enabled=True,
        ).outcome
        for _ in range(6)
    ]

    assert provider.research_calls == [
        "company_base",
        "risk",
        "intellectual_property",
        "operation",
        "history",
        "executive",
    ]
    assert outcomes == [
        "module_completed",
        "module_completed",
        "module_completed",
        "module_completed",
        "module_completed",
        "research_completed",
    ]
    request = client.get(
        "/api/v1/me/company-requests",
        headers=PERSONAL_HEADERS,
    ).json()[0]
    assert request["status"] == "completed"
    assert request["research_modules"] == {
        "company_base": "completed",
        "risk": "completed",
        "intellectual_property": "completed",
        "operation": "no_data",
        "history": "completed",
        "executive": "completed",
    }

    personal_detail = client.get(
        f"/api/v1/companies/{company_id}",
        headers=PERSONAL_HEADERS,
    )
    other_tenant_detail = client.get(
        f"/api/v1/companies/{company_id}",
        headers=BETA_HEADERS,
    )
    assert personal_detail.status_code == other_tenant_detail.status_code == 200
    for detail in (personal_detail.json(), other_tenant_detail.json()):
        assert len(detail["events"]) == 3
        assert len(detail["platform_unconfirmed_leads"]) == 2
        assert detail["private_events"] == []
        assert detail["unconfirmed_leads"] == []
        assert detail["investments"] == []
        assert "经营与公示：暂无可靠公开数据。" in detail["information_gaps"]
        assert all(event["evidence"] for event in detail["events"])
        assert all(event["evidence"] for event in detail["platform_unconfirmed_leads"])

    with migrated_app.state.session_factory() as session:
        job = session.get(CompanyResearchJob, research_job_id)
        assert job is not None
        assert job.status == "completed"
        assert job.external_calls == 6
        assert job.input_tokens == job.output_tokens == 0
        assert session.scalar(select(func.count()).select_from(Event)) == 5
        assert session.scalar(select(func.count()).select_from(EventEvidence)) == 5
        assert (
            session.scalar(
                select(func.count())
                .select_from(RawDocument)
                .where(RawDocument.visibility_scope == SYSTEM_RESTRICTED_SCOPE)
            )
            == 6
        )
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == company_id,
                CompanySnapshot.is_current.is_(True),
            )
        )
        assert snapshot is not None
        assert snapshot.summary["research_modules"]["operation"] == "no_data"
        research_usage = list(
            session.scalars(
                select(UsageLedger).where(UsageLedger.operation == "on_demand_research_module")
            )
        )
        assert len(research_usage) == 6
        assert sum(row.external_calls for row in research_usage) == 6
        assert sum(row.input_tokens + row.output_tokens for row in research_usage) == 0

    cached_provider = _ResearchProvider(cached_modules=True)
    refreshed = client.post(
        f"/api/v1/me/company-requests/refresh/{company_id}",
        headers=BETA_HEADERS,
    )
    assert refreshed.status_code == 200
    assert _run_worker(migrated_app, cached_provider).outcome == "research_job_queued"
    cached_outcomes = [
        _run_worker(
            migrated_app,
            cached_provider,
            research_calls_enabled=True,
        ).outcome
        for _ in range(6)
    ]
    assert cached_outcomes[-1] == "research_completed"
    assert cached_provider.external_calls == 0
    assert cached_provider.cache_hits == 6
    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Event)) == 5
        assert session.scalar(select(func.count()).select_from(EventEvidence)) == 5
        assert (
            session.scalar(
                select(func.count())
                .select_from(RawDocument)
                .where(RawDocument.visibility_scope == SYSTEM_RESTRICTED_SCOPE)
            )
            == 6
        )

    later_provider = _ResearchProvider(checked_at=datetime(2026, 8, 27, 10, 0, tzinfo=UTC))
    later_refresh = client.post(
        f"/api/v1/me/company-requests/refresh/{company_id}",
        headers=ALPHA_HEADERS,
    )
    assert later_refresh.status_code == 200
    assert _run_worker(migrated_app, later_provider).outcome == "research_job_queued"
    for _ in range(6):
        _run_worker(
            migrated_app,
            later_provider,
            research_calls_enabled=True,
        )
    assert later_provider.external_calls == 6
    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Event)) == 5
        assert session.scalar(select(func.count()).select_from(EventEvidence)) == 5
        assert (
            session.scalar(
                select(func.count())
                .select_from(RawDocument)
                .where(RawDocument.visibility_scope == SYSTEM_RESTRICTED_SCOPE)
            )
            == 6
        )


def test_cancel_during_research_keeps_current_result_and_stops_later_modules(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _enable_on_demand(migrated_app)
    _grant_platform_admin(migrated_app)
    identity_provider = _IdentityProvider()
    request_id, company_id, research_job_id = _prepare_new_company_research(
        client,
        migrated_app,
        identity_provider,
    )
    cancellation_statuses: list[str] = []

    def cancel_in_flight() -> None:
        response = client.post(
            f"/api/v1/me/company-requests/{request_id}/cancel",
            headers=PERSONAL_HEADERS,
            json={"reason": "研究开始后发现查询目标错误"},
        )
        assert response.status_code == 200
        cancellation_statuses.append(response.json()["status"])

    provider = _CallbackResearchProvider(cancel_in_flight)
    result = _run_worker(
        migrated_app,
        provider,
        research_calls_enabled=True,
    )

    assert cancellation_statuses == ["cancel_requested"]
    assert result.outcome == "cancelled_after_module"
    assert provider.research_calls == ["company_base"]
    request = client.get(
        "/api/v1/me/company-requests",
        headers=PERSONAL_HEADERS,
    ).json()[0]
    assert request["status"] == "cancelled"
    assert request["research_modules"]["company_base"] == "completed"
    assert request["research_modules"]["risk"] == "pending"
    detail = client.get(
        f"/api/v1/companies/{company_id}",
        headers=PERSONAL_HEADERS,
    ).json()
    assert [event["title"] for event in detail["events"]] == ["工商与股东基础资料已更新"]
    assert detail["platform_unconfirmed_leads"] == []

    with migrated_app.state.session_factory() as session:
        job = session.get(CompanyResearchJob, research_job_id)
        assert job is not None
        assert job.status == "cancelled"
        assert job.external_calls == 1
        persisted_request = session.get(PersonalCompanyRequest, UUID(request_id))
        assert persisted_request is not None
        usage = session.scalar(
            select(PersonalUsageRecord).where(PersonalUsageRecord.resource_id == UUID(request_id))
        )
        assert usage is not None
        assert usage.voided_at is None


def test_failed_module_does_not_erase_prior_results_and_can_resume(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _enable_on_demand(migrated_app)
    _grant_platform_admin(migrated_app)
    provider = _FailingResearchProvider("risk")
    _, company_id, research_job_id = _prepare_new_company_research(
        client,
        migrated_app,
        provider,
    )

    results = [
        _run_worker(
            migrated_app,
            provider,
            research_calls_enabled=True,
        )
        for _ in range(6)
    ]

    assert results[1].outcome == "module_failed_continuing"
    assert results[-1].outcome == "research_partial"
    partial_detail = client.get(
        f"/api/v1/companies/{company_id}",
        headers=PERSONAL_HEADERS,
    ).json()
    assert len(partial_detail["events"]) == 3
    assert len(partial_detail["platform_unconfirmed_leads"]) == 1
    with migrated_app.state.session_factory() as session:
        job = session.get(CompanyResearchJob, research_job_id)
        assert job is not None
        assert job.status == "partial"
        assert job.coverage["modules"]["risk"]["status"] == "failed"

    resumed = client.post(
        f"/api/v1/me/company-requests/refresh/{company_id}",
        headers=BETA_HEADERS,
    )
    assert resumed.status_code == 200
    recovery_provider = _ResearchProvider()
    assert _run_worker(migrated_app, recovery_provider).outcome == "research_job_reused"
    recovered = _run_worker(
        migrated_app,
        recovery_provider,
        research_calls_enabled=True,
    )
    assert recovered.outcome == "research_completed"
    assert recovery_provider.research_calls == ["risk"]
    recovered_detail = client.get(
        f"/api/v1/companies/{company_id}",
        headers=BETA_HEADERS,
    ).json()
    assert len(recovered_detail["events"]) == 3
    assert len(recovered_detail["platform_unconfirmed_leads"]) == 2
