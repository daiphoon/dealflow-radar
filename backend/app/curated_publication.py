"""人工整理观测的明确版本选择及共享展示投影。"""

from __future__ import annotations

from datetime import UTC
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.curated_workbook import digest
from backend.app.models import (
    Event,
    EventEvidence,
    EventObservation,
    EventSharingDecision,
    RawDocument,
    ResearchImport,
)
from backend.app.schemas import CuratedVersionOut

CURATED_SCHEMA = "curated-record-v1"


def has_pending_curated_record(session: Session, event: Event) -> bool:
    latest = session.scalar(
        select(EventObservation)
        .where(
            EventObservation.event_id == event.id,
            EventObservation.schema_version == CURATED_SCHEMA,
        )
        .order_by(EventObservation.created_at.desc())
        .limit(1)
    )
    return (
        latest is None
        or session.scalar(
            select(EventSharingDecision.id)
            .where(
                EventSharingDecision.source_observation_id == latest.id,
                EventSharingDecision.action == "promote",
            )
            .limit(1)
        )
        is None
    )


def select_curated_observation(
    session: Session, event: Event, observation_id: UUID | None, evidence_ids: list[UUID]
) -> EventObservation:
    from backend.app.services import PromotionEligibilityError

    observation = session.get(EventObservation, observation_id) if observation_id else None
    if (
        observation is None
        or observation.event_id != event.id
        or observation.schema_version != CURATED_SCHEMA
        or observation.candidate_payload.get("confirmed") is not True
    ):
        raise PromotionEligibilityError("select a confirmed curated observation for this event")
    payload = observation.candidate_payload
    required = set(payload.get("evidence_ids", []))
    if not required or required != {str(i) for i in evidence_ids}:
        raise PromotionEligibilityError(
            "curated observation requires its complete evidence selection"
        )
    if digest(payload["event_fields"]) != observation.fact_version:
        raise PromotionEligibilityError("curated observation fields changed")
    for evidence_id in evidence_ids:
        evidence = session.get(EventEvidence, evidence_id)
        document = session.get(RawDocument, evidence.raw_document_id) if evidence else None
        imported = session.get(ResearchImport, document.research_import_id) if document else None
        if (
            evidence is None
            or evidence.event_id != event.id
            or document is None
            or imported is None
            or imported.parser_version != "curated-xlsx-v1"
            or imported.license_status != "public"
            or document.payload.get("curated_record", {}).get("fact_version")
            != observation.fact_version
            or digest(document.payload.get("curated_record")) != document.content_hash
            or document.payload.get("_curation", {}).get("confirmed_by")
            != str(imported.imported_by)
        ):
            raise PromotionEligibilityError("curated record lineage is missing or changed")
    return observation


def project_curated_observation(
    event: Event, observation: EventObservation, evidence: list[EventEvidence]
) -> None:
    from backend.app.curated_workbook import exact_day

    payload = observation.candidate_payload
    for key, value in payload["event_fields"].items():
        setattr(event, key, exact_day(value or "") if key == "published_on" else value)
    event.observed_at = observation.created_at
    event.source_quality = evidence[0].display_source_quality or "E"
    event.publication_reasons = [
        "platform_admin_approved",
        "curator_record_not_webpage",
        "source_not_rechecked",
        "assessment_not_performed",
    ]
    metadata = {
        "schema_version": CURATED_SCHEMA,
        "version": {
            **payload["metadata"],
            "fact_version": observation.fact_version,
            "record_version": digest(str(observation.id)),
            "observed_at": observation.created_at.isoformat(),
            "reviewed_at": payload["reviewed_at"],
            "title": event.title,
            "summary": event.summary,
            "facts": event.facts,
            "sources": payload["sources"],
            "evidence_ids": [str(item.id) for item in evidence],
            "confirmed": True,
        },
    }
    # 不把文件名、工作表行号、私有观测/导入 ID 或审核者身份复制到共享层。
    for item in evidence:
        item.display_detail_payload = metadata


def curated_versions(
    session: Session, event: Event, visible_ids: set[UUID]
) -> list[CuratedVersionOut]:
    if event.fingerprint_version != "curated-v1":
        return []
    result = {}
    if event.visibility_scope == "platform_shared":
        rows = list(
            session.scalars(select(EventEvidence).where(EventEvidence.event_id == event.id))
        )
        for evidence in rows:
            payload = evidence.display_detail_payload or {}
            if payload.get("schema_version") != CURATED_SCHEMA:
                continue
            try:
                version = CuratedVersionOut.model_validate(payload["version"])
            except (ValidationError, KeyError):
                continue
            version.evidence_available = (
                bool(version.evidence_ids) and set(version.evidence_ids) <= visible_ids
            )
            version.is_current = (
                version.facts == event.facts
                and version.title == event.title
                and version.summary == event.summary
                and version.observed_at.replace(tzinfo=UTC) == event.observed_at.replace(tzinfo=UTC)
            )
            if not version.evidence_available:
                version.facts, version.sources = [], []
                version.summary = "该版本证据不可用，请查看已保留的审核记录。"
            result[version.record_version] = version
    else:
        for observation in session.scalars(
            select(EventObservation).where(
                EventObservation.event_id == event.id,
                EventObservation.schema_version == CURATED_SCHEMA,
            )
        ):
            payload = observation.candidate_payload
            fields = payload["event_fields"]
            version = CuratedVersionOut.model_validate(
                {
                    **payload["metadata"],
                    "fact_version": observation.fact_version,
                    "record_version": digest(str(observation.id)),
                    "observed_at": observation.created_at.isoformat(),
                    "reviewed_at": payload["reviewed_at"],
                    "title": fields["title"],
                    "summary": fields["summary"],
                    "facts": fields["facts"],
                    "sources": payload["sources"],
                    "evidence_ids": payload["evidence_ids"],
                    "confirmed": False,
                    "is_current": fields["facts"] == event.facts
                    and observation.created_at.replace(tzinfo=UTC)
                    == event.observed_at.replace(tzinfo=UTC),
                }
            )
            result[version.record_version] = version
    return sorted(result.values(), key=lambda item: item.observed_at, reverse=True)
