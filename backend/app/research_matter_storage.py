"""追加事项观测，维护初始资料和现有证据；不自动改已确认事实。"""

from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import urlsplit

from sqlalchemy import select, text

from backend.app.evidence_integrity import EXCERPT_HASH_VERSION, hash_excerpt_bytes
from backend.app.financing_storage import authorized_research_evidence
from backend.app.matter_comparison import compare_matters
from backend.app.matter_dates import occurrence
from backend.app.matter_dispositions import record
from backend.app.matter_retention import RETENTION_VERSION, source_channel, temporal_status
from backend.app.matter_validation import (
    VALIDATION_VERSION,
    action_supported,
    actor_supported,
    validate_field,
)
from backend.app.models import Event, EventEvidence, EventObservation
from backend.app.research_matters import (
    EXTRACTION_VERSION,
    LABELS,
    VERSION,
    Matter,
    digest,
    names_in_document,
    subject_mentions,
)
from backend.app.research_subject import load_subject


def previous_matters(session, event, subject, *, evidence=None, observations=None):
    if evidence is None:
        evidence = list(
            session.scalars(select(EventEvidence).where(EventEvidence.event_id == event.id))
        )
    visible_ids = {
        e.raw_document_id
        for e in evidence
        if e.display_allowed and e.display_license_status == "public"
    }
    if observations is None:
        observations = list(
            session.scalars(
                select(EventObservation).where(
                    EventObservation.event_id == event.id,
                    EventObservation.schema_version == VERSION,
                )
            )
        )
    rows = [
        r for r in observations if r.schema_version == VERSION and r.raw_document_id in visible_ids
    ]
    candidates = [Matter(**r.candidate_payload["matter"]) for r in rows]
    if event.fingerprint_version == "curated-v1":
        for e in evidence:
            data = (e.display_detail_payload or {}).get("version", {})
            if e.display_allowed and data.get("facts") == event.facts:
                from backend.app.matter_baseline import curated_matters

                candidates += curated_matters(event, data, subject)
                break
    if event.fingerprint_version in {"financing-v1", "financing-v2"}:
        from backend.app.financing_events import financing_observations

        for old in financing_observations(evidence, {e.id for e in evidence if e.display_allowed}):
            from backend.app.matter_baseline import financing_matter

            candidates.append(financing_matter(old))
    return candidates


