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

from backend.app.config import IdentityPolicy, PublicationPolicy, RefreshPolicy
from backend.app.demo import (
    ALPHA_FUND_ID,
    ALPHA_TENANT_ID,
    ALPHA_USER_ID,
    BETA_FUND_ID,
    BETA_TENANT_ID,
    BETA_USER_ID,
    DEMO_SHARED_COMPANY_CREDIT_CODE,
    MOCK_SOURCE_ID,
    NO_ACCESS_USER_ID,
    demo_uuid,
)
from backend.app.models import (
    ORGANIZATION_PRIVATE_SCOPE,
    PERSONAL_PRIVATE_SCOPE,
    PLATFORM_SHARED_SCOPE,
    Company,
    CompanyAlias,
    CompanySnapshot,
    EntityMention,
    Event,
    EventEvidence,
    Fund,
    FundAccessGrant,
    Investment,
    OfficialIdentityVerification,
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
    LoadedOfficialIdentityImport,
    LoadedResearchImport,
    ManualResearchImportProvider,
    ManualResearchRecord,
    MockResearchProvider,
    MockResearchRecord,
    OfficialIdentityProvider,
    OfficialIdentityRecord,
)
from backend.app.schemas import (
    CompanyDetail,
    CompanyListItem,
    CompanySearchResult,
    EventOut,
    EvidenceOut,
    IdentityCandidateOut,
    IdentityResolutionOut,
    IngestResult,
    InvestmentOut,
    OfficialIdentityImportResult,
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
        company = _add_if_missing(
            session,
            Company,
            company_id,
            tenant_id=None,
            credit_code=DEMO_SHARED_COMPANY_CREDIT_CODE if index == 1 else None,
            legal_name=record.company_legal_name,
            registered_region=record.registered_region,
            identity_status="verified",
            visibility_scope="public",
        )
        if index == 1 and company.credit_code is None:
            company.credit_code = DEMO_SHARED_COMPANY_CREDIT_CODE
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
            visibility_scope=PLATFORM_SHARED_SCOPE,
            owner_user_id=None,
            owner_tenant_id=None,
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
            select(RawDocument).where(
                RawDocument.visibility_scope == PLATFORM_SHARED_SCOPE,
                RawDocument.owner_user_id.is_(None),
                RawDocument.owner_tenant_id.is_(None),
                RawDocument.document_dedupe_key == dedupe_key,
            )
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
            visibility_scope=PLATFORM_SHARED_SCOPE,
            owner_user_id=None,
            owner_tenant_id=None,
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
                visibility_scope=PLATFORM_SHARED_SCOPE,
                owner_user_id=None,
                owner_tenant_id=None,
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
            visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
            owner_user_id=None,
            owner_tenant_id=ALPHA_TENANT_ID,
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
                visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
                owner_user_id=None,
                owner_tenant_id=ALPHA_TENANT_ID,
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


def _normalized_identity_text(value: str) -> str:
    return "".join(value.split()).casefold()


def _is_platform_shared_company(company: Company) -> bool:
    return (
        company.tenant_id is None
        and company.visibility_scope == "public"
        and company.identity_status == "verified"
    )


def _regions_compatible(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return True
    normalized_left = _normalized_identity_text(left)
    normalized_right = _normalized_identity_text(right)
    return (
        normalized_left == normalized_right
        or normalized_left.startswith(normalized_right)
        or normalized_right.startswith(normalized_left)
    )


def _record_identity_check(company: Company, checked_at: datetime) -> None:
    current = company.last_identity_checked_at
    if current is None:
        company.last_identity_checked_at = checked_at
        return
    comparable_current = current if current.tzinfo is not None else current.replace(tzinfo=UTC)
    comparable_checked = (
        checked_at if checked_at.tzinfo is not None else checked_at.replace(tzinfo=UTC)
    )
    if comparable_checked > comparable_current:
        company.last_identity_checked_at = checked_at


def _official_identity_source(
    session: Session,
    loaded: LoadedOfficialIdentityImport,
) -> Source:
    source_payload = loaded.batch.source
    base_url = source_payload.base_url.rstrip("/")
    source = session.scalar(select(Source).where(Source.code == source_payload.code))
    if source is None:
        source = Source(
            code=source_payload.code,
            name=source_payload.name,
            source_quality="A",
            license_status="public",
            base_url=base_url,
        )
        session.add(source)
        session.flush()
        return source
    if (
        source.name != source_payload.name
        or source.source_quality != "A"
        or source.license_status != "public"
        or (source.base_url or "").rstrip("/") != base_url
    ):
        raise ImportConflictError(f"source_code conflict: {source_payload.code}")
    return source


def _resolve_official_identity_company(
    session: Session,
    tenant_id: UUID,
    record: OfficialIdentityRecord,
) -> tuple[Company | None, str, str]:
    visible_company = or_(Company.tenant_id.is_(None), Company.tenant_id == tenant_id)
    company = session.scalar(
        select(Company).where(visible_company, Company.credit_code == record.credit_code)
    )
    if company is not None:
        _record_identity_check(company, record.checked_at)
        name_matches = company.legal_name == record.legal_name
        region_matches = _regions_compatible(company.registered_region, record.registered_region)
        if name_matches and region_matches:
            company.identity_status = "verified"
            if company.registered_region is None:
                company.registered_region = record.registered_region
            return company, "official_credit_code_exact", "verified"
        reasons = []
        if not name_matches:
            reasons.append("legal_name")
        if not region_matches:
            reasons.append("registered_region")
        return company, f"official_credit_code_{'_'.join(reasons)}_conflict", "conflict"

    name_candidates = list(
        session.scalars(
            select(Company).where(visible_company, Company.legal_name == record.legal_name)
        )
    )
    if len(name_candidates) != 1:
        return None, "official_record_unmatched", "unmatched"
    company = name_candidates[0]
    _record_identity_check(company, record.checked_at)
    if company.credit_code is not None and company.credit_code != record.credit_code:
        return company, "official_legal_name_credit_code_conflict", "conflict"
    if not _regions_compatible(company.registered_region, record.registered_region):
        return company, "official_legal_name_registered_region_conflict", "conflict"
    company.credit_code = record.credit_code
    company.identity_status = "verified"
    if company.registered_region is None:
        company.registered_region = record.registered_region
    return company, "official_legal_name_exact_credit_code_enriched", "verified"


def _official_identity_counts(
    session: Session,
    research_import_id: UUID,
) -> tuple[int, int, int]:
    rows = session.execute(
        select(OfficialIdentityVerification.verification_status, func.count())
        .join(RawDocument, RawDocument.id == OfficialIdentityVerification.raw_document_id)
        .where(RawDocument.research_import_id == research_import_id)
        .group_by(OfficialIdentityVerification.verification_status)
    ).all()
    counts = {status: count for status, count in rows}
    return (
        counts.get("verified", 0),
        counts.get("conflict", 0),
        counts.get("unmatched", 0),
    )


def _existing_official_identity_result(
    session: Session,
    existing: ResearchImport,
) -> OfficialIdentityImportResult:
    verified, conflicts, unmatched = _official_identity_counts(session, existing.id)
    return OfficialIdentityImportResult(
        status="duplicate",
        research_import_id=existing.id,
        batch_id=existing.batch_id,
        records_seen=existing.record_count,
        verifications_created=0,
        verified_records=verified,
        conflict_records=conflicts,
        unmatched_records=unmatched,
    )


def import_official_identities(
    session: Session,
    user: User,
    provider: OfficialIdentityProvider,
) -> OfficialIdentityImportResult:
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
        result = _existing_official_identity_result(session, existing_file)
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
        batch = loaded.batch
        research_import = ResearchImport(
            tenant_id=user.tenant_id,
            imported_by=user.id,
            schema_version=batch.schema_version,
            batch_id=batch.batch_id,
            queried_at=batch.queried_at,
            research_tool=provider.code,
            agent_name=None,
            original_query=batch.original_query,
            target_company_hint=batch.target_company_hint,
            source_filename=loaded.source_filename,
            file_format="json",
            file_hash=loaded.file_hash,
            parser_version=provider.parser_version,
            license_status=batch.license_status,
            status="processing",
            record_count=len(batch.records),
            resolved_count=0,
            unresolved_count=0,
            auto_published_count=0,
            unconfirmed_count=0,
            identity_review_count=0,
        )
        session.add(research_import)
        session.flush()
        source = _official_identity_source(session, loaded)
        verified_count = 0
        conflict_count = 0
        unmatched_count = 0

        for record in batch.records:
            company, match_rule, verification_status = _resolve_official_identity_company(
                session,
                user.tenant_id,
                record,
            )
            if verification_status == "verified":
                verified_count += 1
                research_import.resolved_count += 1
            else:
                research_import.unresolved_count += 1
                if verification_status == "conflict":
                    conflict_count += 1
                else:
                    unmatched_count += 1

            record_payload = record.model_dump(mode="json")
            content_hash = _sha256(json.dumps(record_payload, ensure_ascii=False, sort_keys=True))
            source_record_key = _sha256(
                f"{user.tenant_id}:{batch.batch_id}:{record.external_record_id}"
            )
            document = RawDocument(
                source_id=source.id,
                research_import_id=research_import.id,
                visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
                owner_user_id=None,
                owner_tenant_id=user.tenant_id,
                external_record_id=f"official-identity:{source_record_key[:40]}",
                canonical_url=record.canonical_url,
                title=f"工商身份核验：{record.legal_name}",
                published_at=None,
                published_on=None,
                observed_at=record.checked_at,
                content_hash=content_hash,
                document_dedupe_key=_sha256(f"{source.id}:{source_record_key}"),
                license_status="public",
                payload=record_payload,
            )
            session.add(document)
            session.flush()
            session.add(
                OfficialIdentityVerification(
                    tenant_id=user.tenant_id,
                    company_id=company.id if company is not None else None,
                    raw_document_id=document.id,
                    query_text=record.query_text,
                    legal_name=record.legal_name,
                    credit_code=record.credit_code,
                    registered_region=record.registered_region,
                    registration_status=record.registration_status,
                    verification_status=verification_status,
                    match_rule=match_rule,
                    checked_at=record.checked_at,
                )
            )

        research_import.status = (
            "completed_with_unresolved" if research_import.unresolved_count else "completed"
        )
        session.add(
            UsageLedger(
                tenant_id=user.tenant_id,
                provider=provider.code,
                operation="official_identity_import",
                external_calls=provider.external_calls,
                input_tokens=0,
                output_tokens=0,
                estimated_cost=Decimal(str(provider.estimated_cost)),
                metrics={
                    "research_import_id": str(research_import.id),
                    "records_seen": research_import.record_count,
                    "verified_records": verified_count,
                    "conflict_records": conflict_count,
                    "unmatched_records": unmatched_count,
                },
                idempotency_key=_sha256(f"official-identity-import:{research_import.id}"),
            )
        )
        session.commit()
        return OfficialIdentityImportResult(
            status=research_import.status,
            research_import_id=research_import.id,
            batch_id=research_import.batch_id,
            records_seen=research_import.record_count,
            verifications_created=research_import.record_count,
            verified_records=verified_count,
            conflict_records=conflict_count,
            unmatched_records=unmatched_count,
            external_calls=provider.external_calls,
            estimated_cost=Decimal(str(provider.estimated_cost)),
        )
    except Exception:
        session.rollback()
        raise


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
    document = session.get(RawDocument, document_id)
    if document is None:
        raise NotFoundError("evidence document not found")
    session.add(
        EventEvidence(
            event_id=event_id,
            raw_document_id=document_id,
            visibility_scope=document.visibility_scope,
            owner_user_id=document.owner_user_id,
            owner_tenant_id=document.owner_tenant_id,
            evidence_excerpt=record.evidence_excerpt,
            span_hash=_sha256(record.evidence_excerpt),
            support_type="supports",
        )
    )


def _mark_event_published(session: Session, event: Event, company: Company) -> None:
    evidence_rows = session.execute(
        select(EventEvidence, RawDocument)
        .join(RawDocument, RawDocument.id == EventEvidence.raw_document_id)
        .where(EventEvidence.event_id == event.id)
    ).all()
    if not evidence_rows or company.identity_status != "verified":
        raise AccessDeniedError("published event requires verified identity and evidence")
    if _is_platform_shared_company(company) and all(
        document.visibility_scope == PLATFORM_SHARED_SCOPE for _, document in evidence_rows
    ):
        event.visibility_scope = PLATFORM_SHARED_SCOPE
        event.owner_user_id = None
        event.owner_tenant_id = None
        for evidence, _ in evidence_rows:
            evidence.visibility_scope = PLATFORM_SHARED_SCOPE
            evidence.owner_user_id = None
            evidence.owner_tenant_id = None
    event.status = "published"


def _scope_filters(
    model: type[Event] | type[CompanySnapshot],
    visibility_scope: str,
    owner_user_id: UUID | None,
    owner_tenant_id: UUID | None,
) -> tuple[object, ...]:
    return (
        model.visibility_scope == visibility_scope,
        model.owner_user_id == owner_user_id,
        model.owner_tenant_id == owner_tenant_id,
    )


def _refresh_company_snapshot(
    session: Session,
    company: Company,
    visibility_scope: str,
    owner_user_id: UUID | None,
    owner_tenant_id: UUID | None,
) -> None:
    scope_filters = _scope_filters(
        CompanySnapshot,
        visibility_scope,
        owner_user_id,
        owner_tenant_id,
    )
    session.execute(
        update(CompanySnapshot)
        .where(
            CompanySnapshot.company_id == company.id,
            CompanySnapshot.is_current.is_(True),
            *scope_filters,
        )
        .values(is_current=False)
    )
    current_version = session.scalar(
        select(func.max(CompanySnapshot.snapshot_version)).where(
            CompanySnapshot.company_id == company.id
        )
    )
    published_events = list(
        session.scalars(
            select(Event).where(
                Event.company_id == company.id,
                Event.status == "published",
                *_scope_filters(
                    Event,
                    visibility_scope,
                    owner_user_id,
                    owner_tenant_id,
                ),
            )
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
            visibility_scope=visibility_scope,
            owner_user_id=owner_user_id,
            owner_tenant_id=owner_tenant_id,
            snapshot_version=(current_version or 0) + 1,
            is_current=True,
            data_as_of=max(event_dates, default=None),
            last_checked_at=utc_now(),
            freshness_status="fresh",
            summary={"published_event_count": len(published_events), "highest_risk": highest_risk},
            information_gaps=information_gaps,
        )
    )


def _stored_document_verification(document: RawDocument) -> DocumentVerification:
    payload = document.payload.get("_source_verification")
    if not isinstance(payload, dict):
        return DisabledDocumentVerifier().verify(document.canonical_url)
    try:
        return DocumentVerification.model_validate(payload)
    except ValueError:
        return DisabledDocumentVerifier().verify(document.canonical_url)


def _route_manual_record(
    session: Session,
    company: Company,
    record: ManualResearchRecord,
    document: RawDocument,
    verification: DocumentVerification,
    publication_policy: PublicationPolicy,
    owner_tenant_id: UUID,
) -> tuple[Event, bool, str, bool]:
    publication_route, publication_reasons = _evaluate_publication_route(
        company,
        record,
        verification,
        publication_policy,
    )
    event_fingerprint = _manual_event_fingerprint(company.id, record)
    existing_event = session.scalar(
        select(Event).where(
            Event.company_id == company.id,
            Event.visibility_scope == ORGANIZATION_PRIVATE_SCOPE,
            Event.owner_tenant_id == owner_tenant_id,
            Event.fingerprint_version == MANUAL_EVENT_FINGERPRINT_VERSION,
            Event.event_fingerprint == event_fingerprint,
        )
    )
    if existing_event is not None:
        snapshot_required = False
        if existing_event.status == "candidate":
            _add_manual_event_evidence(session, existing_event.id, document.id, record)
            existing_event.publication_route = publication_route
            existing_event.publication_policy_version = publication_policy.version
            existing_event.publication_reasons = publication_reasons
            if publication_route == "auto_published":
                _mark_event_published(session, existing_event, company)
                snapshot_required = True
        elif existing_event.status == "published" and publication_route == "auto_published":
            _add_manual_event_evidence(session, existing_event.id, document.id, record)
            existing_event.publication_reasons = sorted(
                {*existing_event.publication_reasons, "additional_evidence_auto_attached"}
            )
        elif existing_event.status == "in_review":
            _add_manual_event_evidence(session, existing_event.id, document.id, record)
        return existing_event, False, publication_route, snapshot_required

    event = Event(
        company_id=company.id,
        visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
        owner_user_id=None,
        owner_tenant_id=owner_tenant_id,
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
    snapshot_required = publication_route == "auto_published"
    if snapshot_required:
        _mark_event_published(session, event, company)
    return event, True, publication_route, snapshot_required


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
    published_snapshot_scopes: set[tuple[UUID, str, UUID | None, UUID | None]] = set()

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
            select(RawDocument).where(
                RawDocument.visibility_scope == ORGANIZATION_PRIVATE_SCOPE,
                RawDocument.owner_user_id.is_(None),
                RawDocument.owner_tenant_id == user.tenant_id,
                RawDocument.document_dedupe_key == dedupe_key,
            )
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
            visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
            owner_user_id=None,
            owner_tenant_id=user.tenant_id,
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
            visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
            owner_user_id=None,
            owner_tenant_id=user.tenant_id,
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

        routed_event, event_created, publication_route, snapshot_required = _route_manual_record(
            session,
            company,
            record,
            document,
            verification,
            publication_policy,
            user.tenant_id,
        )
        if publication_route == "auto_published":
            research_import.auto_published_count += 1
        else:
            research_import.unconfirmed_count += 1

        if snapshot_required:
            published_snapshot_scopes.add(
                (
                    company.id,
                    routed_event.visibility_scope,
                    routed_event.owner_user_id,
                    routed_event.owner_tenant_id,
                )
            )
        if event_created:
            events_created += 1

    for company_id, scope, owner_user_id, owner_tenant_id in sorted(
        published_snapshot_scopes,
        key=lambda item: tuple(str(value) for value in item),
    ):
        company = session.get(Company, company_id)
        if company is not None:
            _refresh_company_snapshot(
                session,
                company,
                scope,
                owner_user_id,
                owner_tenant_id,
            )

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


def _readable_scope_clause(
    model: type[CompanyAlias]
    | type[RawDocument]
    | type[EntityMention]
    | type[Event]
    | type[EventEvidence]
    | type[CompanySnapshot],
    user: User,
    *,
    allow_organization_private: bool,
) -> object:
    clauses = [
        (model.visibility_scope == PLATFORM_SHARED_SCOPE)
        & model.owner_user_id.is_(None)
        & model.owner_tenant_id.is_(None),
        (model.visibility_scope == PERSONAL_PRIVATE_SCOPE)
        & (model.owner_user_id == user.id)
        & model.owner_tenant_id.is_(None),
    ]
    if allow_organization_private:
        clauses.append(
            (model.visibility_scope == ORGANIZATION_PRIVATE_SCOPE)
            & model.owner_user_id.is_(None)
            & (model.owner_tenant_id == user.tenant_id)
        )
    return or_(*clauses)


def _event_out(
    session: Session,
    event: Event,
    user: User,
    *,
    allow_organization_private: bool,
) -> EventOut:
    evidence_rows = session.execute(
        select(EventEvidence, RawDocument, Source)
        .join(RawDocument, RawDocument.id == EventEvidence.raw_document_id)
        .join(Source, Source.id == RawDocument.source_id)
        .where(
            EventEvidence.event_id == event.id,
            _readable_scope_clause(
                EventEvidence,
                user,
                allow_organization_private=allow_organization_private,
            ),
            _readable_scope_clause(
                RawDocument,
                user,
                allow_organization_private=allow_organization_private,
            ),
        )
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
                visibility_scope=evidence.visibility_scope,
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
        visibility_scope=event.visibility_scope,
    )


def _identity_candidates_for_mention(
    session: Session,
    tenant_id: UUID,
    mention: EntityMention,
    identity_policy: IdentityPolicy,
) -> list[IdentityCandidateOut]:
    document = session.get(RawDocument, mention.raw_document_id)
    if document is None:
        return []
    identity_payload = document.payload.get("company_identity_evidence", {})
    if not isinstance(identity_payload, dict):
        identity_payload = {}
    evidence_credit_code = identity_payload.get("credit_code")
    evidence_legal_name = identity_payload.get("legal_name")
    cutoff = utc_now() - timedelta(days=identity_policy.verification_ttl_days)
    rows = session.execute(
        select(OfficialIdentityVerification, Company, RawDocument, Source)
        .join(Company, Company.id == OfficialIdentityVerification.company_id)
        .join(RawDocument, RawDocument.id == OfficialIdentityVerification.raw_document_id)
        .join(Source, Source.id == RawDocument.source_id)
        .where(
            OfficialIdentityVerification.tenant_id == tenant_id,
            OfficialIdentityVerification.company_id.is_not(None),
            OfficialIdentityVerification.verification_status.in_(["verified", "conflict"]),
        )
        .order_by(OfficialIdentityVerification.checked_at.desc())
    ).all()
    candidates: list[IdentityCandidateOut] = []
    seen_company_ids: set[UUID] = set()
    normalized_mention = _normalized_identity_text(mention.mention_text)
    for verification, company, evidence_document, source in rows:
        checked_at = verification.checked_at
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=UTC)
        if checked_at < cutoff:
            continue
        if company.credit_code is not None and company.credit_code != verification.credit_code:
            continue
        associated = any(
            [
                mention.candidate_company_id == company.id,
                _normalized_identity_text(verification.query_text) == normalized_mention,
                evidence_credit_code == verification.credit_code,
                evidence_legal_name == verification.legal_name,
            ]
        )
        if not associated or company.id in seen_company_ids:
            continue
        seen_company_ids.add(company.id)
        candidates.append(
            IdentityCandidateOut(
                verification_id=verification.id,
                company_id=company.id,
                legal_name=verification.legal_name,
                credit_code=verification.credit_code,
                registered_region=verification.registered_region,
                registration_status=verification.registration_status,
                verification_status=verification.verification_status,
                match_rule=verification.match_rule,
                checked_at=checked_at,
                source_name=source.name,
                canonical_url=evidence_document.canonical_url,
            )
        )
    return candidates


def list_review_workbench(
    session: Session,
    user: User,
    identity_policy: IdentityPolicy | None = None,
) -> list[ReviewWorkbenchOut]:
    if not user_has_role(session, user.id, "reviewer"):
        raise AccessDeniedError("reviewer role required")
    resolved_identity_policy = identity_policy or IdentityPolicy()
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
        identity_candidates: list[IdentityCandidateOut] = []
        if review.event_id is not None:
            event = session.get(Event, review.event_id)
            if event is not None:
                company = session.get(Company, event.company_id)
                event_out = _event_out(
                    session,
                    event,
                    user,
                    allow_organization_private=True,
                )
        elif review.entity_mention_id is not None:
            mention = session.get(EntityMention, review.entity_mention_id)
            if mention is not None:
                mention_text = mention.mention_text
                match_rule = mention.match_rule
                match_confidence = mention.match_confidence
                resolution_status = mention.resolution_status
                if mention.candidate_company_id is not None:
                    company = session.get(Company, mention.candidate_company_id)
                if review.status == "pending":
                    identity_candidates = _identity_candidates_for_mention(
                        session,
                        user.tenant_id,
                        mention,
                        resolved_identity_policy,
                    )
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
                identity_candidates=identity_candidates,
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
        snapshot_scope = (
            PLATFORM_SHARED_SCOPE
            if _is_platform_shared_company(company)
            else ORGANIZATION_PRIVATE_SCOPE
        )
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == company_id,
                CompanySnapshot.is_current.is_(True),
                CompanySnapshot.visibility_scope == snapshot_scope,
                CompanySnapshot.owner_user_id.is_(None),
                CompanySnapshot.owner_tenant_id
                == (None if snapshot_scope == PLATFORM_SHARED_SCOPE else user.tenant_id),
            )
        )
        events = list(
            session.scalars(
                select(Event)
                .where(
                    Event.company_id == company_id,
                    Event.status == "published",
                    or_(
                        (Event.visibility_scope == PLATFORM_SHARED_SCOPE)
                        & Event.owner_user_id.is_(None)
                        & Event.owner_tenant_id.is_(None),
                        (Event.visibility_scope == ORGANIZATION_PRIVATE_SCOPE)
                        & Event.owner_user_id.is_(None)
                        & (Event.owner_tenant_id == user.tenant_id),
                    ),
                )
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


def search_companies(
    session: Session,
    user: User,
    query: str,
    policy: RefreshPolicy,
) -> list[CompanySearchResult]:
    normalized_query = _normalized_identity_text(query)
    exact_query = query.strip()
    if not normalized_query:
        return []
    alias_company_ids = select(CompanyAlias.company_id).where(
        CompanyAlias.visibility_scope == PLATFORM_SHARED_SCOPE,
        CompanyAlias.owner_user_id.is_(None),
        CompanyAlias.owner_tenant_id.is_(None),
        CompanyAlias.verification_status == "verified",
        CompanyAlias.normalized_alias == normalized_query,
    )
    companies = list(
        session.scalars(
            select(Company)
            .where(
                Company.tenant_id.is_(None),
                Company.visibility_scope == "public",
                Company.identity_status == "verified",
                or_(
                    Company.credit_code == exact_query.upper(),
                    Company.legal_name == exact_query,
                    Company.id.in_(alias_company_ids),
                ),
            )
            .order_by(Company.legal_name)
            .limit(20)
        )
    )
    now = utc_now()
    results: list[CompanySearchResult] = []
    for company in companies:
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == company.id,
                CompanySnapshot.is_current.is_(True),
                CompanySnapshot.visibility_scope == PLATFORM_SHARED_SCOPE,
                CompanySnapshot.owner_user_id.is_(None),
                CompanySnapshot.owner_tenant_id.is_(None),
            )
        )
        results.append(
            CompanySearchResult(
                id=company.id,
                legal_name=company.legal_name,
                credit_code=company.credit_code,
                registered_region=company.registered_region,
                identity_status=company.identity_status,
                freshness_status=_snapshot_freshness_status(snapshot, policy, now, None),
                last_checked_at=snapshot.last_checked_at if snapshot else None,
            )
        )
    return results


