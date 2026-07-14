from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from backend.app.demo import (
    ALPHA_FUND_ID,
    ALPHA_TENANT_ID,
    ALPHA_USER_ID,
    BETA_FUND_ID,
    BETA_TENANT_ID,
    BETA_USER_ID,
    MOCK_SOURCE_ID,
    NO_ACCESS_USER_ID,
    demo_uuid,
)
from backend.app.models import (
    Company,
    CompanyAlias,
    CompanySnapshot,
    EntityMention,
    Event,
    EventEvidence,
    Fund,
    FundAccessGrant,
    Investment,
    RawDocument,
    RefreshJob,
    ReviewQueue,
    Role,
    Source,
    Tenant,
    UsageLedger,
    User,
    UserRoleAssignment,
    utc_now,
)
from backend.app.providers import MockResearchProvider, MockResearchRecord
from backend.app.schemas import (
    CompanyDetail,
    CompanyListItem,
    EventOut,
    EvidenceOut,
    IngestResult,
    InvestmentOut,
    RefreshResult,
)


class AccessDeniedError(Exception):
    pass


class NotFoundError(Exception):
    pass


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _add_if_missing(session: Session, model: type, object_id: UUID, **values: object) -> object:
    existing = session.get(model, object_id)
    if existing is not None:
        return existing
    instance = model(id=object_id, **values)
    session.add(instance)
    return instance


def seed_demo_entities(session: Session, records: list[MockResearchRecord]) -> None:
    _add_if_missing(session, Tenant, ALPHA_TENANT_ID, name="示例机构甲", status="active")
    _add_if_missing(session, Tenant, BETA_TENANT_ID, name="示例机构乙", status="active")
    _add_if_missing(
        session,
        User,
        ALPHA_USER_ID,
        tenant_id=ALPHA_TENANT_ID,
        email="alpha-admin@example.invalid",
        display_name="示例审核员甲",
        status="active",
    )
    _add_if_missing(
        session,
        User,
        BETA_USER_ID,
        tenant_id=BETA_TENANT_ID,
        email="beta-investor@example.invalid",
        display_name="示例投资人乙",
        status="active",
    )
    _add_if_missing(
        session,
        User,
        NO_ACCESS_USER_ID,
        tenant_id=ALPHA_TENANT_ID,
        email="no-access@example.invalid",
        display_name="示例无基金权限用户",
        status="active",
    )
    _add_if_missing(
        session,
        Fund,
        ALPHA_FUND_ID,
        tenant_id=ALPHA_TENANT_ID,
        name="示例基金甲",
        code="DEMO-A",
        status="active",
        visibility_scope="fund",
    )
    _add_if_missing(
        session,
        Fund,
        BETA_FUND_ID,
        tenant_id=BETA_TENANT_ID,
        name="示例基金乙",
        code="DEMO-B",
        status="active",
        visibility_scope="fund",
    )
    admin_role = _add_if_missing(
        session,
        Role,
        demo_uuid("role-institution-admin"),
        code="institution_admin",
        permissions=["demo:ingest", "company:read"],
        scope_type="tenant",
    )
    reviewer_role = _add_if_missing(
        session,
        Role,
        demo_uuid("role-reviewer"),
        code="reviewer",
        permissions=["review:read", "review:decide"],
        scope_type="tenant",
    )
    investor_role = _add_if_missing(
        session,
        Role,
        demo_uuid("role-investor"),
        code="investor",
        permissions=["company:read"],
        scope_type="fund",
    )
    session.flush()
    for assignment_id, user_id, role_id in [
        (demo_uuid("assignment-alpha-admin"), ALPHA_USER_ID, admin_role.id),
        (demo_uuid("assignment-alpha-reviewer"), ALPHA_USER_ID, reviewer_role.id),
        (demo_uuid("assignment-beta-investor"), BETA_USER_ID, investor_role.id),
    ]:
        _add_if_missing(
            session,
            UserRoleAssignment,
            assignment_id,
            user_id=user_id,
            role_id=role_id,
            scope_id=None,
            valid_until=None,
        )
    _add_if_missing(
        session,
        FundAccessGrant,
        demo_uuid("grant-alpha-fund"),
        user_id=ALPHA_USER_ID,
        fund_id=ALPHA_FUND_ID,
        permission="admin",
        valid_until=None,
    )
    _add_if_missing(
        session,
        FundAccessGrant,
        demo_uuid("grant-beta-fund"),
        user_id=BETA_USER_ID,
        fund_id=BETA_FUND_ID,
        permission="read",
        valid_until=None,
    )
    _add_if_missing(
        session,
        Source,
        MOCK_SOURCE_ID,
        code="mock_official",
        name="Mock 官方来源",
        source_quality="A",
        license_status="synthetic_demo",
        base_url="https://example.invalid",
    )
    session.flush()

    for index, record in enumerate(records, start=1):
        company_id = demo_uuid(f"company-{record.company_legal_name}")
        _add_if_missing(
            session,
            Company,
            company_id,
            tenant_id=None,
            credit_code=None,
            legal_name=record.company_legal_name,
            registered_region=record.registered_region,
            identity_status="verified",
            visibility_scope="public",
        )
        _add_if_missing(
            session,
            CompanyAlias,
            demo_uuid(f"alias-{record.company_alias}"),
            company_id=company_id,
            source_id=MOCK_SOURCE_ID,
            alias=record.company_alias,
            normalized_alias=record.company_alias.strip().lower(),
            alias_type="short_name",
            verification_status="verified",
        )
        _add_if_missing(
            session,
            Investment,
            demo_uuid(f"investment-alpha-{company_id}"),
            tenant_id=ALPHA_TENANT_ID,
            fund_id=ALPHA_FUND_ID,
            company_id=company_id,
            round_name="demo_round",
            amount=Decimal(index * 1_000_000),
            currency="CNY",
            ownership=Decimal("0.010000") * index,
            internal_valuation=Decimal(index * 50_000_000),
            visibility_scope="fund",
        )
        if index == 1:
            _add_if_missing(
                session,
                Investment,
                demo_uuid(f"investment-beta-{company_id}"),
                tenant_id=BETA_TENANT_ID,
                fund_id=BETA_FUND_ID,
                company_id=company_id,
                round_name="demo_round",
                amount=Decimal("2500000"),
                currency="CNY",
                ownership=Decimal("0.025000"),
                internal_valuation=Decimal("75000000"),
                visibility_scope="fund",
            )
    session.flush()


