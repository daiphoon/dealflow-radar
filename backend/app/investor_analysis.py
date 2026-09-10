from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app.config import InvestorAnalysisPolicy
from backend.app.database import set_request_context
from backend.app.investor_analysis_schema import (
    INVESTOR_ANALYSIS_PROMPT_VERSION,
    INVESTOR_ANALYSIS_SCHEMA_VERSION,
    RESEARCH_CANDIDATE_ANALYSIS_PROMPT_VERSION,
    RESEARCH_CANDIDATE_ANALYSIS_SCHEMA_VERSION,
    RESEARCH_CANDIDATE_POLICY_VERSION,
    InvestorChangeAnalysisOutput,
    InvestorChangeAnalysisRequest,
    InvestorEvidenceInput,
    ResearchCandidateAnalysisOutput,
    ResearchCandidateAnalysisRequest,
    ResearchEvidenceInput,
    validate_research_candidate_output,
)
from backend.app.models import (
    PLATFORM_SHARED_SCOPE,
    SYSTEM_RESTRICTED_SCOPE,
    Company,
    CompanyResearchJob,
    EntityMention,
    Event,
    EventEvidence,
    EventFact,
    EventFactSupport,
    InvestorChangeAnalysis,
    RawDocument,
    UsageLedger,
    User,
    utc_now,
)
from backend.app.providers import AnalysisLLMProvider, LLMProviderError
from backend.app.services import user_has_role
from backend.app.tender_presentation import (
    current_tender_observation,
    is_tender_event,
    tender_observations,
    usable_shared_evidence,
)

_NUMERIC_TOKEN = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?%?")
_FORBIDDEN_ADVICE = ("建议买入", "建议卖出", "值得投资", "保证收益", "确定上涨")


@dataclass(frozen=True)
class InvestorAnalysisWorkerResult:
    status: str
    analysis_id: UUID | None = None
    event_id: UUID | None = None
    outcome: str | None = None
    external_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: Decimal = Decimal("0")

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "analysis_id": str(self.analysis_id) if self.analysis_id else None,
            "event_id": str(self.event_id) if self.event_id else None,
            "outcome": self.outcome,
            "external_calls": self.external_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost": str(self.estimated_cost),
        }


@dataclass(frozen=True)
class _AnalysisLease:
    analysis_id: UUID
    event_id: UUID
    leased_until: datetime


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _event_fact(event: Event, name: str) -> str | None:
    for fact in event.facts:
        if fact.get("name") == name:
            value = fact.get("value")
            return str(value) if value is not None else None
    return None


def _analysis_request(session: Session, event: Event) -> InvestorChangeAnalysisRequest | None:
    if is_tender_event(event):
        return _tender_analysis_request(session, event)
    company = session.get(Company, event.company_id)
    if company is None:
        return None
    before_value = _event_fact(event, "变更前")
    after_value = _event_fact(event, "变更后")
    field_label = _event_fact(event, "变化字段")
    if before_value is None or after_value is None or field_label is None:
        return None
    evidence_rows = list(
        session.scalars(
            select(EventEvidence)
            .where(
                EventEvidence.event_id == event.id,
                EventEvidence.visibility_scope == PLATFORM_SHARED_SCOPE,
                EventEvidence.owner_user_id.is_(None),
                EventEvidence.owner_tenant_id.is_(None),
                EventEvidence.display_allowed.is_(True),
            )
            .order_by(EventEvidence.created_at, EventEvidence.id)
            .limit(5)
        )
    )
    if not evidence_rows:
        return None
    try:
        return InvestorChangeAnalysisRequest(
            event_id=event.id,
            company_name=company.legal_name,
            event_type=event.event_type,
            field_label=field_label,
            before_value=before_value,
            after_value=after_value,
            deterministic_summary=event.summary,
            uncertainties=event.uncertainties,
            evidence=[
                InvestorEvidenceInput(
                    evidence_id=evidence.id,
                    source_name=evidence.display_source_name or "平台证据",
                    excerpt=evidence.evidence_excerpt,
                    observed_at=(evidence.display_observed_at or event.observed_at).isoformat(),
                )
                for evidence in evidence_rows
            ],
        )
    except ValidationError:
        return None