def get_company_detail(
    session: Session,
    user: User,
    company_id: UUID,
    policy: RefreshPolicy,
    *,
    auto_refresh_enabled: bool,
) -> CompanyDetail:
    investment_rows = _authorized_investments(session, user.id, company_id)
    company = session.get(Company, company_id)
    if company is None:
        raise NotFoundError("company not found")
    shared_company = _is_platform_shared_company(company)
    if not shared_company and not investment_rows:
        raise NotFoundError("company not found")
    snapshot_scope = PLATFORM_SHARED_SCOPE if shared_company else ORGANIZATION_PRIVATE_SCOPE
    snapshot = session.scalar(
        select(CompanySnapshot).where(
            CompanySnapshot.company_id == company_id,
            CompanySnapshot.is_current.is_(True),
            CompanySnapshot.visibility_scope == snapshot_scope,
            CompanySnapshot.owner_user_id.is_(None),
            CompanySnapshot.owner_tenant_id == (None if shared_company else user.tenant_id),
        )
    )
    now = utc_now()
    freshness_status = _snapshot_freshness_status(
        snapshot,
        policy,
        now,
        _active_refresh_job(session, user.tenant_id, company_id) if investment_rows else None,
    )
    events = list(
        session.scalars(
            select(Event)
            .where(
                Event.company_id == company_id,
                Event.status == "published",
                Event.visibility_scope == PLATFORM_SHARED_SCOPE,
                Event.owner_user_id.is_(None),
                Event.owner_tenant_id.is_(None),
            )
            .order_by(Event.occurred_at.desc())
        )
    )
    private_scope_clause = _readable_scope_clause(
        Event,
        user,
        allow_organization_private=bool(investment_rows),
    )
    private_events = list(
        session.scalars(
            select(Event)
            .where(
                Event.company_id == company_id,
                Event.status == "published",
                Event.visibility_scope != PLATFORM_SHARED_SCOPE,
                private_scope_clause,
            )
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
                Event.visibility_scope.in_([PERSONAL_PRIVATE_SCOPE, ORGANIZATION_PRIVATE_SCOPE]),
                private_scope_clause,
            )
            .order_by(Event.observed_at.desc())
        )
    )
    detail = CompanyDetail(
        id=company.id,
        legal_name=company.legal_name,
        credit_code=company.credit_code,
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
        events=[
            _event_out(
                session,
                event,
                user,
                allow_organization_private=bool(investment_rows),
            )
            for event in events
        ],
        private_events=[
            _event_out(
                session,
                event,
                user,
                allow_organization_private=bool(investment_rows),
            )
            for event in private_events
        ],
        unconfirmed_leads=[
            _event_out(
                session,
                event,
                user,
                allow_organization_private=bool(investment_rows),
            )
            for event in unconfirmed_leads
        ],
    )
    if investment_rows and auto_refresh_enabled and freshness_status in {"stale", "unknown"}:
        _create_or_merge_refresh_job(
            session,
            user.tenant_id,
            company_id,
            policy,
            refresh_reason="stale_query",
            now=now,
        )
    return detail