def persist_matters(
    session, company, document, source, actor, matters, policy, *, processing_version=None
):
    processing_version = processing_version or f"{EXTRACTION_VERSION}/{VALIDATION_VERSION}"
    checked = authorized_research_evidence(session, company, document, source, actor)
    if checked is None:
        record(document, "admission", "rejected", "research_evidence_not_authorized")
        return [], 0, 0
    company, document, source, actor = checked
    subject = load_subject(session, company)
    from backend.app.web_research_service import _source_quality

    channel, source_quality = source_channel(
        document, _source_quality(subject, document.canonical_url)
    )
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
    # 同一事务预加载，避免每个候选重新查询所有事件的证据与观测。
    evidence_by_event, observations_by_event = {}, {}
    event_ids = [e.id for e in existing]
    for row in session.scalars(select(EventEvidence).where(EventEvidence.event_id.in_(event_ids))):
        evidence_by_event.setdefault(row.event_id, []).append(row)
    for row in session.scalars(
        select(EventObservation).where(EventObservation.event_id.in_(event_ids))
    ):
        observations_by_event.setdefault(row.event_id, []).append(row)
    previous = {
        e.id: previous_matters(
            session,
            e,
            subject,
            evidence=evidence_by_event.get(e.id, []),
            observations=observations_by_event.get(e.id, []),
        )
        for e in existing
    }
    output, created, attached = [], 0, 0
    for matter in matters:
        body = str(document.payload.get("excerpt") or "")
        mentions = {
            n
            for _, n in subject_mentions(
                names_in_document(subject, body), subject.legal_name, matter.action
            )
        }
        if (
            matter.action not in body
            or matter.subject not in mentions
            or not actor_supported(matter.subject, matter.action, matter.subtype)
            or not action_supported(matter.action, matter.subtype)
        ):
            record(
                document,
                "candidate_validation",
                "rejected",
                "subject_action_or_source_bounds_invalid",
            )
            continue
        valid_fields = {}
        for key, item in matter.fields.items():
            supported, reason = validate_field(
                item.get("role"),
                item.get("value", ""),
                item.get("quote", ""),
                matter.action,
                matter.subtype,
            )
            if supported:
                valid_fields[key] = item
            else:
                matter.issues.append(f"rejected_field:{key}")
                record(
                    document,
                    "field_validation",
                    "rejected",
                    reason,
                    field=key,
                    proposed_value=item.get("value"),
                )
        matter.fields = valid_fields
        retention = temporal_status(matter, document, policy.recent_change_window_days)
        observation_key = digest(matter.payload())
        # 先查历史，不因撤证、撤回或算法升级恢复已撤销的材料。
        prior_rows = list(
            session.scalars(
                select(EventObservation).where(
                    EventObservation.raw_document_id == document.id,
                    EventObservation.schema_version == VERSION,
                )
            )
        )
        prior_evidence = list(
            session.scalars(
                select(EventEvidence).where(
                    EventEvidence.raw_document_id == document.id,
                    EventEvidence.evidence_excerpt == matter.action,
                )
            )
        )
        if any(not e.display_allowed for e in prior_evidence):
            record(
                document,
                "retention",
                "suppressed",
                "evidence_previously_withdrawn",
                observation_key=observation_key,
            )
            continue
        repeated = next(
            (
                r
                for r in prior_rows
                if r.observation_key == observation_key
                and r.processing_version == processing_version
            ),
            None,
        )
        if repeated:
            prior_event = session.get(Event, repeated.event_id)
            if prior_event and prior_event.status not in {"retracted", "rejected"}:
                output.append(prior_event)
            record(
                document,
                "observation",
                "unchanged",
                "same_observation_and_processing_version",
                observation_key=observation_key,
            )
            continue
        if any(
            session.get(Event, e.event_id).status in {"retracted", "rejected"}
            for e in prior_evidence
        ):
            record(
                document,
                "retention",
                "suppressed",
                "event_previously_retracted",
                observation_key=observation_key,
            )
            continue
        matches, decisions, relations = [], {}, []
        for event in existing:
            if event.event_type != matter.category:
                continue
            comparisons = [compare_matters(old, matter) for old in previous.get(event.id, [])]
            for compared in comparisons:
                if compared.decision == "related_stage":
                    relations.append(
                        {
                            "event_id": str(event.id),
                            "type": "related_stage",
                            "reason": compared.reason,
                        }
                    )
            matching = [d for d in comparisons if d.same_matter]
            if matching:
                matches.append(event)
                decisions[event.id] = next(
                    (d for d in matching if d.decision == "field_conflict"), matching[0]
                )
        event = matches[0] if len(matches) == 1 else None
        decision = decisions.get(event.id) if event else None
        if event is None:
            # 有具体主体、动作和原文即保留线索；近期资格单独表达。
            occurred = occurrence(matter.fields).get("iso")
            if len(matches) > 1:
                matter.issues.append("ambiguous_existing_matter")
            fingerprint = digest(
                [
                    str(company.id),
                    matter.scope,
                    matter.subtype,
                    matter.status,
                    matter.action,
                    occurrence(matter.fields).get("iso"),
                    str(document.id),
                ]
            )
            event = session.scalar(
                select(Event).where(
                    Event.company_id == company.id,
                    Event.fingerprint_version == VERSION,
                    Event.event_fingerprint == fingerprint,
                )
            )
            if event is not None and event.status in {"retracted", "rejected"}:
                record(
                    document,
                    "retention",
                    "suppressed",
                    "matching_event_retracted",
                    observation_key=observation_key,
                )
                continue
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
                        "source_support_not_fact_confirmation",
                        retention,
                        *matter.issues,
                    ],
                )
                session.add(event)
                session.flush()
                existing.append(event)
                created += 1
        kind = (
            "correction_candidate"
            if matter.status == "denied"
            else "conflicting"
            if decision and decision.decision == "field_conflict"
            else "same_facts"
            if decision and decision.same_matter
            else "incomplete"
            if matter.issues
            else "initial"
        )
        fields = matter.fields
        observed_on = occurrence(fields).get("iso")
        observation = EventObservation(
            event_id=event.id,
            raw_document_id=document.id,
            schema_version=VERSION,
            fact_version=digest(matter.payload()),
            observation_key=observation_key,
            processing_version=processing_version,
            observation_kind=kind,
            occurred_on=date.fromisoformat(observed_on) if observed_on else None,
            date_precision=occurrence(fields).get("precision", "day" if observed_on else "unknown")
            if occurrence(fields).get("precision") in {"day", "month", "year"}
            else "day"
            if observed_on
            else "unknown",
            candidate_payload={
                "matter": matter.payload(),
                "merge_decision": decision.__dict__
                if decision
                else {"decision": "ambiguous" if len(matches) > 1 else "new_matter"},
                "processing_version": processing_version,
                "retention": retention,
                "baseline_kind": "curated"
                if event.fingerprint_version == "curated-v1"
                else "legacy_financing"
                if event.fingerprint_version in {"financing-v1", "financing-v2"}
                else "research",
                "relations": relations,
                "dispositions": list(getattr(document, "_matter_dispositions", [])),
            },
            created_by=actor.id,
        )
        from backend.app.research_matters import FIELD_LABELS, STATUS_LABELS

        detail = {
            "schema_version": VERSION,
            "excerpt_hash_version": EXCERPT_HASH_VERSION,
            "matter_observation": {
                "kind": kind,
                "processing_version": processing_version,
                "temporal_status": retention,
                "retention_version": RETENTION_VERSION,
                "recent_window_days": policy.recent_change_window_days,
                "relations": relations,
                "source_channel": channel,
                "source_published_on": document.published_on.isoformat()
                if document.published_on
                else None,
                "source_quality": source_quality,
                "information_status": "source_supported_unconfirmed",
                "merge_decision": decision.decision if decision else "new_matter",
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
        new_evidence = EventEvidence(
            event_id=event.id,
            raw_document_id=document.id,
            visibility_scope="platform_shared",
            evidence_excerpt=matter.action,
            span_hash=hash_excerpt_bytes(matter.action),
            support_type="contradicts"
            if kind in {"conflicting", "correction_candidate"}
            else "supports",
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
        same_span = session.scalar(
            select(EventEvidence).where(
                EventEvidence.event_id == event.id,
                EventEvidence.raw_document_id == document.id,
                EventEvidence.span_hash == new_evidence.span_hash,
            )
        )
        if same_span is not None:
            # 一份原始引文一条证据；处理版本和候选历史追加保留，当前投影可重建。
            history = list((same_span.display_detail_payload or {}).get("matter_history", []))
            if not history and (same_span.display_detail_payload or {}).get("matter_observation"):
                history.append(same_span.display_detail_payload["matter_observation"])
            history.append(detail["matter_observation"])
            same_span.display_detail_payload = {**detail, "matter_history": history}
            same_span.support_type = new_evidence.support_type
        else:
            session.add(new_evidence)
        session.flush()
        session.add(observation)
        session.flush()
        previous.setdefault(event.id, []).append(matter)
        if event.fingerprint_version == VERSION:
            from backend.app.fact_support import materialize_event_fact_ledger

            materialize_event_fact_ledger(session, event)
        record(
            document,
            "comparison",
            "retained",
            decision.reason if decision else "new_or_unresolved_identity",
            observation_key=observation_key,
            merge_decision=decision.decision if decision else "new_matter",
            event_id=str(event.id),
            temporal_status=retention,
            source_channel=channel,
        )
        record(
            document,
            "display",
            "unconfirmed",
            "source_support_not_fact_confirmation",
            observation_key=observation_key,
            advances_freshness=False,
        )
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
            from backend.app.evidence_integrity import excerpt_hash_status

            if excerpt_hash_status(row) not in {"exact", "legacy_matter_json_string"}:
                continue
            item = dict(payload["matter_observation"])
            # 正文支持逐字段显示；不能把未通过校验的观测字段泄回详情卡片。
            valid = {}
            rejected = []
            for key, value in item.get("fields", {}).items():
                ok, reason = validate_field(
                    value.get("role"),
                    value.get("value", ""),
                    value.get("quote", ""),
                    item.get("excerpt", ""),
                    item.get("subtype"),
                )
                if ok:
                    valid[key] = value
                else:
                    rejected.append(f"rejected_field:{key}:{reason}")
            item["fields"] = valid
            item["issues"] = list(dict.fromkeys([*item.get("issues", []), *rejected]))
            result.append({**item, "evidence_id": row.id})
    return result