def _event_is_eligible(event: Event, policy: InvestorAnalysisPolicy) -> bool:
    return (
        event.visibility_scope == PLATFORM_SHARED_SCOPE
        and event.owner_user_id is None
        and event.owner_tenant_id is None
        and event.status == "published"
        and (
            event.publication_route == "deterministic_change"
            or (is_tender_event(event) and event.publication_route == "human_promoted")
        )
        and event.materiality_score >= policy.min_materiality_score
    )


def _tender_analysis_request(
    session: Session, event: Event
) -> InvestorChangeAnalysisRequest | None:
    if event.status != "published" or event.publication_route != "human_promoted":
        return None
    company = session.get(Company, event.company_id)
    current = current_tender_observation(session, event)
    if company is None or current is None:
        return None
    previous = [
        item
        for item in tender_observations(session, event)
        if item.confirmed
        and item.evidence_available
        and not item.is_current
        and (item.reviewed_at or item.observed_at) < (current.reviewed_at or current.observed_at)
    ]

    def facts_text(facts):
        return "；".join(
            f"{item['name']}：{item['value']}{item.get('unit') or ''}" for item in facts
        )

    prior = previous[-1] if previous else None
    all_ids = set(current.evidence_ids) | (set(prior.evidence_ids) if prior else set())
    support_rows = session.execute(
        select(EventFact.name, EventFact.value, EventFact.unit, EventEvidence)
        .join(EventFactSupport, EventFactSupport.event_fact_id == EventFact.id)
        .join(EventEvidence, EventEvidence.id == EventFactSupport.event_evidence_id)
        .where(
            EventFact.event_id == event.id,
            EventFactSupport.support_status == "supported",
            EventEvidence.id.in_(all_ids),
        )
        .order_by(EventEvidence.created_at, EventEvidence.id)
    ).all()

    def supported_fields(observation):
        if observation is None:
            return {}
        keys = {(fact["name"], fact["value"], fact.get("unit")) for fact in observation.facts}
        return {
            name: (fact, evidence)
            for name, value, unit, evidence in support_rows
            if (name, value, unit) in keys
            and evidence.id in observation.evidence_ids
            and usable_shared_evidence(evidence)
            for fact in [{"name": name, "value": value, "unit": unit}]
        }

    current_fields, prior_fields = supported_fields(current), supported_fields(prior)
    evidence_by_id, after_facts, before_facts = {}, [], []
    # 沿用五条引用上限；只解读能同时提供前后版本证据的字段，完整事实仍在页面中。
    focus = (
        "中标金额",
        "投标报价",
        "公告阶段",
        "项目编号",
        "中标日期",
        "公示日期",
        "供应商全称",
        "项目名称",
        "采购人",
        "标段编号",
    )
    for name in focus:
        if name not in current_fields or (prior is not None and name not in prior_fields):
            continue
        fact, evidence = current_fields[name]
        required = {evidence.id: evidence}
        if prior is not None:
            prior_fact, prior_evidence = prior_fields[name]
            required[prior_evidence.id] = prior_evidence
        if len(evidence_by_id.keys() | required.keys()) > 5:
            continue
        evidence_by_id.update(required)
        after_facts.append(fact)
        if prior is not None:
            before_facts.append(prior_fact)
    if not after_facts:
        return None
    evidence = list(evidence_by_id.values())
    try:
        return InvestorChangeAnalysisRequest(
            event_id=event.id,
            company_name=company.legal_name,
            event_type=event.event_type,
            field_label="招投标公告记录",
            before_value=facts_text(before_facts)
            if before_facts
            else "暂无此前已核实记录（不表示此前未中标）",
            after_value=facts_text(after_facts),
            deterministic_summary="经人工核实的招投标公告所述字段；不代表已履约或确认收入。",
            uncertainties=event.uncertainties,
            evidence=[
                InvestorEvidenceInput(
                    evidence_id=item.id,
                    source_name=item.display_source_name or "已核实来源",
                    excerpt=item.evidence_excerpt,
                    observed_at=(item.display_observed_at or event.observed_at).isoformat(),
                )
                for item in evidence
            ],
        )
    except ValidationError:
        return None


