from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.config import InvestorAnalysisPolicy
from backend.app.demo import (
    ALPHA_TENANT_ID,
    ALPHA_USER_ID,
    BETA_USER_ID,
    MOCK_SOURCE_ID,
    NO_ACCESS_USER_ID,
)
from backend.app.investor_analysis import (
    enqueue_pending_investor_analyses,
    enqueue_pending_research_candidate_analyses,
    run_investor_analysis_worker_once,
)
from backend.app.investor_analysis_schema import (
    INVESTOR_ANALYSIS_DISCLAIMER,
    INVESTOR_ANALYSIS_SCHEMA_VERSION,
    RESEARCH_CANDIDATE_ANALYSIS_DISCLAIMER,
    RESEARCH_CANDIDATE_ANALYSIS_SCHEMA_VERSION,
    RESEARCH_CANDIDATE_POLICY_VERSION,
    InvestorChangeAnalysisOutput,
    InvestorChangeAnalysisRequest,
    LLMProviderResult,
    ResearchCandidateAnalysisOutput,
    ResearchCandidateAnalysisRequest,
    ResearchLLMProviderResult,
)
from backend.app.models import (
    PLATFORM_SHARED_SCOPE,
    Company,
    CompanyResearchJob,
    EntityMention,
    Event,
    EventEvidence,
    InvestorChangeAnalysis,
    RawDocument,
    Role,
    UsageLedger,
    User,
    UserRoleAssignment,
)

PERSONAL_HEADERS = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _grant_platform_admin(app: FastAPI) -> None:
    with app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.code == "platform_admin"))
        assert role is not None
        if (
            session.scalar(
                select(UserRoleAssignment).where(
                    UserRoleAssignment.user_id == ALPHA_USER_ID,
                    UserRoleAssignment.role_id == role.id,
                )
            )
            is None
        ):
            session.add(
                UserRoleAssignment(
                    user_id=ALPHA_USER_ID,
                    role_id=role.id,
                    scope_id=None,
                    valid_until=None,
                )
            )
            session.commit()


