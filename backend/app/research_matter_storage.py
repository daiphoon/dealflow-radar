"""追加事项观测，维护初始资料和现有证据；不自动改已确认事实。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from sqlalchemy import select, text

from backend.app.financing_storage import authorized_research_evidence
from backend.app.models import Event, EventEvidence, EventObservation
from backend.app.research_matters import (
    LABELS,
    VERSION,
    Matter,
    compatible,
    digest,
    extract_matters,
)
from backend.app.research_subject import load_subject


def previous_matters(session, event, subject):
    evidence = list(
        session.scalars(select(EventEvidence).where(EventEvidence.event_id == event.id))
    )
    visible_ids = {
        e.raw_document_id
        for e in evidence
        if e.display_allowed and e.display_license_status == "public"
    }
    rows = list(
        session.scalars(
            select(EventObservation).where(
                EventObservation.event_id == event.id,
                EventObservation.schema_version == VERSION,
                EventObservation.raw_document_id.in_(visible_ids),
            )
        )
    )
    candidates = [Matter(**r.candidate_payload["matter"]) for r in rows]
    if event.fingerprint_version == "curated-v1":
        for e in evidence:
            data = (e.display_detail_payload or {}).get("version", {})
            if e.display_allowed and data.get("facts") == event.facts:
                scope = data.get("subject_scope", "")
                name = (
                    subject.aliases[0]
                    if "品牌" in scope and len(subject.aliases) == 1
                    else subject.legal_name
                )
                candidates += extract_matters(subject, name + event.title + "，" + event.summary)
                break
    if event.fingerprint_version in {"financing-v1", "financing-v2"}:
        from backend.app.financing_events import financing_observations

        for old in financing_observations(evidence, {e.id for e in evidence if e.display_allowed}):
            candidates += extract_matters(subject, old.excerpt)
    return candidates


def persist_matters(session, company, document, source, actor, matters, policy):
    checked = authorized_research_evidence(session, company, document, source, actor)
    if checked is None:
        return [], 0, 0
    company, document, source, actor = checked
    subject = load_subject(session, company)
    from backend.app.web_research_service import _source_quality

    source_quality = _source_quality(subject, document.canonical_url)
    if session.get_bind().dialect.name == "postgresql":
        key = int.from_bytes(bytes.fromhex(digest([str(company.id), VERSION]))[:8], signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    existing = list(
        session.scalars(
            select(Event).where(
                Event.company_id == company.id,
                Event.visibility_scope == "platform_shared",
                Event.owner_user_id.is_(None),
                Event.owner_tenant_id.is_(None),
                Event.status.notin_(["rejected", "retracted"]),
            )
        )
    )
    output, created, attached = [], 0, 0
    for matter in matters:
        matches = []
        for event in existing:
            if event.event_type != matter.category:
                continue
            if (
                document.published_on
                and event.published_on
                and abs((document.published_on - event.published_on).days) > 45
            ):
                continue
            previous = previous_matters(session, event, subject)
            if previous and any(compatible(old, matter) for old in previous):
                matches.append(event)
        event = matches[0] if len(matches) == 1 else None
        if event is not None:
            repeated = session.scalar(
                select(EventObservation).where(
                    EventObservation.event_id == event.id,
                    EventObservation.raw_document_id == document.id,
                    EventObservation.schema_version == VERSION,
                )
            )
            if repeated:
                output.append(event)
                continue
        if event is None:
            # 未知/过旧来源可维护已有事项，但不能充作近期新事件。
            observed = (
                document.observed_at.replace(tzinfo=UTC)
                if document.observed_at.tzinfo is None
                else document.observed_at
            )
            recent = document.published_at
            if recent is not None and recent.tzinfo is None:
                recent = recent.replace(tzinfo=UTC)
            occurred = matter.fields.get("date", {}).get("iso")
            if (
                recent is None
                or recent < observed - timedelta(days=policy.recent_change_window_days)
                or recent > observed + timedelta(days=1)
                or (
                    occurred
                    and date.fromisoformat(occurred)
                    < (observed - timedelta(days=policy.recent_change_window_days)).date()
                )
            ):
                continue
            if occurred and date.fromisoformat(occurred) > observed.date():
                continue
            if "generated_source_not_fact" in matter.issues:
                continue
            if len(matches) > 1:
                matter.issues.append("ambiguous_existing_matter")
            fingerprint = digest(
                [
                    str(company.id),
                    matter.scope,
                    matter.subtype,
                    matter.status,
                    matter.action,
                    document.published_on.isoformat(),
                ]
            )
            event = session.scalar(
                select(Event).where(
                    Event.company_id == company.id,
                    Event.fingerprint_version == VERSION,
                    Event.event_fingerprint == fingerprint,
                )
            )
            if event is None:
                event = Event(
                    company_id=company.id,
                    visibility_scope="platform_shared",
                    event_type=matter.category,
                    event_subtype=matter.subtype,
                    status="candidate",
                    direction="neutral",
                    materiality_score=0,
                    risk_severity="none",
                    confidence_score=Decimal("0"),
                    source_quality=source_quality,
                    title=f"{matter.subject}｜{LABELS[matter.subtype]}"[:200],
                    summary=matter.action,
                    facts=matter.facts(),
                    uncertainties=[
                        "程序发现的未确认事项；原文引用不等于事实确认。",
                        "发生日期与来源发布时间分开；未知不推算。",
                        *matter.issues,
                    ],
                    occurred_at=datetime.combine(
                        date.fromisoformat(occurred), datetime.min.time(), UTC
                    )
                    if occurred
                    else None,
                    published_at=document.published_at,
                    published_on=document.published_on,
                    observed_at=document.observed_at,
                    fingerprint_version=VERSION,
                    event_fingerprint=fingerprint,
                    publication_route="unconfirmed_lead",
                    publication_policy_version=VERSION,
                    publication_reasons=[
                        "auto_publish_disabled",
                        "human_fact_review_not_completed",
                        *matter.issues,
                    ],
                )
                session.add(event)
                session.flush()
                existing.append(event)
                created += 1
        repeated = session.scalar(
            select(EventObservation).where(
                EventObservation.event_id == event.id,
                EventObservation.raw_document_id == document.id,
                EventObservation.schema_version == VERSION,
            )
        )
        if repeated:
            output.append(event)
            continue
        kind = (
            "correction_candidate"
            if matter.status == "denied"
            else "same_facts"
            if len(matches) == 1
            else "incomplete"
            if matter.issues
            else "initial"
        )
        if len(matches) == 1 and matter.status != "denied":
            prior = previous_matters(session, event, subject)
            if any(
                any(
                    k in old.fields and old.fields[k]["value"] != v["value"]
                    for k, v in matter.fields.items()
                    if k != "date"
                )
                for old in prior
            ):
                kind = "conflicting"
        fields = matter.fields
        observed_on = fields.get("date", {}).get("iso")
        observation = EventObservation(
            event_id=event.id,
            raw_document_id=document.id,
            schema_version=VERSION,
            fact_version=digest(matter.payload()),
            observation_kind=kind,
            occurred_on=date.fromisoformat(observed_on) if observed_on else None,
            date_precision="day" if observed_on else "unknown",
            candidate_payload={"matter": matter.payload()},
            created_by=actor.id,
        )
        from backend.app.research_matters import FIELD_LABELS, STATUS_LABELS

        detail = {
            "schema_version": VERSION,
            "matter_observation": {
                "kind": kind,
                "fact_version": observation.fact_version,
                "category": matter.category,
                "subtype": matter.subtype,
                "label": LABELS[matter.subtype],
                "status": matter.status,
                "status_label": STATUS_LABELS[matter.status],
                "subject": matter.subject,
                "scope": matter.scope,
                "field_labels": FIELD_LABELS,
                "fields": fields,
                "issues": matter.issues,
                "excerpt": matter.action,
                "source_url": document.canonical_url,
                "source_title": document.title,
                "observed_at": document.observed_at.isoformat(),
                "confirmed": False,
            },
        }
        session.add(
            EventEvidence(
                event_id=event.id,
                raw_document_id=document.id,
                visibility_scope="platform_shared",
                evidence_excerpt=matter.action,
                span_hash=digest(matter.action),
                support_type="supports",
                display_source_name=urlsplit(document.canonical_url).hostname,
                display_source_quality=source_quality,
                display_title=document.title,
                display_canonical_url=document.canonical_url,
                display_published_at=document.published_at,
                display_published_on=document.published_on,
                display_observed_at=document.observed_at,
                display_url_health_status="healthy",
                display_url_http_status=document.payload.get("_source_verification", {}).get(
                    "http_status"
                ),
                display_final_url=document.canonical_url,
                display_license_status="public",
                display_allowed=True,
                display_detail_payload=detail,
            )
        )
        session.flush()
        session.add(observation)
        session.flush()
        if event.fingerprint_version == VERSION:
            from backend.app.fact_support import materialize_event_fact_ledger

            materialize_event_fact_ledger(session, event)
        attached += int(len(matches) == 1)
        output.append(event)
    return output, created, attached


def visible_observations(evidence, visible_ids):
    result = []
    for row in evidence:
        if row.id not in visible_ids or not row.display_allowed:
            continue
        payload = row.display_detail_payload or {}
        if payload.get("schema_version") == VERSION and isinstance(
            payload.get("matter_observation"), dict
        ):
            result.append({**payload["matter_observation"], "evidence_id": row.id})
    return result
