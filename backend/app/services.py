from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.config import PublicationPolicy, RefreshPolicy
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
    ResearchImport,
    ReviewQueue,
    Role,
    Source,
    Tenant,
    UsageLedger,
    User,
    UserRoleAssignment,
    utc_now,
)
from backend.app.providers import (
    DisabledDocumentVerifier,
    DocumentVerification,
    DocumentVerifier,
    LoadedResearchImport,
    ManualResearchImportProvider,
    ManualResearchRecord,
    MockResearchProvider,
    MockResearchRecord,
)
from backend.app.schemas import (
    CompanyDetail,
    CompanyListItem,
    EventOut,
    EvidenceOut,
    IngestResult,
    InvestmentOut,
    RefreshResult,
    ResearchImportResult,
    ReviewWorkbenchOut,
)

MANUAL_EVENT_FINGERPRINT_VERSION = "manual-v1"


class AccessDeniedError(Exception):
    pass


class NotFoundError(Exception):
    pass


class ImportConflictError(Exception):
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


def _manual_source(session: Session, record: ManualResearchRecord) -> Source:
    source = session.scalar(select(Source).where(Source.code == record.source_code))
    parsed_url = urlsplit(record.canonical_url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    if source is None:
        source = Source(
            code=record.source_code,
            name=record.source_name,
            source_quality=record.source_quality.value,
            license_status="public",
            base_url=base_url,
        )
        session.add(source)
        session.flush()
        return source
    if (
        source.name != record.source_name
        or source.source_quality != record.source_quality.value
        or source.license_status != "public"
        or source.base_url != base_url
    ):
        raise ImportConflictError(f"source_code conflict: {record.source_code}")
    return source


def _normalized_web_host(url: str | None) -> str | None:
    if url is None:
        return None
    host = urlsplit(url).hostname
    if host is None:
        return None
    normalized = host.rstrip(".").lower()
    return normalized.removeprefix("www.")


def _website_conflicts(company: Company, record: ManualResearchRecord) -> bool:
    company_host = _normalized_web_host(company.official_website)
    evidence_host = _normalized_web_host(record.company_identity_evidence.official_website)
    return company_host is not None and evidence_host is not None and company_host != evidence_host


def _resolve_manual_company(
    session: Session,
    tenant_id: UUID,
    record: ManualResearchRecord,
) -> tuple[Company | None, str, Decimal, str]:
    identity = record.company_identity_evidence
    visible_company = or_(Company.tenant_id.is_(None), Company.tenant_id == tenant_id)
    if identity.credit_code is not None:
        candidates = list(
            session.scalars(
                select(Company).where(
                    visible_company,
                    Company.credit_code == identity.credit_code,
                )
            )
        )
        if len(candidates) != 1:
            return None, "credit_code_unmatched", Decimal("0.000"), "unresolved"
        company = candidates[0]
        region_conflict = (
            identity.registered_region is not None
            and company.registered_region is not None
            and identity.registered_region != company.registered_region
        )
        if (
            company.legal_name != identity.legal_name
            or region_conflict
            or _website_conflicts(company, record)
            or company.identity_status != "verified"
        ):
            return company, "credit_code_conflict", Decimal("0.500"), "unresolved"
        return company, "credit_code_exact", Decimal("1.000"), "verified"

    candidates = list(
        session.scalars(
            select(Company).where(
                visible_company,
                Company.legal_name == identity.legal_name,
            )
        )
    )
    if len(candidates) != 1:
        return None, "legal_name_unmatched", Decimal("0.000"), "unresolved"
    company = candidates[0]
    region_conflict = (
        identity.registered_region is not None
        and company.registered_region is not None
        and identity.registered_region != company.registered_region
    )
    if (
        region_conflict
        or _website_conflicts(company, record)
        or company.identity_status != "verified"
    ):
        return company, "legal_name_conflict", Decimal("0.600"), "unresolved"
    return company, "legal_name_exact", Decimal("0.950"), "verified"


def _record_published_on(record: ManualResearchRecord) -> date | None:
    if record.source_published_on is not None:
        return record.source_published_on
    if record.source_published_at is not None:
        return record.source_published_at.date()
    for fact in record.facts:
        if fact.name != "source_displayed_date":
            continue
        try:
            return date.fromisoformat(fact.value)
        except ValueError:
            return None
    return None


def _evaluate_publication_route(
    company: Company,
    record: ManualResearchRecord,
    verification: DocumentVerification,
    policy: PublicationPolicy,
) -> tuple[str, list[str]]:
    blocking_reasons: list[str] = []
    if not policy.enabled:
        blocking_reasons.append("auto_publish_disabled")
    if verification.status != "healthy":
        blocking_reasons.append(f"source_url_{verification.status}")
    if record.source_quality.value not in {"A", "B"}:
        blocking_reasons.append("source_quality_not_allowed")
    if Decimal(str(record.confidence_score)) < policy.min_confidence:
        blocking_reasons.append("confidence_below_threshold")
    if record.risk_severity.value in {"high", "critical"}:
        blocking_reasons.append("high_risk_unconfirmed")
    if record.source_quality.value == "B":
        company_host = _normalized_web_host(company.official_website)
        source_host = _normalized_web_host(verification.final_url or record.canonical_url)
        if company_host is None:
            blocking_reasons.append("official_website_not_verified")
        elif company_host != source_host:
            blocking_reasons.append("official_source_domain_mismatch")
    if blocking_reasons:
        return "unconfirmed_lead", blocking_reasons
    return (
        "auto_published",
        [
            "identity_verified",
            "source_url_healthy",
            "source_quality_allowed",
            "confidence_threshold_met",
            "risk_allowed",
        ],
    )


def _manual_event_fingerprint(company_id: UUID, record: ManualResearchRecord) -> str:
    canonical_facts = sorted(
        (fact.model_dump() for fact in record.facts),
        key=lambda fact: (fact["name"], fact["value"], fact["unit"] or ""),
    )
    return _sha256(
        "|".join(
            [
                str(company_id),
                record.event_type.value,
                record.event_subtype,
                record.occurred_at.date().isoformat()
                if record.occurred_at is not None
                else "unknown",
                json.dumps(
                    canonical_facts,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ]
        )
    )


def _add_manual_event_evidence(
    session: Session,
    event_id: UUID,
    document_id: UUID,
    record: ManualResearchRecord,
) -> None:
    session.add(
        EventEvidence(
            event_id=event_id,
            raw_document_id=document_id,
            evidence_excerpt=record.evidence_excerpt,
            span_hash=_sha256(record.evidence_excerpt),
            support_type="supports",
        )
    )


def _mark_event_published(session: Session, event: Event, company: Company) -> None:
    evidence_count = session.scalar(
        select(func.count()).select_from(EventEvidence).where(EventEvidence.event_id == event.id)
    )
    if not evidence_count or company.identity_status != "verified":
        raise AccessDeniedError("published event requires verified identity and evidence")
    event.status = "published"


def _refresh_company_snapshot(session: Session, company: Company) -> None:
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
    event_dates: list[date] = []
    missing_fact_dates = False
    for item in published_events:
        if item.occurred_at is not None:
            event_dates.append(item.occurred_at.date())
        elif item.published_at is not None:
            event_dates.append(item.published_at.date())
        elif item.published_on is not None:
            event_dates.append(item.published_on)
        else:
            missing_fact_dates = True
    information_gaps = ["财务数据：暂无可靠公开数据。"]
    if missing_fact_dates:
        information_gaps.append("部分事件缺少发生或来源日期；数据基准使用系统发现日期。")
    session.add(
        CompanySnapshot(
            company_id=company.id,
            snapshot_version=(current_version or 0) + 1,
            is_current=True,
            data_as_of=max(event_dates, default=None),
            last_checked_at=utc_now(),
            freshness_status="fresh",
            summary={"published_event_count": len(published_events), "highest_risk": highest_risk},
            information_gaps=information_gaps,
        )
    )


def _duplicate_import_result(existing: ResearchImport) -> ResearchImportResult:
    return ResearchImportResult(
        status="duplicate",
        research_import_id=existing.id,
        batch_id=existing.batch_id,
        records_seen=existing.record_count,
        documents_created=0,
        events_created=0,
        reviews_created=0,
        resolved_records=existing.resolved_count,
        unresolved_records=existing.unresolved_count,
        auto_published_records=existing.auto_published_count,
        unconfirmed_records=existing.unconfirmed_count,
        identity_review_records=existing.identity_review_count,
    )


def _ingest_manual_batch(
    session: Session,
    user: User,
    provider: ManualResearchImportProvider,
    loaded: LoadedResearchImport,
    publication_policy: PublicationPolicy,
    document_verifier: DocumentVerifier,
) -> ResearchImportResult:
    batch_payload = loaded.batch
    research_import = ResearchImport(
        tenant_id=user.tenant_id,
        imported_by=user.id,
        schema_version=batch_payload.schema_version,
        batch_id=batch_payload.batch_id,
        queried_at=batch_payload.queried_at,
        research_tool=batch_payload.research_tool,
        agent_name=batch_payload.agent_name,
        original_query=batch_payload.original_query,
        target_company_hint=batch_payload.target_company_hint,
        source_filename=loaded.source_filename,
        file_format="json",
        file_hash=loaded.file_hash,
        parser_version=provider.parser_version,
        license_status=batch_payload.license_status,
        status="processing",
        record_count=len(batch_payload.records),
        resolved_count=0,
        unresolved_count=0,
        auto_published_count=0,
        unconfirmed_count=0,
        identity_review_count=0,
    )
    session.add(research_import)
    session.flush()
    documents_created = 0
    events_created = 0
    reviews_created = 0
    external_calls = 0
    source_url_checks = 0
    published_company_ids: set[UUID] = set()

    for record in batch_payload.records:
        company, match_rule, match_confidence, resolution_status = _resolve_manual_company(
            session,
            user.tenant_id,
            record,
        )
        if resolution_status == "verified":
            research_import.resolved_count += 1
        else:
            research_import.unresolved_count += 1

        source = _manual_source(session, record)
        record_payload = record.model_dump(mode="json")
        content_hash = _sha256(json.dumps(record_payload, ensure_ascii=False, sort_keys=True))
        dedupe_key = _sha256(f"{source.id}:{record.external_record_id}")
        existing_document = session.scalar(
            select(RawDocument).where(RawDocument.document_dedupe_key == dedupe_key)
        )
        if existing_document is not None:
            if existing_document.content_hash != content_hash:
                raise ImportConflictError(
                    f"external_record_id content conflict: {record.external_record_id}"
                )
            continue

        verification = DisabledDocumentVerifier().verify(record.canonical_url)
        if resolution_status == "verified" and publication_policy.enabled:
            if source_url_checks < publication_policy.max_source_url_checks_per_import:
                verification = document_verifier.verify(record.canonical_url)
                source_url_checks += 1
                external_calls += verification.external_calls
            else:
                verification = DocumentVerification(
                    status="unchecked",
                    reason="source_url_check_budget_deferred",
                    external_calls=0,
                )
        document_payload = {
            **record_payload,
            "_source_verification": verification.model_dump(mode="json"),
        }

        document = RawDocument(
            source_id=source.id,
            research_import_id=research_import.id,
            external_record_id=record.external_record_id,
            canonical_url=record.canonical_url,
            title=record.title,
            published_at=record.source_published_at,
            published_on=_record_published_on(record),
            observed_at=utc_now(),
            content_hash=content_hash,
            document_dedupe_key=dedupe_key,
            license_status="public",
            payload=document_payload,
        )
        session.add(document)
        session.flush()
        mention = EntityMention(
            raw_document_id=document.id,
            candidate_company_id=company.id if company is not None else None,
            mention_text=record.company_identity_evidence.legal_name,
            match_rule=match_rule,
            match_confidence=match_confidence,
            resolution_status=resolution_status,
        )
        session.add(mention)
        session.flush()
        documents_created += 1
        if resolution_status != "verified" or company is None:
            session.add(
                ReviewQueue(
                    tenant_id=user.tenant_id,
                    entity_mention_id=mention.id,
                    status="pending",
                    trigger_rules=["manual_research_import", "identity_unresolved"],
                )
            )
            reviews_created += 1
            research_import.identity_review_count += 1
            continue

        publication_route, publication_reasons = _evaluate_publication_route(
            company,
            record,
            verification,
            publication_policy,
        )
        if publication_route == "auto_published":
            research_import.auto_published_count += 1
        else:
            research_import.unconfirmed_count += 1

        event_fingerprint = _manual_event_fingerprint(company.id, record)
        existing_event = session.scalar(
            select(Event).where(
                Event.company_id == company.id,
                Event.fingerprint_version == MANUAL_EVENT_FINGERPRINT_VERSION,
                Event.event_fingerprint == event_fingerprint,
            )
        )
        if existing_event is not None:
            if existing_event.status == "candidate":
                _add_manual_event_evidence(session, existing_event.id, document.id, record)
                existing_event.publication_route = publication_route
                existing_event.publication_policy_version = publication_policy.version
                existing_event.publication_reasons = publication_reasons
                if publication_route == "auto_published":
                    _mark_event_published(session, existing_event, company)
                    published_company_ids.add(company.id)
            elif existing_event.status == "published" and publication_route == "auto_published":
                _add_manual_event_evidence(session, existing_event.id, document.id, record)
                existing_event.publication_reasons = sorted(
                    {*existing_event.publication_reasons, "additional_evidence_auto_attached"}
                )
            elif existing_event.status == "in_review":
                _add_manual_event_evidence(session, existing_event.id, document.id, record)
            continue
        event = Event(
            company_id=company.id,
            event_type=record.event_type.value,
            event_subtype=record.event_subtype,
            status="candidate",
            direction=record.direction.value,
            materiality_score=record.materiality_score,
            risk_severity=record.risk_severity.value,
            confidence_score=Decimal(str(record.confidence_score)),
            source_quality=record.source_quality.value,
            title=record.title,
            summary=record.evidence_excerpt,
            facts=[fact.model_dump() for fact in record.facts],
            uncertainties=record.uncertainties,
            occurred_at=record.occurred_at,
            published_at=record.source_published_at,
            published_on=_record_published_on(record),
            observed_at=utc_now(),
            fingerprint_version=MANUAL_EVENT_FINGERPRINT_VERSION,
            event_fingerprint=event_fingerprint,
            publication_route=publication_route,
            publication_policy_version=publication_policy.version,
            publication_reasons=publication_reasons,
        )
        session.add(event)
        session.flush()
        _add_manual_event_evidence(session, event.id, document.id, record)
        if publication_route == "auto_published":
            _mark_event_published(session, event, company)
            published_company_ids.add(company.id)
        events_created += 1

    for company_id in sorted(published_company_ids, key=str):
        company = session.get(Company, company_id)
        if company is not None:
            _refresh_company_snapshot(session, company)

    research_import.status = (
        "completed_with_unresolved" if research_import.unresolved_count else "completed"
    )
    session.add(
        UsageLedger(
            tenant_id=user.tenant_id,
            provider=provider.code,
            operation="manual_research_import",
            external_calls=external_calls,
            input_tokens=0,
            output_tokens=0,
            estimated_cost=Decimal("0"),
            metrics={
                "research_import_id": str(research_import.id),
                "records_seen": research_import.record_count,
                "documents_created": documents_created,
                "events_created": events_created,
                "resolved_records": research_import.resolved_count,
                "unresolved_records": research_import.unresolved_count,
                "auto_published_records": research_import.auto_published_count,
                "unconfirmed_records": research_import.unconfirmed_count,
                "identity_review_records": research_import.identity_review_count,
                "publication_policy_version": publication_policy.version,
                "source_url_checks": source_url_checks,
            },
            idempotency_key=_sha256(f"manual-research-import:{research_import.id}"),
        )
    )
    session.commit()
    return ResearchImportResult(
        status=research_import.status,
        research_import_id=research_import.id,
        batch_id=research_import.batch_id,
        records_seen=research_import.record_count,
        documents_created=documents_created,
        events_created=events_created,
        reviews_created=reviews_created,
        resolved_records=research_import.resolved_count,
        unresolved_records=research_import.unresolved_count,
        auto_published_records=research_import.auto_published_count,
        unconfirmed_records=research_import.unconfirmed_count,
        identity_review_records=research_import.identity_review_count,
        external_calls=external_calls,
    )


def import_manual_research(
    session: Session,
    user: User,
    provider: ManualResearchImportProvider,
    publication_policy: PublicationPolicy | None = None,
    document_verifier: DocumentVerifier | None = None,
) -> ResearchImportResult:
    if not user_has_role(session, user.id, "institution_admin"):
        session.rollback()
        raise AccessDeniedError("institution_admin role required")
    loaded = provider.load()
    existing_file = session.scalar(
        select(ResearchImport).where(
            ResearchImport.tenant_id == user.tenant_id,
            ResearchImport.file_hash == loaded.file_hash,
            ResearchImport.parser_version == provider.parser_version,
        )
    )
    if existing_file is not None:
        result = _duplicate_import_result(existing_file)
        session.commit()
        return result
    existing_batch = session.scalar(
        select(ResearchImport).where(
            ResearchImport.tenant_id == user.tenant_id,
            ResearchImport.batch_id == loaded.batch.batch_id,
        )
    )
    if existing_batch is not None:
        session.rollback()
        raise ImportConflictError(f"batch_id conflict: {loaded.batch.batch_id}")
    try:
        return _ingest_manual_batch(
            session,
            user,
            provider,
            loaded,
            publication_policy or PublicationPolicy(),
            document_verifier or DisabledDocumentVerifier(),
        )
    except Exception:
        session.rollback()
        raise


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
    evidence_items: list[EvidenceOut] = []
    for evidence, document, source in evidence_rows:
        verification = document.payload.get("_source_verification", {})
        if not isinstance(verification, dict):
            verification = {}
        evidence_items.append(
            EvidenceOut(
                id=evidence.id,
                source_name=source.name,
                source_quality=source.source_quality,
                title=document.title,
                canonical_url=document.canonical_url,
                published_at=document.published_at,
                published_on=document.published_on,
                observed_at=document.observed_at,
                excerpt=evidence.evidence_excerpt,
                url_health_status=str(verification.get("status", "unchecked")),
                url_http_status=verification.get("http_status"),
                url_checked_at=verification.get("checked_at"),
                final_url=verification.get("final_url"),
            )
        )
    return EventOut(
        id=event.id,
        event_type=event.event_type,
        event_subtype=event.event_subtype,
        occurred_at=event.occurred_at,
        published_at=event.published_at,
        published_on=event.published_on,
        direction=event.direction,
        materiality_score=event.materiality_score,
        risk_severity=event.risk_severity,
        confidence_score=event.confidence_score,
        source_quality=event.source_quality,
        title=event.title,
        summary=event.summary,
        facts=event.facts,
        uncertainties=event.uncertainties,
        status=event.status,
        publication_route=event.publication_route,
        publication_policy_version=event.publication_policy_version,
        publication_reasons=event.publication_reasons,
        observed_at=event.observed_at,
        evidence=evidence_items,
    )


def list_review_workbench(session: Session, user: User) -> list[ReviewWorkbenchOut]:
    if not user_has_role(session, user.id, "reviewer"):
        raise AccessDeniedError("reviewer role required")
    reviews = list(
        session.scalars(
            select(ReviewQueue)
            .where(ReviewQueue.tenant_id == user.tenant_id)
            .order_by(ReviewQueue.created_at)
        )
    )
    items: list[ReviewWorkbenchOut] = []
    for review in reviews:
        company: Company | None = None
        event_out: EventOut | None = None
        mention_text: str | None = None
        match_rule: str | None = None
        match_confidence: Decimal | None = None
        resolution_status: str | None = None
        if review.event_id is not None:
            event = session.get(Event, review.event_id)
            if event is not None:
                company = session.get(Company, event.company_id)
                event_out = _event_out(session, event)
        elif review.entity_mention_id is not None:
            mention = session.get(EntityMention, review.entity_mention_id)
            if mention is not None:
                mention_text = mention.mention_text
                match_rule = mention.match_rule
                match_confidence = mention.match_confidence
                resolution_status = mention.resolution_status
                if mention.candidate_company_id is not None:
                    company = session.get(Company, mention.candidate_company_id)
        items.append(
            ReviewWorkbenchOut(
                id=review.id,
                event_id=review.event_id,
                entity_mention_id=review.entity_mention_id,
                status=review.status,
                trigger_rules=review.trigger_rules,
                decision=review.decision,
                decision_reason=review.decision_reason,
                created_at=review.created_at,
                decided_at=review.decided_at,
                company_id=company.id if company is not None else None,
                company_legal_name=company.legal_name if company is not None else None,
                event=event_out,
                mention_text=mention_text,
                match_rule=match_rule,
                match_confidence=match_confidence,
                resolution_status=resolution_status,
            )
        )
    return items


def _active_refresh_job(session: Session, tenant_id: UUID, company_id: UUID) -> RefreshJob | None:
    return session.scalar(
        select(RefreshJob).where(
            RefreshJob.tenant_id == tenant_id,
            RefreshJob.company_id == company_id,
            RefreshJob.job_type == "mock_refresh",
            RefreshJob.status.in_(["queued", "running"]),
        )
    )


def _snapshot_freshness_status(
    snapshot: CompanySnapshot | None,
    policy: RefreshPolicy,
    now: datetime,
    active_job: RefreshJob | None,
) -> str:
    if active_job is not None:
        return "refreshing"
    if snapshot is None:
        return "unknown"
    if snapshot.freshness_status in {"unknown", "budget_deferred"}:
        return snapshot.freshness_status
    last_checked_at = snapshot.last_checked_at
    if last_checked_at.tzinfo is None:
        last_checked_at = last_checked_at.replace(tzinfo=UTC)
    expires_at = last_checked_at + timedelta(days=policy.recent_query_ttl_days)
    return "stale" if expires_at <= now else "fresh"


def list_companies(session: Session, user_id: UUID, policy: RefreshPolicy) -> list[CompanyListItem]:
    investment_rows = _authorized_investments(session, user_id)
    user = session.get(User, user_id)
    if user is None:
        return []
    company_ids = sorted({row[0].company_id for row in investment_rows}, key=str)
    items: list[CompanyListItem] = []
    risk_order = {"none": 0, "low": 1, "moderate": 2, "high": 3, "critical": 4}
    now = utc_now()
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
                freshness_status=_snapshot_freshness_status(
                    snapshot,
                    policy,
                    now,
                    _active_refresh_job(session, user.tenant_id, company_id),
                ),
                last_checked_at=snapshot.last_checked_at if snapshot else None,
                latest_event_title=events[0].title if events else None,
                highest_risk=highest_risk,
                information_gaps=snapshot.information_gaps if snapshot else ["尚无已发布快照"],
            )
        )
    return items