def _create_change_event(
    app: FastAPI,
    *,
    suffix: str,
    materiality_score: int = 80,
    before_value: str = "20%",
    after_value: str = "25%",
) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    with app.state.session_factory() as session:
        company = session.scalar(
            select(Company)
            .where(
                Company.tenant_id.is_(None),
                Company.visibility_scope == "public",
                Company.identity_status == "verified",
            )
            .order_by(Company.id)
        )
        assert company is not None
        raw_document = RawDocument(
            source_id=MOCK_SOURCE_ID,
            research_import_id=None,
            candidate_document_id=None,
            owner_user_id=None,
            owner_tenant_id=ALPHA_TENANT_ID,
            visibility_scope="organization_private",
            external_record_id=f"investor-analysis-{suffix}",
            canonical_url=f"https://example.invalid/investor-analysis-{suffix}",
            title="机构私有授权研究底稿",
            published_at=None,
            published_on=None,
            observed_at=datetime(2026, 8, 28, tzinfo=UTC),
            content_hash=_sha256(f"investor-analysis-document:{suffix}"),
            document_dedupe_key=_sha256(f"investor-analysis-dedupe:{suffix}"),
            license_status="licensed",
            payload={"private_test_fixture": True},
        )
        session.add(raw_document)
        session.flush()
        private_event = Event(
            company_id=company.id,
            owner_user_id=None,
            owner_tenant_id=ALPHA_TENANT_ID,
            visibility_scope="organization_private",
            event_type="financing_cap_table",
            event_subtype="shareholder_ratio_candidate",
            status="candidate",
            direction="neutral",
            materiality_score=materiality_score,
            risk_severity="low",
            confidence_score=Decimal("0.950"),
            source_quality="A",
            title="机构私有股东变化候选",
            summary=f"工商登记持股比例由{before_value}变为{after_value}。",
            facts=[],
            uncertainties=[],
            occurred_at=None,
            published_at=None,
            published_on=None,
            observed_at=datetime(2026, 8, 28, tzinfo=UTC),
            fingerprint_version="change-v1",
            event_fingerprint=_sha256(f"investor-private-change:{suffix}"),
            publication_route="unconfirmed_lead",
            publication_policy_version="investor-material-change-v1",
            publication_reasons=["private_research_context"],
        )
        session.add(private_event)
        session.flush()
        source_evidence = EventEvidence(
            event_id=private_event.id,
            raw_document_id=raw_document.id,
            source_event_evidence_id=None,
            owner_user_id=None,
            owner_tenant_id=ALPHA_TENANT_ID,
            visibility_scope="organization_private",
            evidence_excerpt=private_event.summary,
            span_hash=_sha256(f"investor-private-evidence:{suffix}"),
            support_type="supports",
            display_allowed=False,
        )
        session.add(source_evidence)
        session.flush()
        event = Event(
            company_id=company.id,
            owner_user_id=None,
            owner_tenant_id=None,
            visibility_scope=PLATFORM_SHARED_SCOPE,
            event_type="financing_cap_table",
            event_subtype="shareholder_ratio_changed",
            status="published",
            direction="neutral",
            materiality_score=materiality_score,
            risk_severity="low",
            confidence_score=Decimal("0.950"),
            source_quality="A",
            title="工商登记持股比例发生重要变化",
            summary=f"工商登记持股比例由{before_value}变为{after_value}。",
            facts=[
                {"name": "变化字段", "value": "工商登记持股比例", "unit": None},
                {"name": "变更前", "value": before_value, "unit": None},
                {"name": "变更后", "value": after_value, "unit": None},
            ],
            uncertainties=["仍需结合公司章程和实际控制关系判断影响。"],
            occurred_at=datetime(2026, 8, 28, tzinfo=UTC),
            published_at=None,
            published_on=None,
            observed_at=datetime(2026, 8, 28, tzinfo=UTC),
            fingerprint_version="change-v1",
            event_fingerprint=_sha256(f"investor-change:{suffix}"),
            publication_route="deterministic_change",
            publication_policy_version="investor-material-change-v1",
            publication_reasons=["material_change_threshold_met"],
        )
        session.add(event)
        session.flush()
        evidence = EventEvidence(
            event_id=event.id,
            raw_document_id=None,
            source_event_evidence_id=source_evidence.id,
            owner_user_id=None,
            owner_tenant_id=None,
            visibility_scope=PLATFORM_SHARED_SCOPE,
            evidence_excerpt=event.summary,
            span_hash=_sha256(f"investor-analysis-evidence:{suffix}"),
            support_type="supports",
            display_source_name="授权工商数据源",
            display_source_quality="A",
            display_title="股东信息比较记录",
            display_canonical_url="https://licensed.example.test/records/123",
            display_observed_at=datetime(2026, 8, 28, tzinfo=UTC),
            display_url_health_status="unchecked",
            display_license_status="licensed",
            display_allowed=True,
        )
        session.add(evidence)
        session.commit()
        return company.id, event.id, evidence.id, private_event.id, raw_document.id