def ingest_mock_records(session: Session, provider: MockResearchProvider) -> IngestResult:
    records = provider.load()
    seed_demo_entities(session, records)
    documents_created = 0
    events_created = 0
    reviews_created = 0

    for record in records:
        dedupe_key = _sha256(f"{MOCK_SOURCE_ID}:{record.external_record_id}")
        existing = session.scalar(
            select(RawDocument).where(RawDocument.document_dedupe_key == dedupe_key)
        )
        if existing is not None:
            continue
        company = session.scalar(
            select(Company).where(Company.legal_name == record.company_legal_name)
        )
        if company is None:
            raise NotFoundError(f"unresolved company: {record.company_legal_name}")
        payload = record.model_dump(mode="json")
        content_hash = _sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        document = RawDocument(
            source_id=MOCK_SOURCE_ID,
            external_record_id=record.external_record_id,
            canonical_url=record.canonical_url,
            title=record.title,
            published_at=record.published_at,
            observed_at=utc_now(),
            content_hash=content_hash,
            document_dedupe_key=dedupe_key,
            license_status="synthetic_demo",
            payload=payload,
        )
        session.add(document)
        session.flush()
        session.add(
            EntityMention(
                raw_document_id=document.id,
                candidate_company_id=company.id,
                mention_text=record.company_legal_name,
                match_rule="legal_name_exact",
                match_confidence=Decimal("1.000"),
                resolution_status="verified",
            )
        )
        event_fingerprint = _sha256(
            "|".join(
                [
                    str(company.id),
                    record.event_type.value,
                    record.event_subtype,
                    record.published_at.date().isoformat(),
                    json.dumps([fact.model_dump() for fact in record.facts], ensure_ascii=False),
                ]
            )
        )
        event = Event(
            company_id=company.id,
            event_type=record.event_type.value,
            event_subtype=record.event_subtype,
            status="in_review",
            direction=record.direction.value,
            materiality_score=record.materiality_score,
            risk_severity=record.risk_severity.value,
            confidence_score=Decimal(str(record.confidence_score)),
            source_quality=record.source_quality.value,
            title=record.title,
            summary=record.evidence_excerpt,
            facts=[fact.model_dump() for fact in record.facts],
            uncertainties=record.uncertainties,
            occurred_at=record.published_at,
            published_at=record.published_at,
            observed_at=utc_now(),
            fingerprint_version="1",
            event_fingerprint=event_fingerprint,
        )
        session.add(event)
        session.flush()
        session.add(
            EventEvidence(
                event_id=event.id,
                raw_document_id=document.id,
                evidence_excerpt=record.evidence_excerpt,
                span_hash=_sha256(record.evidence_excerpt),
                support_type="supports",
            )
        )
        session.add(
            ReviewQueue(
                tenant_id=ALPHA_TENANT_ID,
                event_id=event.id,
                status="pending",
                trigger_rules=["demo_manual_review"],
            )
        )
        documents_created += 1
        events_created += 1
        reviews_created += 1

    session.add(
        UsageLedger(
            tenant_id=ALPHA_TENANT_ID,
            provider=provider.code,
            operation="mock_ingest",
            external_calls=0,
            input_tokens=0,
            output_tokens=0,
            estimated_cost=Decimal("0"),
            metrics={
                "records_seen": len(records),
                "documents_created": documents_created,
                "events_created": events_created,
            },
            idempotency_key=_sha256(f"mock-ingest:{uuid4()}"),
        )
    )
    session.commit()
    return IngestResult(
        records_seen=len(records),
        documents_created=documents_created,
        events_created=events_created,
        reviews_created=reviews_created,
    )