def _analysis_input_hash(session: Session, event: Event, request: object) -> str:
    payload = request.model_dump(mode="json")
    if is_tender_event(event):
        current = current_tender_observation(session, event)
        return _sha256(
            {"fact_version": current.fact_version if current else None, "request": payload}
        )
    return _sha256(payload)


def _research_analysis_request(
    session: Session,
    event: Event,
) -> ResearchCandidateAnalysisRequest | None:
    company = session.get(Company, event.company_id)
    if company is None or not company.credit_code:
        return None
    evidence_rows = list(
        session.scalars(
            select(EventEvidence)
            .where(
                EventEvidence.event_id == event.id,
                EventEvidence.visibility_scope == PLATFORM_SHARED_SCOPE,
                EventEvidence.owner_user_id.is_(None),
                EventEvidence.owner_tenant_id.is_(None),
                EventEvidence.display_allowed.is_(True),
            )
            .order_by(EventEvidence.created_at, EventEvidence.id)
            .limit(5)
        )
    )
    if not evidence_rows:
        return None
    try:
        return ResearchCandidateAnalysisRequest(
            event_id=event.id,
            company_name=company.legal_name,
            credit_code=company.credit_code,
            event_type=event.event_type,
            deterministic_title=event.title,
            deterministic_summary=event.summary,
            evidence=[
                ResearchEvidenceInput(
                    evidence_id=evidence.id,
                    source_name=evidence.display_source_name or "平台证据",
                    title=evidence.display_title or event.title,
                    excerpt=evidence.evidence_excerpt,
                    published_at=(
                        evidence.display_published_at.isoformat()
                        if evidence.display_published_at
                        else (
                            evidence.display_published_on.isoformat()
                            if evidence.display_published_on
                            else None
                        )
                    ),
                    observed_at=(evidence.display_observed_at or event.observed_at).isoformat(),
                )
                for evidence in evidence_rows
            ],
        )
    except ValidationError:
        return None


def _originating_research_job_completed(
    session: Session,
    event: Event,
    evidence_ids: set[UUID],
) -> bool:
    evidence_rows = session.scalars(
        select(EventEvidence).where(
            EventEvidence.event_id == event.id,
            EventEvidence.id.in_(evidence_ids),
            EventEvidence.visibility_scope == PLATFORM_SHARED_SCOPE,
            EventEvidence.owner_user_id.is_(None),
            EventEvidence.owner_tenant_id.is_(None),
            EventEvidence.display_allowed.is_(True),
            EventEvidence.raw_document_id.is_not(None),
        )
    )
    for evidence in evidence_rows:
        document = session.get(RawDocument, evidence.raw_document_id)
        if document is None or document.visibility_scope != SYSTEM_RESTRICTED_SCOPE:
            continue
        verified_mention = session.scalar(
            select(EntityMention.id).where(
                EntityMention.raw_document_id == document.id,
                EntityMention.candidate_company_id == event.company_id,
                EntityMention.visibility_scope == SYSTEM_RESTRICTED_SCOPE,
                EntityMention.owner_user_id.is_(None),
                EntityMention.owner_tenant_id.is_(None),
                EntityMention.resolution_status == "verified",
            )
        )
        if verified_mention is None:
            continue
        research_job_id = document.payload.get("research_job_id")
        if not isinstance(research_job_id, str):
            continue
        try:
            job_id = UUID(research_job_id)
        except ValueError:
            continue
        job = session.get(CompanyResearchJob, job_id)
        if job is not None and job.company_id == event.company_id and job.status == "completed":
            return True
    return False