def _create_research_candidate(
    app: FastAPI,
    *,
    suffix: str,
    job_status: str = "completed",
    evidence_reason: str = "new_evidence_content",
    materiality_score: int = 70,
) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    with app.state.session_factory() as session:
        company = session.scalar(
            select(Company)
            .where(
                Company.tenant_id.is_(None),
                Company.visibility_scope == "public",
                Company.identity_status == "verified",
                Company.credit_code.is_not(None),
            )
            .order_by(Company.id)
        )
        assert company is not None
        job = CompanyResearchJob(
            company_id=company.id,
            created_by_user_id=NO_ACCESS_USER_ID,
            status=job_status,
            current_stage=job_status,
            policy_version="bounded-web-v3",
            coverage={"fixture": True},
            external_calls=0,
            input_tokens=0,
            output_tokens=0,
        )
        session.add(job)
        session.flush()
        raw_document = RawDocument(
            source_id=MOCK_SOURCE_ID,
            research_import_id=None,
            candidate_document_id=None,
            owner_user_id=None,
            owner_tenant_id=None,
            visibility_scope="system_restricted",
            external_record_id=f"research-analysis-{suffix}",
            canonical_url=f"https://example.invalid/research-analysis-{suffix}",
            title="公司完成融资公告",
            published_at=datetime(2026, 9, 4, tzinfo=UTC),
            published_on=datetime(2026, 9, 4, tzinfo=UTC).date(),
            observed_at=datetime(2026, 9, 4, tzinfo=UTC),
            content_hash=_sha256(f"research-analysis-document:{suffix}"),
            document_dedupe_key=_sha256(f"research-analysis-dedupe:{suffix}"),
            license_status="public",
            payload={
                "research_job_id": str(job.id),
                "retention": "minimal_excerpt",
                "excerpt": f"{company.legal_name}已完成融资，相关事项已经公告。",
                "system_restricted_marker": "must-not-reach-model-or-api",
            },
        )
        session.add(raw_document)
        session.flush()
        session.add(
            EntityMention(
                raw_document_id=raw_document.id,
                owner_user_id=None,
                owner_tenant_id=None,
                visibility_scope="system_restricted",
                candidate_company_id=company.id,
                mention_text=company.legal_name,
                match_rule="verified_identity_exact_or_official_domain",
                match_confidence=Decimal("0.950"),
                resolution_status="verified",
            )
        )
        event = Event(
            company_id=company.id,
            owner_user_id=None,
            owner_tenant_id=None,
            visibility_scope=PLATFORM_SHARED_SCOPE,
            event_type="financing_cap_table",
            event_subtype="bounded_public_web_page",
            status="candidate",
            direction="positive",
            materiality_score=materiality_score,
            risk_severity="low",
            confidence_score=Decimal("0.800"),
            source_quality="A",
            title="公司完成融资公告",
            summary=f"公开来源出现与该公司相关的变化线索：{company.legal_name}已完成融资。",
            facts=[{"name": "公开页面标题", "value": "公司完成融资公告", "unit": None}],
            uncertainties=["尚未经过人工事实复核。"],
            occurred_at=None,
            published_at=datetime(2026, 9, 4, tzinfo=UTC),
            published_on=datetime(2026, 9, 4, tzinfo=UTC).date(),
            observed_at=datetime(2026, 9, 4, tzinfo=UTC),
            fingerprint_version="web-v1",
            event_fingerprint=_sha256(f"research-analysis-event:{suffix}"),
            publication_route="unconfirmed_lead",
            publication_policy_version=RESEARCH_CANDIDATE_POLICY_VERSION,
            publication_reasons=[
                "auto_publish_disabled",
                "bounded_public_web_discovery",
                evidence_reason,
            ],
        )
        session.add(event)
        session.flush()
        evidence = EventEvidence(
            event_id=event.id,
            raw_document_id=raw_document.id,
            source_event_evidence_id=None,
            owner_user_id=None,
            owner_tenant_id=None,
            visibility_scope=PLATFORM_SHARED_SCOPE,
            evidence_excerpt=f"{company.legal_name}已完成融资，相关事项已经公告。",
            span_hash=_sha256(f"research-analysis-evidence:{suffix}"),
            support_type="supports",
            display_source_name="公司官方网站",
            display_source_quality="A",
            display_title="公司完成融资公告",
            display_canonical_url=f"https://company.example.test/notices/{suffix}",
            display_published_at=datetime(2026, 9, 4, tzinfo=UTC),
            display_observed_at=datetime(2026, 9, 4, tzinfo=UTC),
            display_url_health_status="healthy",
            display_url_http_status=200,
            display_url_checked_at=datetime(2026, 9, 4, tzinfo=UTC),
            display_final_url=f"https://company.example.test/notices/{suffix}",
            display_license_status="public",
            display_allowed=True,
        )
        session.add(evidence)
        session.commit()
        return company.id, event.id, evidence.id, raw_document.id, job.id