def get_company_detail(
    session: Session,
    user: User,
    company_id: UUID,
    policy: RefreshPolicy,
    *,
    auto_refresh_enabled: bool,
) -> CompanyDetail:
    investment_rows = _authorized_investments(session, user.id, company_id)
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
    now = utc_now()
    freshness_status = _snapshot_freshness_status(
        snapshot,
        policy,
        now,
        _active_refresh_job(session, user.tenant_id, company_id),
    )
    events = list(
        session.scalars(
            select(Event)
            .where(Event.company_id == company_id, Event.status == "published")
            .order_by(Event.occurred_at.desc())
        )
    )
    unconfirmed_leads = list(
        session.scalars(
            select(Event)
            .where(
                Event.company_id == company_id,
                Event.status == "candidate",
                Event.publication_route == "unconfirmed_lead",
            )
            .order_by(Event.observed_at.desc())
        )
    )
    detail = CompanyDetail(
        id=company.id,
        legal_name=company.legal_name,
        registered_region=company.registered_region,
        official_website=company.official_website,
        identity_status=company.identity_status,
        data_as_of=snapshot.data_as_of if snapshot else None,
        last_checked_at=snapshot.last_checked_at if snapshot else None,
        freshness_status=freshness_status,
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
        unconfirmed_leads=[_event_out(session, event) for event in unconfirmed_leads],
    )
    if auto_refresh_enabled and freshness_status in {"stale", "unknown"}:
        _create_or_merge_refresh_job(
            session,
            user.tenant_id,
            company_id,
            policy,
            refresh_reason="stale_query",
            now=now,
        )
    return detail


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
    if review.event_id is None:
        raise AccessDeniedError("entity mention review requires identity resolution workflow")
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
    company = session.get(Company, event.company_id)
    if company is None:
        raise AccessDeniedError("published event requires a resolved company")
    event.publication_route = "human_confirmed"
    event.publication_policy_version = "legacy-review-workbench-v1"
    event.publication_reasons = ["human_approved"]
    _mark_event_published(session, event, company)
    _refresh_company_snapshot(session, company)
    session.commit()
    return review