def user_has_role(session: Session, user_id: UUID, role_code: str) -> bool:
    count = session.scalar(
        select(func.count())
        .select_from(UserRoleAssignment)
        .join(Role, Role.id == UserRoleAssignment.role_id)
        .where(
            UserRoleAssignment.user_id == user_id,
            Role.code == role_code,
            or_(
                UserRoleAssignment.valid_until.is_(None),
                UserRoleAssignment.valid_until > utc_now(),
            ),
        )
    )
    return bool(count)


def _authorized_investments(
    session: Session, user_id: UUID, company_id: UUID | None = None
) -> list[tuple[Investment, Fund]]:
    user = session.get(User, user_id)
    if user is None:
        return []
    statement = (
        select(Investment, Fund)
        .join(Fund, Fund.id == Investment.fund_id)
        .join(
            FundAccessGrant,
            (FundAccessGrant.fund_id == Investment.fund_id) & (FundAccessGrant.user_id == user_id),
        )
        .where(
            Investment.tenant_id == user.tenant_id,
            Fund.tenant_id == user.tenant_id,
            or_(
                FundAccessGrant.valid_until.is_(None),
                FundAccessGrant.valid_until > utc_now(),
            ),
        )
    )
    if company_id is not None:
        statement = statement.where(Investment.company_id == company_id)
    return list(session.execute(statement).all())


def _event_out(session: Session, event: Event) -> EventOut:
    evidence_rows = session.execute(
        select(EventEvidence, RawDocument, Source)
        .join(RawDocument, RawDocument.id == EventEvidence.raw_document_id)
        .join(Source, Source.id == RawDocument.source_id)
        .where(EventEvidence.event_id == event.id)
    ).all()
    return EventOut(
        id=event.id,
        event_type=event.event_type,
        event_subtype=event.event_subtype,
        occurred_at=event.occurred_at,
        published_at=event.published_at,
        direction=event.direction,
        materiality_score=event.materiality_score,
        risk_severity=event.risk_severity,
        confidence_score=event.confidence_score,
        source_quality=event.source_quality,
        title=event.title,
        summary=event.summary,
        status=event.status,
        observed_at=event.observed_at,
        evidence=[
            EvidenceOut(
                id=evidence.id,
                source_name=source.name,
                source_quality=source.source_quality,
                title=document.title,
                canonical_url=document.canonical_url,
                published_at=document.published_at,
                observed_at=document.observed_at,
                excerpt=evidence.evidence_excerpt,
            )
            for evidence, document, source in evidence_rows
        ],
    )


def list_companies(session: Session, user_id: UUID) -> list[CompanyListItem]:
    investment_rows = _authorized_investments(session, user_id)
    company_ids = sorted({row[0].company_id for row in investment_rows}, key=str)
    items: list[CompanyListItem] = []
    risk_order = {"none": 0, "low": 1, "moderate": 2, "high": 3, "critical": 4}
    for company_id in company_ids:
        company = session.get(Company, company_id)
        if company is None:
            continue
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == company_id, CompanySnapshot.is_current.is_(True)
            )
        )
        events = list(
            session.scalars(
                select(Event)
                .where(Event.company_id == company_id, Event.status == "published")
                .order_by(Event.occurred_at.desc())
            )
        )
        highest_risk = max(
            (event.risk_severity for event in events),
            key=lambda value: risk_order.get(value, -1),
            default=None,
        )
        items.append(
            CompanyListItem(
                id=company.id,
                legal_name=company.legal_name,
                identity_status=company.identity_status,
                freshness_status=snapshot.freshness_status if snapshot else "unknown",
                last_checked_at=snapshot.last_checked_at if snapshot else None,
                latest_event_title=events[0].title if events else None,
                highest_risk=highest_risk,
                information_gaps=snapshot.information_gaps if snapshot else ["尚无已发布快照"],
            )
        )
    return items