class _MockAnalysisProvider:
    code = "mock_analysis"
    model = "mock-fixed-output"

    def __init__(self, *, invented_number: bool = False) -> None:
        self.calls = 0
        self.invented_number = invented_number
        self.requests: list[InvestorChangeAnalysisRequest] = []
        self.research_requests: list[ResearchCandidateAnalysisRequest] = []

    def analyze_investor_change(
        self,
        request: InvestorChangeAnalysisRequest,
    ) -> LLMProviderResult:
        self.calls += 1
        self.requests.append(request)
        why_it_matters = "该变化意味着股东登记比例发生调整，需继续核对治理和控制关系。"
        if self.invented_number:
            why_it_matters = "该变化可能使公司价值增长30%，需继续观察。"
        return LLMProviderResult(
            analysis=InvestorChangeAnalysisOutput(
                schema_version=INVESTOR_ANALYSIS_SCHEMA_VERSION,
                headline="工商登记持股比例发生变化",
                before_value=request.before_value,
                after_value=request.after_value,
                what_changed=(
                    f"{request.field_label}由{request.before_value}变为{request.after_value}。"
                ),
                why_it_matters=why_it_matters,
                potential_impacts=["可能影响股东表决权和公司治理判断。"],
                uncertainties=["尚不能仅根据登记比例判断实际控制关系。"],
                evidence_ids=[request.evidence[0].evidence_id],
                confidence=0.91,
                follow_up_items=["关注后续股东及公司章程变更。"],
                impact_direction="uncertain",
                disclaimer=INVESTOR_ANALYSIS_DISCLAIMER,
            ),
            external_calls=0,
            input_tokens=120,
            output_tokens=80,
            estimated_cost=Decimal("0"),
            response_id="mock-response",
        )

    def analyze_research_candidate(
        self,
        request: ResearchCandidateAnalysisRequest,
    ) -> ResearchLLMProviderResult:
        self.calls += 1
        self.research_requests.append(request)
        why_it_matters = "该线索可能反映公司融资进展，仍需结合原始公告继续核实。"
        if self.invented_number:
            why_it_matters = "该线索可能令公司价值增长30%，仍需继续核实。"
        return ResearchLLMProviderResult(
            analysis=ResearchCandidateAnalysisOutput(
                schema_version=RESEARCH_CANDIDATE_ANALYSIS_SCHEMA_VERSION,
                event_id=request.event_id,
                event_type=request.event_type,
                headline="公司出现新的融资进展线索",
                what_changed="公开页面提到公司已经完成融资。",
                why_it_matters=why_it_matters,
                potential_impacts=["这可能影响股东对资本实力和后续发展的判断。"],
                uncertainties=["融资金额和投资方尚未在当前证据中确认。"],
                evidence_ids=[request.evidence[0].evidence_id],
                confidence=0.82,
                follow_up_items=["继续核对公司正式公告及工商变更。"],
                impact_direction="uncertain",
                disclaimer=RESEARCH_CANDIDATE_ANALYSIS_DISCLAIMER,
            ),
            external_calls=0,
            input_tokens=90,
            output_tokens=60,
            estimated_cost=Decimal("0"),
            response_id="mock-research-response",
        )


def test_worker_generates_evidence_constrained_shared_analysis_once(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    company_id, event_id, evidence_id, private_event_id, raw_document_id = _create_change_event(
        migrated_app,
        suffix="success",
    )
    provider = _MockAnalysisProvider()

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        result = run_investor_analysis_worker_once(
            session,
            user,
            provider,
            InvestorAnalysisPolicy(),
        )

    assert result.outcome == "completed"
    assert result.external_calls == 0
    assert provider.calls == 1
    assert len(provider.requests) == 1
    assert "private_test_fixture" not in provider.requests[0].model_dump_json()
    with migrated_app.state.session_factory() as session:
        analysis = session.scalar(
            select(InvestorChangeAnalysis).where(InvestorChangeAnalysis.event_id == event_id)
        )
        event = session.get(Event, event_id)
        evidence = session.get(EventEvidence, evidence_id)
        private_event = session.get(Event, private_event_id)
        raw_document = session.get(RawDocument, raw_document_id)
        usage = session.scalar(
            select(UsageLedger).where(UsageLedger.operation == "investor_change_analysis")
        )
        assert analysis is not None
        assert analysis.status == "completed"
        assert analysis.visibility_scope == PLATFORM_SHARED_SCOPE
        assert analysis.analysis_output is not None
        assert event is not None and event.status == "published"
        assert event.publication_route == "deterministic_change"
        assert evidence is not None and evidence.source_event_evidence_id is not None
        assert private_event is not None
        assert private_event.visibility_scope == "organization_private"
        assert private_event.owner_tenant_id == ALPHA_TENANT_ID
        assert raw_document is not None
        assert raw_document.visibility_scope == "organization_private"
        assert raw_document.owner_tenant_id == ALPHA_TENANT_ID
        assert usage is not None
        assert usage.metrics["automatic_publication"] is False
        assert usage.metrics["report_generated"] is False

        assert enqueue_pending_investor_analyses(session, InvestorAnalysisPolicy()) == 0
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        assert (
            run_investor_analysis_worker_once(
                session,
                user,
                provider,
                InvestorAnalysisPolicy(),
            ).status
            == "idle"
        )
    assert provider.calls == 1

    response = client.get(f"/api/v1/companies/{company_id}", headers=PERSONAL_HEADERS)
    assert response.status_code == 200
    payload = response.json()
    change = next(item for item in payload["events"] if item["id"] == str(event_id))
    assert change["analysis"]["why_it_matters"].startswith("该变化意味着")
    assert change["analysis"]["disclaimer"] == INVESTOR_ANALYSIS_DISCLAIMER
    assert payload["investments"] == []
    assert "provider" not in change["analysis"]
    assert "input_tokens" not in change["analysis"]

    with migrated_app.state.session_factory() as session:
        evidence = session.get(EventEvidence, evidence_id)
        assert evidence is not None
        evidence.display_allowed = False
        session.commit()
    response = client.get(f"/api/v1/companies/{company_id}", headers=PERSONAL_HEADERS)
    assert response.status_code == 200
    payload = response.json()
    change = next(item for item in payload["events"] if item["id"] == str(event_id))
    assert change["analysis"] is None


def test_worker_rejects_model_number_absent_from_evidence(migrated_app: FastAPI) -> None:
    _grant_platform_admin(migrated_app)
    _, event_id, _, _, _ = _create_change_event(
        migrated_app,
        suffix="invented-number",
        before_value="存续",
        after_value="迁出",
    )
    provider = _MockAnalysisProvider(invented_number=True)

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        result = run_investor_analysis_worker_once(
            session,
            user,
            provider,
            InvestorAnalysisPolicy(),
        )

    assert result.outcome == "evidence_validation_failed"
    with migrated_app.state.session_factory() as session:
        analysis = session.scalar(
            select(InvestorChangeAnalysis).where(InvestorChangeAnalysis.event_id == event_id)
        )
        assert analysis is not None
        assert analysis.status == "failed"
        assert analysis.analysis_output is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.operation == "investor_change_analysis")
            )
            == 1
        )