def research_candidate_is_eligible(
    session: Session,
    event: Event,
    policy: InvestorAnalysisPolicy,
) -> bool:
    request = _research_analysis_request(session, event)
    if request is None:
        return False
    return (
        event.visibility_scope == PLATFORM_SHARED_SCOPE
        and event.owner_user_id is None
        and event.owner_tenant_id is None
        and event.status == "candidate"
        and event.publication_route == "unconfirmed_lead"
        and event.event_subtype == "bounded_public_web_page"
        and event.publication_policy_version == RESEARCH_CANDIDATE_POLICY_VERSION
        and "new_evidence_content" in event.publication_reasons
        and event.materiality_score >= policy.min_materiality_score
        and _originating_research_job_completed(
            session,
            event,
            {item.evidence_id for item in request.evidence},
        )
    )


def enqueue_pending_investor_analyses(
    session: Session,
    policy: InvestorAnalysisPolicy,
    *,
    limit: int = 20,
) -> int:
    events = session.scalars(
        select(Event)
        .where(
            Event.visibility_scope == PLATFORM_SHARED_SCOPE,
            Event.owner_user_id.is_(None),
            Event.owner_tenant_id.is_(None),
            Event.status == "published",
            or_(
                Event.publication_route == "deterministic_change",
                (Event.fingerprint_version == "tender-v1")
                & (Event.publication_route == "human_promoted"),
            ),
            Event.materiality_score >= policy.min_materiality_score,
        )
        .order_by(Event.created_at, Event.id)
    ).yield_per(100)
    creation_limit = max(1, min(limit, 100))
    created = 0
    for event in events:
        if created >= creation_limit:
            break
        request = _analysis_request(session, event)
        if request is None:
            continue
        input_hash = _analysis_input_hash(session, event, request)
        existing = session.scalar(
            select(InvestorChangeAnalysis).where(
                InvestorChangeAnalysis.event_id == event.id,
                InvestorChangeAnalysis.prompt_version == INVESTOR_ANALYSIS_PROMPT_VERSION,
                InvestorChangeAnalysis.input_hash == input_hash,
            )
        )
        if existing is not None:
            continue
        session.add(
            InvestorChangeAnalysis(
                event_id=event.id,
                visibility_scope=PLATFORM_SHARED_SCOPE,
                status="pending",
                provider=None,
                model=None,
                prompt_version=INVESTOR_ANALYSIS_PROMPT_VERSION,
                schema_version=INVESTOR_ANALYSIS_SCHEMA_VERSION,
                input_hash=input_hash,
                evidence_ids=[str(item.evidence_id) for item in request.evidence],
                analysis_output=None,
                input_tokens=0,
                output_tokens=0,
                estimated_cost=Decimal("0"),
                attempt_count=0,
                response_id=None,
                last_error_code=None,
                leased_until=None,
                heartbeat_at=None,
            )
        )
        created += 1
    session.commit()
    return created


def enqueue_pending_research_candidate_analyses(
    session: Session,
    policy: InvestorAnalysisPolicy,
    *,
    limit: int = 20,
) -> int:
    events = session.scalars(
        select(Event)
        .where(
            Event.visibility_scope == PLATFORM_SHARED_SCOPE,
            Event.owner_user_id.is_(None),
            Event.owner_tenant_id.is_(None),
            Event.status == "candidate",
            Event.publication_route == "unconfirmed_lead",
            Event.event_subtype == "bounded_public_web_page",
            Event.publication_policy_version == RESEARCH_CANDIDATE_POLICY_VERSION,
            Event.materiality_score >= policy.min_materiality_score,
        )
        .order_by(Event.created_at, Event.id)
    ).yield_per(100)
    creation_limit = max(1, min(limit, 100))
    created = 0
    for event in events:
        if created >= creation_limit:
            break
        if not research_candidate_is_eligible(session, event, policy):
            continue
        request = _research_analysis_request(session, event)
        if request is None:
            continue
        input_hash = _sha256(request.model_dump(mode="json"))
        existing = session.scalar(
            select(InvestorChangeAnalysis).where(
                InvestorChangeAnalysis.event_id == event.id,
                InvestorChangeAnalysis.prompt_version == RESEARCH_CANDIDATE_ANALYSIS_PROMPT_VERSION,
                InvestorChangeAnalysis.input_hash == input_hash,
            )
        )
        if existing is not None:
            continue
        session.add(
            InvestorChangeAnalysis(
                event_id=event.id,
                visibility_scope=PLATFORM_SHARED_SCOPE,
                status="pending",
                provider=None,
                model=None,
                prompt_version=RESEARCH_CANDIDATE_ANALYSIS_PROMPT_VERSION,
                schema_version=RESEARCH_CANDIDATE_ANALYSIS_SCHEMA_VERSION,
                input_hash=input_hash,
                evidence_ids=[str(item.evidence_id) for item in request.evidence],
                analysis_output=None,
                input_tokens=0,
                output_tokens=0,
                estimated_cost=Decimal("0"),
                attempt_count=0,
                response_id=None,
                last_error_code=None,
                leased_until=None,
                heartbeat_at=None,
            )
        )
        created += 1
    session.commit()
    return created


