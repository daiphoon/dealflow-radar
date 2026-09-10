"""中标当前投影及可见观测；共享路径只读取已批准的展示快照。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.change_detection import MATERIAL_CHANGE_MIN_SCORE
from backend.app.fact_support import fact_key
from backend.app.models import Event, EventEvidence, EventObservation
from backend.app.schemas import TenderObservationOut
from backend.app.tender_events import TenderCandidate, tender_candidate_facts

SNAPSHOT_SCHEMA = "tender-shared-snapshot-v1"


def is_tender_event(event: Event) -> bool:
    return event.fingerprint_version == "tender-v1"


def candidate_from_observation(observation: EventObservation) -> TenderCandidate:
    payload = dict(observation.candidate_payload["candidate"])
    for key in ("business_key", "fact_version", "date_precision"):
        payload.pop(key, None)
    return TenderCandidate.model_validate_json(json.dumps(payload))


def candidate_facts(candidate: TenderCandidate) -> list[dict[str, str | None]]:
    return [
        {"name": name, "value": value, "unit": unit}
        for _, name, value, unit in tender_candidate_facts(candidate)
    ]


def same_facts(left: list[dict], right: list[dict]) -> bool:
    def keys(facts):
        return {fact_key(item["name"], item["value"], item.get("unit")) for item in facts}

    return keys(left) == keys(right)


def usable_shared_evidence(evidence: EventEvidence) -> bool:
    return (
        evidence.visibility_scope == "platform_shared"
        and evidence.owner_user_id is None
        and evidence.owner_tenant_id is None
        and evidence.display_allowed
        and evidence.display_license_status in {"public", "permission_confirmed"}
        and evidence.display_url_health_status in {"healthy", "unchecked"}
        and evidence.span_hash == hashlib.sha256(evidence.evidence_excerpt.encode()).hexdigest()
    )


def tender_observations(
    session: Session, event: Event, visible_evidence_ids: set[UUID] | None = None
) -> list[TenderObservationOut]:
    if not is_tender_event(event):
        return []
    evidence = list(
        session.scalars(select(EventEvidence).where(EventEvidence.event_id == event.id))
    )
    if event.visibility_scope == "platform_shared":
        allowed = {item.id for item in evidence if usable_shared_evidence(item)}
        if visible_evidence_ids is not None:
            allowed &= visible_evidence_ids
        result, seen = [], set()
        for item in evidence:
            metadata = item.display_detail_payload or {}
            if metadata.get("schema_version") != SNAPSHOT_SCHEMA:
                continue
            try:
                snapshot = TenderObservationOut.model_validate(metadata["observation"])
                # 共享快照不包含私有观测 ID，也不查询私有原文档或所有者。
                snapshot.observation_id = None
                snapshot.confirmed = True
                snapshot.evidence_available = (
                    bool(snapshot.evidence_ids) and set(snapshot.evidence_ids) <= allowed
                )
                snapshot.is_current = same_facts(snapshot.facts, event.facts)
            except (ValidationError, KeyError, TypeError):
                continue
            key = tuple(sorted(str(value) for value in snapshot.evidence_ids))
            if key not in seen:
                seen.add(key)
                result.append(snapshot)
        result.sort(key=lambda row: row.reviewed_at or row.observed_at)
        # 同事实转载沿用本轮最早的已核实版本；更正后恢复旧值也属于新的核实轮次。
        last_different = max(
            (index for index, row in enumerate(result) if not row.is_current), default=-1
        )
        for index, row in enumerate(result):
            row.is_current = row.is_current and index > last_different
        return result
    if event.visibility_scope not in {"personal_private", "organization_private"}:
        return []
    allowed = visible_evidence_ids if visible_evidence_ids is not None else {x.id for x in evidence}
    result = []
    for observation in session.scalars(
        select(EventObservation)
        .where(EventObservation.event_id == event.id)
        .order_by(EventObservation.created_at, EventObservation.id)
    ):
        try:
            candidate = candidate_from_observation(observation)
            facts = candidate_facts(candidate)
            ids = [
                UUID(link["evidence_id"]) for link in observation.candidate_payload["field_links"]
            ]
            result.append(
                TenderObservationOut(
                    observation_id=observation.id,
                    fact_version=candidate.fact_version,
                    observation_kind=observation.observation_kind,
                    occurred_on=observation.occurred_on,
                    date_precision=observation.date_precision,
                    observed_at=candidate.document.observed_at,
                    facts=facts,
                    evidence_ids=ids,
                    is_current=same_facts(facts, event.facts),
                    evidence_available=bool(ids) and set(ids) <= allowed,
                    can_publish=not candidate.issues
                    and candidate.business_key is not None
                    and observation.observation_kind != "conflicting",
                )
            )
        except (ValidationError, KeyError, TypeError, ValueError):
            continue
    return result


def current_tender_observation(session: Session, event: Event) -> TenderObservationOut | None:
    return next(
        (
            item
            for item in tender_observations(session, event)
            if item.is_current and item.evidence_available
        ),
        None,
    )


def event_display_kind(session: Session, event: Event) -> str:
    if event.status != "published":
        return "unconfirmed"
    if is_tender_event(event):
        if (
            event.publication_route != "human_promoted"
            or current_tender_observation(session, event) is None
        ):
            return "unconfirmed"
        return (
            "confirmed_change"
            if event.materiality_score >= MATERIAL_CHANGE_MIN_SCORE
            else "baseline"
        )
    return "confirmed_change" if event.publication_route == "deterministic_change" else "baseline"


def tender_snapshot(
    candidate: TenderCandidate, evidence_ids: list[UUID], *, reviewed_at: datetime
) -> dict:
    observation = TenderObservationOut(
        fact_version=candidate.fact_version,
        observation_kind="correction_candidate" if candidate.is_correction else "initial",
        occurred_on=candidate.occurred_on,
        date_precision=candidate.date_precision,
        observed_at=candidate.document.observed_at,
        reviewed_at=reviewed_at.replace(tzinfo=UTC) if reviewed_at.tzinfo is None else reviewed_at,
        facts=candidate_facts(candidate),
        evidence_ids=evidence_ids,
        is_current=True,
        confirmed=True,
    )
    return {"schema_version": SNAPSHOT_SCHEMA, "observation": observation.model_dump(mode="json")}