def test_worker_generates_candidate_analysis_only_from_new_completed_research(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    company_id, event_id, evidence_id, raw_document_id, _ = _create_research_candidate(
        migrated_app,
        suffix="new-evidence",
    )
    provider = _MockAnalysisProvider()

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        result = run_investor_analysis_worker_once(
            session,
            user,
            provider,
            InvestorAnalysisPolicy(),
        )

    assert result.outcome == "completed"
    assert provider.calls == 1
    assert len(provider.research_requests) == 1
    request_json = provider.research_requests[0].model_dump_json()
    assert "must-not-reach-model-or-api" not in request_json
    with migrated_app.state.session_factory() as session:
        event = session.get(Event, event_id)
        raw_document = session.get(RawDocument, raw_document_id)
        analysis = session.scalar(
            select(InvestorChangeAnalysis).where(InvestorChangeAnalysis.event_id == event_id)
        )
        usage = session.scalar(
            select(UsageLedger).where(UsageLedger.operation == "research_candidate_analysis")
        )
        assert event is not None
        assert event.status == "candidate"
        assert event.publication_route == "unconfirmed_lead"
        assert raw_document is not None
        assert raw_document.visibility_scope == "system_restricted"
        assert analysis is not None
        assert analysis.status == "completed"
        assert analysis.schema_version == RESEARCH_CANDIDATE_ANALYSIS_SCHEMA_VERSION
        assert usage is not None
        assert usage.metrics["automatic_publication"] is False
        assert usage.metrics["report_generated"] is False

        assert enqueue_pending_research_candidate_analyses(session, InvestorAnalysisPolicy()) == 0
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        assert (
            run_investor_analysis_worker_once(
                session,
                user,
                provider,
                InvestorAnalysisPolicy(),
            ).status
            == "idle"
        )
    assert provider.calls == 1

    for user_id in (NO_ACCESS_USER_ID, BETA_USER_ID):
        response = client.get(
            f"/api/v1/companies/{company_id}",
            headers={"X-Demo-User-Id": str(user_id)},
        )
        assert response.status_code == 200
        payload = response.json()
        candidate = next(
            item for item in payload["platform_unconfirmed_leads"] if item["id"] == str(event_id)
        )
        assert candidate["research_analysis"]["headline"] == "公司出现新的融资进展线索"
        assert (
            candidate["research_analysis"]["disclaimer"] == RESEARCH_CANDIDATE_ANALYSIS_DISCLAIMER
        )
        assert "must-not-reach-model-or-api" not in response.text
        if user_id == NO_ACCESS_USER_ID:
            assert payload["investments"] == []
            assert payload["private_events"] == []

    with migrated_app.state.session_factory() as session:
        evidence = session.get(EventEvidence, evidence_id)
        assert evidence is not None
        evidence.display_allowed = False
        session.commit()
    response = client.get(f"/api/v1/companies/{company_id}", headers=PERSONAL_HEADERS)
    assert response.status_code == 200
    candidate = next(
        item
        for item in response.json()["platform_unconfirmed_leads"]
        if item["id"] == str(event_id)
    )
    assert candidate["research_analysis"] is None


def test_reused_or_cancelled_research_evidence_never_queues_model(
    migrated_app: FastAPI,
) -> None:
    _, reused_event_id, _, _, _ = _create_research_candidate(
        migrated_app,
        suffix="reused-evidence",
        evidence_reason="reused_evidence_content",
    )
    _, cancelled_event_id, _, _, _ = _create_research_candidate(
        migrated_app,
        suffix="cancelled-job",
        job_status="cancelled",
    )

    with migrated_app.state.session_factory() as session:
        assert enqueue_pending_research_candidate_analyses(session, InvestorAnalysisPolicy()) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(InvestorChangeAnalysis)
                .where(InvestorChangeAnalysis.event_id.in_((reused_event_id, cancelled_event_id)))
            )
            == 0
        )


