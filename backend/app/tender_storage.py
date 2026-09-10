"""Persist the bounded tender candidate within its original document scope."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.fact_support import fact_key
from backend.app.models import (
    ORGANIZATION_PRIVATE_SCOPE,
    PERSONAL_PRIVATE_SCOPE,
    PLATFORM_SHARED_SCOPE,
    Company,
    EntityMention,
    Event,
    EventEvidence,
    EventFact,
    EventFactSupport,
    EventObservation,
    RawDocument,
    ResearchImport,
    Source,
    User,
    utc_now,
)
from backend.app.services import (
    AccessDeniedError,
    ImportConflictError,
    _authorized_investments,
    _is_platform_shared_company,
    _scope_filters,
    _scope_owner_matches,
    user_has_role,
)
from backend.app.tender_events import (
    TenderCandidate,
    TenderDocument,
    TenderSubject,
    extract_tender_candidate,
    tender_candidate_facts,
)

SUPPORT_POLICY_VERSION = "tender-field-support-v1"
ALLOWED_LICENSES = {"public", "permission_confirmed"}


@dataclass(frozen=True)
class TenderIngestResult:
    status: str
    event_id: UUID | None = None
    observation_id: UUID | None = None
    event_created: bool = False


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _fresh(session: Session, model: type, object_id: UUID):
    return session.scalar(
        select(model).where(model.id == object_id).execution_options(populate_existing=True)
    )


def _authorize(session: Session, user: User, company: Company, document: RawDocument) -> None:
    organization_access = bool(_authorized_investments(session, user.id, company.id))
    if not _is_platform_shared_company(company) and not (
        company.tenant_id == user.tenant_id and organization_access
    ):
        raise AccessDeniedError("company is unavailable in the current scope")
    scope = document.visibility_scope
    if scope == PLATFORM_SHARED_SCOPE:
        allowed = (
            document.owner_user_id is None
            and document.owner_tenant_id is None
            and _is_platform_shared_company(company)
            and user_has_role(session, user.id, "platform_admin")
        )
    elif scope == PERSONAL_PRIVATE_SCOPE:
        allowed = document.owner_user_id == user.id and document.owner_tenant_id is None
    elif scope == ORGANIZATION_PRIVATE_SCOPE:
        allowed = (
            document.owner_user_id is None
            and document.owner_tenant_id == user.tenant_id
            and (
                organization_access
                or user_has_role(session, user.id, "institution_admin")
                or user_has_role(session, user.id, "reviewer")
            )
        )
    else:
        allowed = False
    if not allowed:
        raise AccessDeniedError("document is not writable in the current scope")


def _validated_input(
    session: Session, user: User, supplied: TenderCandidate
) -> tuple[User, Company, RawDocument, Source, TenderCandidate]:
    actor_id, tenant_id = user.id, user.tenant_id
    if session.get_bind().dialect.name == "postgresql":
        context = session.execute(
            text(
                "SELECT current_setting('app.current_user_id', true), "
                "current_setting('app.current_tenant_id', true)"
            )
        ).one()
        if tuple(context) != (str(actor_id), str(tenant_id)):
            raise AccessDeniedError("request database context must match the actor")
    actor = _fresh(session, User, actor_id)
    if actor is None or actor.status != "active" or actor.tenant_id != tenant_id:
        raise AccessDeniedError("active actor required")
    document = _fresh(session, RawDocument, supplied.document.document_id)
    company = _fresh(session, Company, supplied.subject.company_id)
    if document is None or company is None:
        raise AccessDeniedError("document or company is unavailable")
    _authorize(session, actor, company, document)
    source = _fresh(session, Source, document.source_id)
    if (
        source is None
        or source.license_status not in ALLOWED_LICENSES
        or document.license_status not in ALLOWED_LICENSES
    ):
        raise AccessDeniedError("source and document permission must be confirmed")
    if document.research_import_id is not None:
        batch = session.get(ResearchImport, document.research_import_id)
        if batch is None or batch.license_status not in ALLOWED_LICENSES:
            raise AccessDeniedError("research import permission is unavailable")
    mentions = list(
        session.scalars(
            select(EntityMention).where(
                EntityMention.raw_document_id == document.id,
                EntityMention.resolution_status == "verified",
            )
        )
    )
    if not mentions or any(
        mention.candidate_company_id != company.id or not _scope_owner_matches(mention, document)
        for mention in mentions
    ):
        raise ImportConflictError("document requires an unambiguous verified subject binding")
    bodies = {
        document.payload[key]
        for key in ("excerpt", "evidence_excerpt")
        if isinstance(document.payload.get(key), str) and document.payload[key].strip()
    }
    if len(bodies) != 1:
        raise ImportConflictError("exact stored evidence text is missing or ambiguous")
    if company.identity_status != "verified":
        raise ImportConflictError("company identity is not verified")
    expected = stored_tender_candidate(company, document)
    if expected is None or expected != supplied:
        raise ImportConflictError("candidate differs from stored subject, fields or evidence")
    return actor, company, document, source, expected


def stored_tender_candidate(company: Company, document: RawDocument) -> TenderCandidate | None:
    bodies = {
        document.payload[key]
        for key in ("excerpt", "evidence_excerpt")
        if isinstance(document.payload.get(key), str) and document.payload[key].strip()
    }
    if len(bodies) != 1 or company.identity_status != "verified":
        return None
    stored_document = TenderDocument(
        document_id=document.id,
        source_id=document.source_id,
        canonical_url=document.canonical_url,
        title=document.title,
        content_hash=document.content_hash,
        published_at=_aware(document.published_at),
        observed_at=_aware(document.observed_at),
        license_status=document.license_status,
        body=bodies.pop(),
    )
    subject = TenderSubject(
        company_id=company.id,
        legal_name=company.legal_name,
        credit_code=company.credit_code,
        identity_status="verified",
    )
    return extract_tender_candidate(stored_document, subject).candidate


def _new_event(
    company: Company,
    document: RawDocument,
    source: Source,
    candidate: TenderCandidate,
    fingerprint: str,
) -> Event:
    return Event(
        company_id=company.id,
        visibility_scope=document.visibility_scope,
        owner_user_id=document.owner_user_id,
        owner_tenant_id=document.owner_tenant_id,
        event_type="contract_commercial",
        event_subtype=f"tender_{candidate.phase}",
        fingerprint_version=candidate.fingerprint_version,
        event_fingerprint=fingerprint,
        status="candidate",
        publication_route="unconfirmed_lead",
        publication_policy_version=candidate.schema_version,
        publication_reasons=["tender_candidate_only", *candidate.issues],
        direction="unknown",
        # 沿用现有公开研究的合同类基础值；不把候选当作已履约或收入。
        materiality_score=70,
        risk_severity="low",
        confidence_score=Decimal("0.800") if source.source_quality == "A" else Decimal("0.700"),
        source_quality=source.source_quality,
        title=document.title[:200],
        summary=f"公开材料出现与该公司相关的{candidate.project_name[:160]}招投标候选记录。",
        facts=[
            {"name": name, "value": value, "unit": unit}
            for _, name, value, unit in tender_candidate_facts(candidate)
        ],
        uncertainties=["仅为来源所述候选，未确认履约、收入或投资影响。", *candidate.issues],
        # date 只存入观测记录，不伪造一个午夜发生时刻。
        occurred_at=None,
        published_at=document.published_at,
        published_on=document.published_on,
        observed_at=document.observed_at,
    )


def _append_evidence(
    session: Session,
    event: Event,
    document: RawDocument,
    candidate: TenderCandidate,
) -> list[dict[str, object]]:
    evidence_by_field: dict[str, EventEvidence] = {}
    for item in candidate.evidence:
        digest = _sha256(item.span.quote)
        evidence_id = uuid5(NAMESPACE_URL, f"tender-evidence:{event.id}:{document.id}:{digest}")
        evidence = session.get(EventEvidence, evidence_id)
        if evidence is None:
            evidence = EventEvidence(
                id=evidence_id,
                event_id=event.id,
                raw_document_id=document.id,
                visibility_scope=document.visibility_scope,
                owner_user_id=document.owner_user_id,
                owner_tenant_id=document.owner_tenant_id,
                evidence_excerpt=item.span.quote,
                span_hash=digest,
                support_type="supports",
            )
            session.add(evidence)
        elif (
            evidence.event_id != event.id
            or evidence.raw_document_id != document.id
            or evidence.evidence_excerpt != item.span.quote
            or evidence.span_hash != digest
            or not _scope_owner_matches(evidence, document)
        ):
            raise ImportConflictError("existing evidence does not match its origin")
        evidence_by_field[item.field] = evidence
    session.flush()
    existing = {
        fact.fact_key: fact
        for fact in session.scalars(select(EventFact).where(EventFact.event_id == event.id))
    }
    spans = {item.field: item.span for item in candidate.evidence}
    links = []
    for position, (field, name, value, unit) in enumerate(tender_candidate_facts(candidate)):
        key = fact_key(name, value, unit)
        fact = existing.get(key)
        if fact is None:
            fact = EventFact(
                id=uuid5(NAMESPACE_URL, f"dealflow-radar:event-fact:{event.id}:{key}"),
                event_id=event.id,
                fact_key=key,
                name=name,
                value=value,
                unit=unit,
                position=position,
                occurrence_count=1,
            )
            session.add(fact)
            session.flush()
        elif fact_key(fact.name, fact.value, fact.unit) != key:
            raise ImportConflictError("existing fact does not match its key")
        evidence, span = evidence_by_field[field], spans[field]
        support_id = uuid5(
            NAMESPACE_URL, f"dealflow-radar:event-fact-support:{fact.id}:{evidence.id}"
        )
        if session.get(EventFactSupport, support_id) is None:
            session.add(
                EventFactSupport(
                    id=support_id,
                    event_id=event.id,
                    event_fact_id=fact.id,
                    event_evidence_id=evidence.id,
                    support_status="supported",
                    evidence_locator={
                        "kind": "tender_field",
                        "field": field,
                        "start_offset": span.start_offset,
                        "end_offset": span.end_offset,
                        "text_hash": candidate.evidence_text_hash,
                    },
                    deterministic_checks={
                        "stored_text_reextracted": True,
                        "subject_binding_verified": True,
                        "normalization": candidate.schema_version,
                    },
                    support_reasons=["source_field_support_only_not_fact_confirmation"],
                    policy_version=SUPPORT_POLICY_VERSION,
                )
            )
        links.append(
            {
                "field": field,
                "fact_id": str(fact.id),
                "evidence_id": str(evidence.id),
                "support_id": str(support_id),
            }
        )
    session.flush()
    return links


def persist_tender_candidate(
    session: Session,
    user: User,
    candidate: TenderCandidate,
    *,
    enabled: bool = False,
) -> TenderIngestResult:
    """Internal opt-in only. Caller owns commit/rollback and authenticated DB context."""
    if not enabled:
        return TenderIngestResult("disabled")
    if session.new or session.dirty or session.deleted:
        raise ImportConflictError("flush intended inputs before candidate persistence")
    actor, company, document, source, verified = _validated_input(session, user, candidate)
    fingerprint = verified.business_key or _sha256(
        f"tender-document-only:{company.id}:{document.id}:{verified.schema_version}"
    )
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        scope_key = (
            f"tender:{document.visibility_scope}:{document.owner_user_id}:"
            f"{document.owner_tenant_id}:{company.id}:{fingerprint}"
        )
        lock_key = int.from_bytes(hashlib.sha256(scope_key.encode()).digest()[:8], signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    elif dialect == "sqlite":
        connection = session.connection()
        if not connection.connection.driver_connection.in_transaction:
            # SQLite deferred BEGIN must exist before SAVEPOINT to preserve outer rollback.
            connection.exec_driver_sql("BEGIN")
    with session.begin_nested():
        payload = verified.model_dump(mode="json")
        previous = session.scalar(
            select(EventObservation).where(
                EventObservation.raw_document_id == document.id,
                EventObservation.schema_version == verified.schema_version,
            )
        )
        if previous is not None:
            if previous.candidate_payload.get("candidate") != payload:
                raise ImportConflictError("stored observation changed; append a new document")
            return TenderIngestResult("duplicate", previous.event_id, previous.id)
        event = session.scalar(
            select(Event).where(
                Event.company_id == company.id,
                Event.fingerprint_version == verified.fingerprint_version,
                Event.event_fingerprint == fingerprint,
                *_scope_filters(
                    Event,
                    document.visibility_scope,
                    document.owner_user_id,
                    document.owner_tenant_id,
                ),
            )
        )
        created = event is None
        if event is None:
            event = _new_event(company, document, source, verified, fingerprint)
            session.add(event)
            session.flush()
        observations = list(
            session.scalars(select(EventObservation).where(EventObservation.event_id == event.id))
        )
        if verified.is_correction:
            kind = "correction_candidate"
        elif verified.issues:
            kind = "incomplete"
        elif not observations:
            kind = "initial"
        elif any(item.fact_version == verified.fact_version for item in observations):
            kind = "same_facts"
        else:
            kind = "conflicting"
        links = _append_evidence(session, event, document, verified)
        observation = EventObservation(
            event_id=event.id,
            raw_document_id=document.id,
            schema_version=verified.schema_version,
            fact_version=verified.fact_version,
            observation_kind=kind,
            occurred_on=verified.occurred_on,
            date_precision=verified.date_precision,
            candidate_payload={"candidate": payload, "field_links": links},
            created_by=actor.id,
            created_at=utc_now(),
        )
        session.add(observation)
        session.flush()
        return TenderIngestResult("stored", event.id, observation.id, created)