def _refresh_merge_candidate(
    session: Session, tenant_id: UUID, company_id: UUID, now: datetime
) -> RefreshJob | None:
    return session.scalar(
        select(RefreshJob)
        .where(
            RefreshJob.tenant_id == tenant_id,
            RefreshJob.company_id == company_id,
            RefreshJob.job_type == "mock_refresh",
            or_(
                RefreshJob.status.in_(["queued", "running"]),
                RefreshJob.cooldown_until > now,
            ),
        )
        .order_by(RefreshJob.created_at.desc())
    )


def _create_or_merge_refresh_job(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    policy: RefreshPolicy,
    *,
    refresh_reason: str,
    now: datetime,
) -> RefreshResult:
    existing = _refresh_merge_candidate(session, tenant_id, company_id, now)
    if existing is not None:
        return RefreshResult(status="merged", job_id=existing.id)
    cooldown = timedelta(hours=policy.request_cooldown_hours)
    cooldown_bucket = int(now.timestamp() // cooldown.total_seconds())
    idempotency_key = _sha256(
        f"{tenant_id}:{company_id}:mock_refresh:{policy.version}:{cooldown_bucket}"
    )
    job = RefreshJob(
        tenant_id=tenant_id,
        company_id=company_id,
        job_type="mock_refresh",
        refresh_reason=refresh_reason,
        status="queued",
        idempotency_key=idempotency_key,
        estimated_cost=Decimal("0"),
        cooldown_until=now + cooldown,
    )
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
    except IntegrityError:
        existing = _refresh_merge_candidate(session, tenant_id, company_id, now)
        if existing is None:
            existing = session.scalar(
                select(RefreshJob).where(RefreshJob.idempotency_key == idempotency_key)
            )
        if existing is None:
            raise
        return RefreshResult(status="merged", job_id=existing.id)
    session.commit()
    return RefreshResult(status="queued", job_id=job.id)


def request_refresh(
    session: Session,
    user: User,
    company_id: UUID,
    policy: RefreshPolicy,
    *,
    dry_run: bool,
) -> RefreshResult:
    if not _authorized_investments(session, user.id, company_id):
        raise NotFoundError("company not found")
    if dry_run:
        return RefreshResult(status="dry_run")
    return _create_or_merge_refresh_job(
        session,
        user.tenant_id,
        company_id,
        policy,
        refresh_reason="manual_request",
        now=utc_now(),
    )