def test_research_candidate_without_verified_company_mention_never_queues_model(
    migrated_app: FastAPI,
) -> None:
    _, event_id, _, raw_document_id, _ = _create_research_candidate(
        migrated_app,
        suffix="missing-verified-mention",
    )
    with migrated_app.state.session_factory() as session:
        mention = session.scalar(
            select(EntityMention).where(EntityMention.raw_document_id == raw_document_id)
        )
        assert mention is not None
        session.delete(mention)
        session.commit()
        assert enqueue_pending_research_candidate_analyses(session, InvestorAnalysisPolicy()) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(InvestorChangeAnalysis)
                .where(InvestorChangeAnalysis.event_id == event_id)
            )
            == 0
        )


def test_research_candidate_analysis_rejects_number_absent_from_evidence(
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    _, event_id, _, _, _ = _create_research_candidate(
        migrated_app,
        suffix="invented-number",
    )
    provider = _MockAnalysisProvider(invented_number=True)

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        result = run_investor_analysis_worker_once(
            session,
            user,
            provider,
            InvestorAnalysisPolicy(),
        )

    assert result.outcome == "evidence_validation_failed"
    with migrated_app.state.session_factory() as session:
        analysis = session.scalar(
            select(InvestorChangeAnalysis).where(InvestorChangeAnalysis.event_id == event_id)
        )
        event = session.get(Event, event_id)
        assert analysis is not None and analysis.status == "failed"
        assert analysis.analysis_output is None
        assert event is not None and event.status == "candidate"


def test_low_materiality_change_is_not_queued(migrated_app: FastAPI) -> None:
    _, event_id, _, _, _ = _create_change_event(
        migrated_app,
        suffix="low-materiality",
        materiality_score=59,
    )
    with migrated_app.state.session_factory() as session:
        assert enqueue_pending_investor_analyses(session, InvestorAnalysisPolicy()) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(InvestorChangeAnalysis)
                .where(InvestorChangeAnalysis.event_id == event_id)
            )
            == 0
        )


def test_monthly_budget_defers_without_calling_provider(migrated_app: FastAPI) -> None:
    _grant_platform_admin(migrated_app)
    _, event_id, _, _, _ = _create_change_event(migrated_app, suffix="budget")
    provider = _MockAnalysisProvider()

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        result = run_investor_analysis_worker_once(
            session,
            user,
            provider,
            InvestorAnalysisPolicy(monthly_token_limit=1),
        )

    assert result.outcome == "budget_deferred"
    assert provider.calls == 0
    with migrated_app.state.session_factory() as session:
        analysis = session.scalar(
            select(InvestorChangeAnalysis).where(InvestorChangeAnalysis.event_id == event_id)
        )
        assert analysis is not None and analysis.status == "budget_deferred"
        assert (
            session.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.operation == "investor_change_analysis")
            )
            == 0
        )