def get_company_detail(session: Session, user_id: UUID, company_id: UUID) -> CompanyDetail:
    investment_rows = _authorized_investments(session, user_id, company_id)
    if not investment_rows:
        raise NotFoundError("company not found")
    company = session.get(Company, company_id)
    if company is None:
        raise NotFoundError("company not found")
    snapshot = session.scalar(
        select(CompanySnapshot).where(
            CompanySnapshot.company_id == company_id, CompanySnapshot.is_current.is_(True)
        )
    )
    events = list(
        session.scalars(
            select(Event)
            .where(Event.company_id == company_id, Event.status == "published")
            .order_by(Event.occurred_at.desc())
        )
    )
    return CompanyDetail(
        id=company.id,
        legal_name=company.legal_name,
        registered_region=company.registered_region,
        identity_status=company.identity_status,
        data_as_of=snapshot.data_as_of if snapshot else None,
        last_checked_at=snapshot.last_checked_at if snapshot else None,
        freshness_status=snapshot.freshness_status if snapshot else "unknown",
        information_gaps=snapshot.information_gaps if snapshot else ["尚无已发布快照"],
        investments=[
            InvestmentOut(
                fund_id=fund.id,
                fund_name=fund.name,
                round_name=investment.round_name,
                amount=investment.amount,
                currency=investment.currency,
                ownership=investment.ownership,
                internal_valuation=investment.internal_valuation,
                visibility_scope=investment.visibility_scope,
            )
            for investment, fund in investment_rows
        ],
        events=[_event_out(session, event) for event in events],
    )


def decide_review(
    session: Session, review_id: UUID, user: User, decision: str, reason: str
) -> ReviewQueue:
    if not user_has_role(session, user.id, "reviewer"):
        raise AccessDeniedError("reviewer role required")
    review = session.get(ReviewQueue, review_id)
    if review is None or review.tenant_id != user.tenant_id:
        raise NotFoundError("review not found")
    if review.status != "pending":
        raise AccessDeniedError("review already decided")
    event = session.get(Event, review.event_id)
    if event is None:
        raise NotFoundError("event not found")
    review.status = "approved" if decision == "approve" else "rejected"
    review.decision = decision
    review.decision_reason = reason
    review.assigned_user_id = user.id
    review.decided_at = utc_now()
    if decision == "reject":
        event.status = "rejected"
        session.commit()
        return review
    evidence_count = session.scalar(
        select(func.count()).select_from(EventEvidence).where(EventEvidence.event_id == event.id)
    )
    company = session.get(Company, event.company_id)
    if not evidence_count or company is None or company.identity_status != "verified":
        raise AccessDeniedError("published event requires verified identity and evidence")
    event.status = "published"
    session.execute(
        update(CompanySnapshot)
        .where(CompanySnapshot.company_id == company.id, CompanySnapshot.is_current.is_(True))
        .values(is_current=False)
    )
    current_version = session.scalar(
        select(func.max(CompanySnapshot.snapshot_version)).where(
            CompanySnapshot.company_id == company.id
        )
    )
    published_events = list(
        session.scalars(
            select(Event).where(Event.company_id == company.id, Event.status == "published")
        )
    )
    risk_order = {"none": 0, "low": 1, "moderate": 2, "high": 3, "critical": 4}
    highest_risk = max(
        (item.risk_severity for item in published_events),
        key=lambda value: risk_order.get(value, -1),
        default="none",
    )
    event_dates = [
        value.date()
        for item in published_events
        if (value := item.occurred_at or item.published_at) is not None
    ]
    session.add(
        CompanySnapshot(
            company_id=company.id,
            snapshot_version=(current_version or 0) + 1,
            is_current=True,
            data_as_of=max(event_dates, default=date.today()),
            last_checked_at=utc_now(),
            freshness_status="fresh",
            summary={"published_event_count": len(published_events), "highest_risk": highest_risk},
            information_gaps=["财务数据：暂无可靠公开数据。"],
        )
    )
    session.commit()
    return review


def request_refresh(
    session: Session, user: User, company_id: UUID, *, dry_run: bool
) -> RefreshResult:
    if not _authorized_investments(session, user.id, company_id):
        raise NotFoundError("company not found")
    if dry_run:
        return RefreshResult(status="dry_run")
    idempotency_key = _sha256(
        f"{user.tenant_id}:{company_id}:mock_refresh:{datetime.now(UTC).date()}"
    )
    existing = session.scalar(
        select(RefreshJob).where(
            RefreshJob.tenant_id == user.tenant_id,
            RefreshJob.company_id == company_id,
            RefreshJob.job_type == "mock_refresh",
            or_(
                RefreshJob.status.in_(["queued", "running"]),
                RefreshJob.idempotency_key == idempotency_key,
            ),
        )
    )
    if existing is not None:
        return RefreshResult(status="merged", job_id=existing.id)
    job = RefreshJob(
        tenant_id=user.tenant_id,
        company_id=company_id,
        job_type="mock_refresh",
        refresh_reason="manual_request",
        status="queued",
        idempotency_key=idempotency_key,
        estimated_cost=Decimal("0"),
    )
    session.add(job)
    session.commit()
    return RefreshResult(status="queued", job_id=job.id)
