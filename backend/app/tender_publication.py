"""复用人工共享授权，选择有完整证据的一份中标观测。"""

from __future__ import annotations

import hashlib
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.fact_support import fact_key
from backend.app.models import (
    Company,
    Event,
    EventEvidence,
    EventFact,
    EventFactSupport,
    EventObservation,
    RawDocument,
    ResearchImport,
)
from backend.app.tender_events import TenderCandidate, tender_candidate_facts
from backend.app.tender_presentation import candidate_from_observation


def select_tender_observation(
    session: Session,
    event: Event,
    observation_id: UUID | None,
    evidence_ids: list[UUID],
) -> tuple[EventObservation, TenderCandidate]:
    from backend.app.services import PromotionEligibilityError
    from backend.app.tender_storage import stored_tender_candidate

    rows = list(
        session.scalars(select(EventObservation).where(EventObservation.event_id == event.id))
    )
    if observation_id is None:
        if len({item.fact_version for item in rows}) != 1:
            raise PromotionEligibilityError("select one observation when facts differ")
        document_ids = set(
            session.scalars(
                select(EventEvidence.raw_document_id).where(EventEvidence.id.in_(evidence_ids))
            )
        )
        rows = [item for item in rows if item.raw_document_id in document_ids]
    else:
        rows = [item for item in rows if item.id == observation_id]
    if len(rows) != 1:
        raise PromotionEligibilityError("selected observation does not belong to this event")
    observation = rows[0]
    document = session.get(RawDocument, observation.raw_document_id)
    company = session.get(Company, event.company_id)
    candidate = candidate_from_observation(observation)
    if (
        document is None
        or company is None
        or stored_tender_candidate(company, document) != candidate
    ):
        raise PromotionEligibilityError("stored tender evidence or subject changed")
    if (
        candidate.issues
        or candidate.business_key is None
        or observation.observation_kind == "conflicting"
    ):
        raise PromotionEligibilityError("incomplete tender observation must remain unconfirmed")
    if document.research_import_id is not None:
        batch = session.get(ResearchImport, document.research_import_id)
        if batch is None or batch.license_status not in {"public", "permission_confirmed"}:
            raise PromotionEligibilityError("research import permission is unavailable")
    required = {UUID(link["evidence_id"]) for link in observation.candidate_payload["field_links"]}
    available = set(
        session.scalars(
            select(EventEvidence.id).where(
                EventEvidence.event_id == event.id,
                EventEvidence.raw_document_id == document.id,
            )
        )
    )
    if not required or not required <= set(evidence_ids) <= available:
        raise PromotionEligibilityError("select complete field evidence from one observation")
    expected = {
        field: fact_key(name, value, unit)
        for field, name, value, unit in tender_candidate_facts(candidate)
    }
    spans = {item.field: item.span for item in candidate.evidence}
    links = observation.candidate_payload["field_links"]
    if len(links) != len(expected) or {link["field"] for link in links} != set(expected):
        raise PromotionEligibilityError("observation field coverage was changed")
    for link in links:
        fact = session.get(EventFact, UUID(link["fact_id"]))
        support = session.get(EventFactSupport, UUID(link["support_id"]))
        evidence = session.get(EventEvidence, UUID(link["evidence_id"]))
        span = spans[link["field"]]
        if (
            fact is None
            or support is None
            or evidence is None
            or fact.event_id != event.id
            or support.event_id != event.id
            or support.event_fact_id != fact.id
            or support.event_evidence_id != evidence.id
            or support.support_status != "supported"
            or fact.fact_key != expected[link["field"]]
            or fact_key(fact.name, fact.value, fact.unit) != expected[link["field"]]
            or evidence.evidence_excerpt != span.quote
            or evidence.span_hash != hashlib.sha256(span.quote.encode()).hexdigest()
        ):
            raise PromotionEligibilityError("observation field evidence was changed")
    return observation, candidate


def copy_tender_supports(
    session: Session,
    event: Event,
    observation: EventObservation,
    evidence_by_source: dict[UUID, EventEvidence],
) -> list[UUID]:
    from backend.app.services import PromotionEligibilityError

    required_evidence = []
    for position, link in enumerate(observation.candidate_payload["field_links"]):
        source_fact = session.get(EventFact, UUID(link["fact_id"]))
        source_support = session.get(EventFactSupport, UUID(link["support_id"]))
        evidence = evidence_by_source[UUID(link["evidence_id"])]
        if (
            source_fact is None
            or source_support is None
            or source_fact.event_id != observation.event_id
            or source_support.event_id != observation.event_id
            or source_support.event_fact_id != source_fact.id
            or source_support.event_evidence_id != UUID(link["evidence_id"])
            or source_support.support_status != "supported"
        ):
            raise PromotionEligibilityError("observation field support is unavailable")
        key = fact_key(source_fact.name, source_fact.value, source_fact.unit)
        if key != source_fact.fact_key:
            raise PromotionEligibilityError("stored field was changed")
        fact_id = uuid5(NAMESPACE_URL, f"dealflow-radar:event-fact:{event.id}:{key}")
        if session.get(EventFact, fact_id) is None:
            session.add(
                EventFact(
                    id=fact_id,
                    event_id=event.id,
                    fact_key=key,
                    name=source_fact.name,
                    value=source_fact.value,
                    unit=source_fact.unit,
                    position=position,
                    occurrence_count=1,
                )
            )
            session.flush()
        support_id = uuid5(
            NAMESPACE_URL, f"dealflow-radar:event-fact-support:{fact_id}:{evidence.id}"
        )
        if session.get(EventFactSupport, support_id) is None:
            session.add(
                EventFactSupport(
                    id=support_id,
                    event_id=event.id,
                    event_fact_id=fact_id,
                    event_evidence_id=evidence.id,
                    support_status="supported",
                    evidence_locator={
                        "kind": "approved_tender_field",
                        "fact_version": observation.fact_version,
                    },
                    deterministic_checks={"source_field_revalidated": True},
                    support_reasons=["platform_admin_confirmed_source_support"],
                    policy_version="tender-field-support-v1",
                )
            )
        required_evidence.append(evidence.id)
    session.flush()
    return required_evidence