def _record_former_legal_name(
    session: Session,
    company: Company,
    source_id: UUID,
    former_name: str,
    owner_tenant_id: UUID,
) -> None:
    normalized_alias = _normalized_identity_text(former_name)
    existing = session.scalar(
        select(CompanyAlias).where(
            CompanyAlias.company_id == company.id,
            CompanyAlias.visibility_scope == ORGANIZATION_PRIVATE_SCOPE,
            CompanyAlias.owner_user_id.is_(None),
            CompanyAlias.owner_tenant_id == owner_tenant_id,
            CompanyAlias.normalized_alias == normalized_alias,
            CompanyAlias.alias_type == "former_legal_name",
        )
    )
    if existing is None:
        session.add(
            CompanyAlias(
                company_id=company.id,
                source_id=source_id,
                alias=former_name,
                normalized_alias=normalized_alias,
                alias_type="former_legal_name",
                verification_status="verified",
                visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
                owner_user_id=None,
                owner_tenant_id=owner_tenant_id,
            )
        )


def resolve_identity_review(
    session: Session,
    review_id: UUID,
    user: User,
    verification_id: UUID,
    reason: str,
    identity_policy: IdentityPolicy | None = None,
    publication_policy: PublicationPolicy | None = None,
) -> IdentityResolutionOut:
    if not user_has_role(session, user.id, "reviewer") or not user_has_role(
        session, user.id, "institution_admin"
    ):
        session.rollback()
        raise AccessDeniedError("reviewer and institution_admin roles required")
    resolved_identity_policy = identity_policy or IdentityPolicy()
    resolved_publication_policy = publication_policy or PublicationPolicy()
    try:
        review = session.get(ReviewQueue, review_id)
        if review is None or review.tenant_id != user.tenant_id:
            raise NotFoundError("review not found")
        if review.status != "pending":
            raise AccessDeniedError("review already decided")
        if review.entity_mention_id is None or review.event_id is not None:
            raise AccessDeniedError("review is not an identity resolution item")
        mention = session.get(EntityMention, review.entity_mention_id)
        if mention is None:
            raise NotFoundError("entity mention not found")
        candidates = _identity_candidates_for_mention(
            session,
            user.tenant_id,
            mention,
            resolved_identity_policy,
        )
        selected = next(
            (candidate for candidate in candidates if candidate.verification_id == verification_id),
            None,
        )
        if selected is None:
            raise AccessDeniedError("official identity candidate is missing, stale, or unrelated")
        verification = session.get(OfficialIdentityVerification, verification_id)
        if verification is None or verification.company_id is None:
            raise AccessDeniedError("official identity candidate is unavailable")
        company = session.get(Company, verification.company_id)
        identity_document = session.get(RawDocument, verification.raw_document_id)
        source_document = session.get(RawDocument, mention.raw_document_id)
        if company is None or identity_document is None or source_document is None:
            raise NotFoundError("identity resolution evidence not found")
        if company.credit_code is not None and company.credit_code != verification.credit_code:
            raise AccessDeniedError("selected company credit code conflicts with official evidence")

        former_name = company.legal_name
        if former_name != verification.legal_name:
            _record_former_legal_name(
                session,
                company,
                identity_document.source_id,
                former_name,
                user.tenant_id,
            )
        company.legal_name = verification.legal_name
        company.credit_code = verification.credit_code
        if verification.registered_region is not None:
            company.registered_region = verification.registered_region
        company.identity_status = "verified"
        _record_identity_check(company, verification.checked_at)

        mention.candidate_company_id = company.id
        mention.match_rule = f"official_identity_selected:{resolved_identity_policy.version}"
        mention.match_confidence = Decimal("1.000")
        mention.resolution_status = "verified"
        review.status = "approved"
        review.decision = "resolve_identity"
        review.decision_reason = reason
        review.assigned_user_id = user.id
        review.decided_at = utc_now()

        record_payload = {
            key: value for key, value in source_document.payload.items() if not key.startswith("_")
        }
        record = ManualResearchRecord.model_validate(record_payload)
        event, _, publication_route, snapshot_required = _route_manual_record(
            session,
            company,
            record,
            source_document,
            _stored_document_verification(source_document),
            resolved_publication_policy,
            user.tenant_id,
        )
        if snapshot_required:
            _refresh_company_snapshot(
                session,
                company,
                event.visibility_scope,
                event.owner_user_id,
                event.owner_tenant_id,
            )

        if source_document.research_import_id is not None:
            research_import = session.get(ResearchImport, source_document.research_import_id)
            if research_import is not None and research_import.tenant_id == user.tenant_id:
                research_import.resolved_count += 1
                research_import.unresolved_count = max(0, research_import.unresolved_count - 1)
                research_import.identity_review_count = max(
                    0, research_import.identity_review_count - 1
                )
                if publication_route == "auto_published":
                    research_import.auto_published_count += 1
                else:
                    research_import.unconfirmed_count += 1
                research_import.status = (
                    "completed_with_unresolved" if research_import.unresolved_count else "completed"
                )

        session.add(
            UsageLedger(
                tenant_id=user.tenant_id,
                company_id=company.id,
                provider="official_identity_resolution",
                operation="identity_review_resolution",
                external_calls=0,
                input_tokens=0,
                output_tokens=0,
                estimated_cost=Decimal("0"),
                metrics={
                    "review_id": str(review.id),
                    "entity_mention_id": str(mention.id),
                    "verification_id": str(verification.id),
                    "event_id": str(event.id),
                    "publication_route": publication_route,
                    "identity_policy_version": resolved_identity_policy.version,
                    "publication_policy_version": resolved_publication_policy.version,
                },
                idempotency_key=_sha256(f"identity-review-resolution:{review.id}"),
            )
        )
        session.commit()
        return IdentityResolutionOut(
            review_id=review.id,
            company_id=company.id,
            event_id=event.id,
            publication_route=publication_route,
            review_status=review.status,
        )
    except Exception:
        session.rollback()
        raise


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
    _refresh_company_snapshot(
        session,
        company,
        event.visibility_scope,
        event.owner_user_id,
        event.owner_tenant_id,
    )
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