def validate_investor_analysis_worker_user(session: Session, user: User) -> None:
    if not user_has_role(session, user.id, "platform_admin"):
        raise RuntimeError("investor analysis worker requires platform_admin")


def _month_start(now: datetime) -> datetime:
    utc = now.astimezone(UTC)
    return datetime(utc.year, utc.month, 1, tzinfo=UTC)


def _monthly_tokens(session: Session, tenant_id: UUID, now: datetime) -> int:
    return int(
        session.scalar(
            select(
                func.coalesce(
                    func.sum(UsageLedger.input_tokens + UsageLedger.output_tokens),
                    0,
                )
            ).where(
                UsageLedger.tenant_id == tenant_id,
                UsageLedger.operation.in_(
                    ("investor_change_analysis", "research_candidate_analysis")
                ),
                UsageLedger.created_at >= _month_start(now),
            )
        )
        or 0
    )


def _lease_next_analysis(
    session: Session,
    policy: InvestorAnalysisPolicy,
    now: datetime,
) -> _AnalysisLease | None:
    analysis = session.scalar(
        select(InvestorChangeAnalysis)
        .where(
            or_(
                InvestorChangeAnalysis.status == "pending",
                (
                    (InvestorChangeAnalysis.status == "budget_deferred")
                    & (InvestorChangeAnalysis.updated_at < _month_start(now))
                ),
                (
                    (InvestorChangeAnalysis.status == "running")
                    & (
                        (InvestorChangeAnalysis.leased_until.is_(None))
                        | (InvestorChangeAnalysis.leased_until <= now)
                    )
                ),
            )
        )
        .order_by(InvestorChangeAnalysis.created_at, InvestorChangeAnalysis.id)
        .with_for_update(skip_locked=True)
    )
    if analysis is None:
        session.rollback()
        return None
    leased_until = now + timedelta(seconds=policy.worker_lease_seconds)
    analysis.status = "running"
    analysis.leased_until = leased_until
    analysis.heartbeat_at = now
    analysis.attempt_count += 1
    session.commit()
    return _AnalysisLease(
        analysis_id=analysis.id,
        event_id=analysis.event_id,
        leased_until=leased_until,
    )


def _analysis_text_values(output: InvestorChangeAnalysisOutput) -> list[str]:
    return [
        output.headline,
        output.what_changed,
        output.why_it_matters,
        *output.potential_impacts,
        *output.uncertainties,
        *output.follow_up_items,
    ]


def _validate_evidence_constrained_output(
    request: InvestorChangeAnalysisRequest,
    output: InvestorChangeAnalysisOutput,
) -> None:
    if output.before_value != request.before_value or output.after_value != request.after_value:
        raise ValueError("analysis changed deterministic before/after values")
    allowed_evidence_ids = {item.evidence_id for item in request.evidence}
    if not set(output.evidence_ids) <= allowed_evidence_ids:
        raise ValueError("analysis referenced evidence outside the request")
    source_text = _canonical_json(request.model_dump(mode="json"))
    allowed_numbers = set(_NUMERIC_TOKEN.findall(source_text))
    output_text = " ".join(_analysis_text_values(output))
    invented_numbers = set(_NUMERIC_TOKEN.findall(output_text)) - allowed_numbers
    if invented_numbers:
        raise ValueError("analysis introduced numbers absent from evidence")
    if any(phrase in output_text for phrase in _FORBIDDEN_ADVICE):
        raise ValueError("analysis contains prohibited investment advice")


def _record_usage(
    session: Session,
    user: User,
    analysis: InvestorChangeAnalysis,
    *,
    provider: str,
    model: str,
    external_calls: int,
    input_tokens: int,
    output_tokens: int,
    estimated_cost: Decimal,
    outcome: str,
    operation: str = "investor_change_analysis",
) -> None:
    session.add(
        UsageLedger(
            tenant_id=user.tenant_id,
            company_id=session.scalar(
                select(Event.company_id).where(Event.id == analysis.event_id)
            ),
            provider=provider,
            operation=operation,
            external_calls=external_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            metrics={
                "analysis_id": str(analysis.id),
                "event_id": str(analysis.event_id),
                "model": model,
                "prompt_version": analysis.prompt_version,
                "schema_version": analysis.schema_version,
                "outcome": outcome,
                "automatic_publication": False,
                "report_generated": False,
            },
            idempotency_key=_sha256(
                {
                    "operation": operation,
                    "analysis_id": str(analysis.id),
                    "attempt_count": analysis.attempt_count,
                }
            ),
        )
    )


def run_investor_analysis_worker_once(
    session: Session,
    user: User,
    provider: AnalysisLLMProvider,
    policy: InvestorAnalysisPolicy,
    *,
    now: datetime | None = None,
) -> InvestorAnalysisWorkerResult:
    validate_investor_analysis_worker_user(session, user)
    checked_at = (now or utc_now()).astimezone(UTC)
    worker_user_id = user.id
    worker_tenant_id = user.tenant_id
    set_request_context(session, worker_user_id, worker_tenant_id)
    enqueue_pending_investor_analyses(session, policy)
    set_request_context(session, worker_user_id, worker_tenant_id)
    enqueue_pending_research_candidate_analyses(session, policy)
    set_request_context(session, worker_user_id, worker_tenant_id)
    lease = _lease_next_analysis(session, policy, checked_at)
    if lease is None:
        return InvestorAnalysisWorkerResult(status="idle")
    set_request_context(session, worker_user_id, worker_tenant_id)
    analysis = session.scalar(
        select(InvestorChangeAnalysis)
        .where(InvestorChangeAnalysis.id == lease.analysis_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    event = session.get(Event, lease.event_id)
    if analysis is None or event is None or analysis.status != "running":
        session.rollback()
        return InvestorAnalysisWorkerResult(status="lease_lost", analysis_id=lease.analysis_id)
    if analysis.schema_version == INVESTOR_ANALYSIS_SCHEMA_VERSION:
        operation = "investor_change_analysis"
        eligible = _event_is_eligible(event, policy)
        request = _analysis_request(session, event)
    elif analysis.schema_version == RESEARCH_CANDIDATE_ANALYSIS_SCHEMA_VERSION:
        operation = "research_candidate_analysis"
        eligible = research_candidate_is_eligible(session, event, policy)
        request = _research_analysis_request(session, event)
    else:
        analysis.status = "failed"
        analysis.last_error_code = "unsupported_analysis_schema"
        analysis.leased_until = None
        session.commit()
        return InvestorAnalysisWorkerResult(
            status="completed",
            analysis_id=analysis.id,
            event_id=event.id,
            outcome="unsupported_analysis_schema",
        )
    if not eligible:
        analysis.status = "failed"
        analysis.last_error_code = "event_no_longer_eligible"
        analysis.leased_until = None
        session.commit()
        return InvestorAnalysisWorkerResult(
            status="completed",
            analysis_id=analysis.id,
            event_id=event.id,
            outcome="event_no_longer_eligible",
        )
    if request is None or _analysis_input_hash(session, event, request) != analysis.input_hash:
        analysis.status = "failed"
        analysis.last_error_code = "analysis_input_changed"
        analysis.leased_until = None
        session.commit()
        return InvestorAnalysisWorkerResult(
            status="completed",
            analysis_id=analysis.id,
            event_id=event.id,
            outcome="analysis_input_changed",
        )
    projected_tokens = (
        len(_canonical_json(request.model_dump(mode="json"))) + policy.max_output_tokens
    )
    if (
        _monthly_tokens(session, user.tenant_id, checked_at) + projected_tokens
        > policy.monthly_token_limit
    ):
        analysis.status = "budget_deferred"
        analysis.last_error_code = "monthly_token_budget_exceeded"
        analysis.leased_until = None
        session.commit()
        return InvestorAnalysisWorkerResult(
            status="completed",
            analysis_id=analysis.id,
            event_id=event.id,
            outcome="budget_deferred",
        )
    try:
        if isinstance(request, ResearchCandidateAnalysisRequest):
            result = provider.analyze_research_candidate(request)
        else:
            result = provider.analyze_investor_change(request)
    except LLMProviderError as error:
        analysis.status = "failed"
        analysis.provider = provider.code
        analysis.model = provider.model
        analysis.input_tokens = error.input_tokens
        analysis.output_tokens = error.output_tokens
        analysis.last_error_code = str(error)[:80]
        analysis.leased_until = None
        _record_usage(
            session,
            user,
            analysis,
            provider=provider.code,
            model=provider.model,
            external_calls=error.external_calls,
            input_tokens=error.input_tokens,
            output_tokens=error.output_tokens,
            estimated_cost=error.estimated_cost,
            outcome="provider_failed",
            operation=operation,
        )
        session.commit()
        return InvestorAnalysisWorkerResult(
            status="completed",
            analysis_id=analysis.id,
            event_id=event.id,
            outcome="provider_failed",
            external_calls=error.external_calls,
            input_tokens=error.input_tokens,
            output_tokens=error.output_tokens,
            estimated_cost=error.estimated_cost,
        )
    try:
        if isinstance(request, ResearchCandidateAnalysisRequest):
            if not isinstance(result.analysis, ResearchCandidateAnalysisOutput):
                raise ValueError("provider returned the wrong analysis schema")
            validate_research_candidate_output(request, result.analysis)
        else:
            if not isinstance(result.analysis, InvestorChangeAnalysisOutput):
                raise ValueError("provider returned the wrong analysis schema")
            _validate_evidence_constrained_output(request, result.analysis)
    except ValueError:
        analysis.status = "failed"
        analysis.provider = provider.code
        analysis.model = provider.model
        analysis.last_error_code = "evidence_validation_failed"
        analysis.leased_until = None
        _record_usage(
            session,
            user,
            analysis,
            provider=provider.code,
            model=provider.model,
            external_calls=result.external_calls,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost=result.estimated_cost,
            outcome="evidence_validation_failed",
            operation=operation,
        )
        session.commit()
        return InvestorAnalysisWorkerResult(
            status="completed",
            analysis_id=analysis.id,
            event_id=event.id,
            outcome="evidence_validation_failed",
            external_calls=result.external_calls,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost=result.estimated_cost,
        )

    analysis.status = "completed"
    analysis.provider = provider.code
    analysis.model = provider.model
    analysis.analysis_output = result.analysis.model_dump(mode="json")
    analysis.evidence_ids = [str(item) for item in result.analysis.evidence_ids]
    analysis.input_tokens = result.input_tokens
    analysis.output_tokens = result.output_tokens
    analysis.estimated_cost = result.estimated_cost
    analysis.response_id = result.response_id
    analysis.last_error_code = None
    analysis.leased_until = None
    analysis.heartbeat_at = checked_at
    _record_usage(
        session,
        user,
        analysis,
        provider=provider.code,
        model=provider.model,
        external_calls=result.external_calls,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost=result.estimated_cost,
        outcome="completed",
        operation=operation,
    )
    session.commit()
    return InvestorAnalysisWorkerResult(
        status="completed",
        analysis_id=analysis.id,
        event_id=event.id,
        outcome="completed",
        external_calls=result.external_calls,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost=result.estimated_cost,
    )
