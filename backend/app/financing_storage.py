"""受限公开研究的融资观测；新增证据不覆盖已确认事实。"""

from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

from sqlalchemy import select, text

from backend.app.financing_events import (
    EXTRACTOR_VERSION,
    SCHEMA_VERSION,
    FinancingCandidate,
    amount_value,
    digest,
    extract_financing,
    round_value,
    same_matter,
)
from backend.app.models import (
    Company,
    EntityMention,
    Event,
    EventEvidence,
    EventObservation,
    RawDocument,
    Source,
    User,
    utc_now,
)
from backend.app.research_subject import load_subject, normalize


def _curated_candidate(event, company, evidence):
    for row in evidence:
        payload = row.display_detail_payload or {}
        version = payload.get("version", {})
        if (
            payload.get("schema_version") != "curated-record-v1"
            or not row.display_allowed
            or version.get("facts") != event.facts
        ):
            continue
        scope_text = version.get("subject_scope", "")
        if "品牌" in scope_text and len(company.aliases) == 1:
            scope = f"brand:{normalize(company.aliases[0])}"
            name = company.aliases[0]
        elif "法人" in scope_text and not any(s in scope_text for s in ("未确认", "集团", "品牌")):
            scope, name = f"legal_entity:{company.credit_code}", company.legal_name
        else:
            return None
        day = version.get("date_text")
        try:
            date.fromisoformat(day)
        except (TypeError, ValueError):
            return None
        body = f"{event.title}。{event.summary}"
        return FinancingCandidate(
            name, scope, round_value(body), amount_value(event.summary), (), day, None, "", False
        )
    return None


def persist_financing_document(session, supplied_company, supplied_document, supplied_source, user):
    from backend.app.services import AccessDeniedError, user_has_role
    from backend.app.web_research_service import _source_quality

    if user is None:
        raise AccessDeniedError("financing observations require an authenticated worker")
    actor = session.get(User, user.id)
    if (
        actor is None
        or actor.status != "active"
        or not user_has_role(session, actor.id, "platform_admin")
    ):
        raise AccessDeniedError("active platform admin required")
    if session.get_bind().dialect.name == "postgresql":
        context = session.execute(
            text(
                "SELECT current_setting('app.current_user_id', true), "
                "current_setting('app.current_tenant_id', true)"
            )
        ).one()
        if tuple(context) != (str(actor.id), str(actor.tenant_id)):
            raise AccessDeniedError("worker context differs from actor")
    company = session.get(Company, supplied_company.id)
    document = session.get(RawDocument, supplied_document.id)
    source = session.get(Source, supplied_source.id)
    if (
        not company
        or company.visibility_scope != "public"
        or company.tenant_id is not None
        or company.identity_status != "verified"
        or not company.credit_code
        or not document
        or document.visibility_scope != "system_restricted"
        or document.owner_user_id is not None
        or document.owner_tenant_id is not None
        or document.license_status != "public"
        or not source
        or source.code != "bounded_public_web"
        or source.id != document.source_id
        or source.license_status != "public"
        or document.payload.get("_source_verification", {}).get("status") != "healthy"
    ):
        raise AccessDeniedError("unavailable bounded public evidence")
    mentions = list(
        session.scalars(
            select(EntityMention).where(
                EntityMention.raw_document_id == document.id,
                EntityMention.resolution_status == "verified",
            )
        )
    )
    if not mentions or any(m.candidate_company_id != company.id for m in mentions):
        return None, False
    subject = load_subject(session, company)
    source_quality = _source_quality(company, document.canonical_url)
    candidate = extract_financing(
        subject, str(document.payload.get("excerpt") or ""), document.published_on
    )
    if candidate is None:
        return None, False
    if session.get_bind().dialect.name == "postgresql":
        key = int.from_bytes(bytes.fromhex(digest([str(company.id), "financing"]))[:8], signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    previous = session.scalar(
        select(EventObservation)
        .join(Event)
        .where(
            EventObservation.raw_document_id == document.id,
            EventObservation.schema_version == SCHEMA_VERSION,
            Event.company_id == company.id,
            Event.visibility_scope == "platform_shared",
        )
    )
    if previous:
        return session.get(Event, previous.event_id), False
    events = list(
        session.scalars(
            select(Event).where(
                Event.company_id == company.id,
                Event.visibility_scope == "platform_shared",
                Event.owner_user_id.is_(None),
                Event.owner_tenant_id.is_(None),
                Event.event_type == "financing_cap_table",
                Event.status.notin_(["rejected", "retracted"]),
            )
        )
    )
    matches = []
    for event in events:
        evidence = list(
            session.scalars(select(EventEvidence).where(EventEvidence.event_id == event.id))
        )
        old = (
            _curated_candidate(event, subject, evidence)
            if event.fingerprint_version == "curated-v1"
            else None
        )
        if old is None and event.fingerprint_version == "financing-v1":
            observation = session.scalar(
                select(EventObservation)
                .where(
                    EventObservation.event_id == event.id,
                    EventObservation.schema_version == SCHEMA_VERSION,
                )
                .order_by(EventObservation.created_at)
                .limit(1)
            )
            if observation:
                payload = dict(observation.candidate_payload["candidate"])
                payload["investors"] = tuple(payload["investors"])
                payload["issues"] = tuple(payload["issues"])
                old = FinancingCandidate(**payload)
        if old and same_matter(old, candidate):
            matches.append((event, old))
    created = len(matches) != 1
    if created:
        if len(matches) > 1:
            candidate = replace(candidate, issues=(*candidate.issues, "ambiguous_existing_matter"))
        fingerprint = digest(
            [
                str(company.id),
                candidate.subject_scope,
                candidate.round,
                candidate.disclosed_on,
                str(document.id) if candidate.issues else None,
            ]
        )
        event = Event(
            company_id=company.id,
            visibility_scope="platform_shared",
            event_type="financing_cap_table",
            event_subtype="financing_disclosure",
            status="candidate",
            direction="neutral",
            materiality_score=0,
            risk_severity="none",
            confidence_score=Decimal("0"),
            source_quality=source_quality,
            title=f"{candidate.subject_name}｜{candidate.round or '轮次未披露'}融资线索"[:200],
            summary=candidate.evidence,
            facts=candidate.facts(),
            uncertainties=[
                "程序发现，尚未核实；金额保持公开措辞，不代表法人实收、营收或估值。",
                "未披露字段保持未知；重要性、风险和置信度尚未评价。",
            ],
            occurred_at=None,
            published_at=document.published_at,
            published_on=document.published_on,
            observed_at=document.observed_at,
            fingerprint_version="financing-v1",
            event_fingerprint=fingerprint,
            publication_route="unconfirmed_lead",
            publication_policy_version=SCHEMA_VERSION,
            publication_reasons=[
                "auto_publish_disabled",
                "human_fact_review_not_completed",
                "assessment_not_performed",
                *candidate.issues,
            ],
        )
        session.add(event)
        session.flush()
        kind = "incomplete" if candidate.issues else "initial"
    else:
        event, old = matches[0]
        # 已有资料未知的字段允许补充，实质差异独立保存，不改当前事实。
        differences = any(
            old.fields()[key] != candidate.fields()[key]
            for key in ("amount_text", "investors", "occurred_on")
        )
        kind = (
            "correction_candidate"
            if candidate.is_correction
            else ("conflicting" if differences else "same_facts")
        )
    verification = document.payload["_source_verification"]
    observation = EventObservation(
        event_id=event.id,
        raw_document_id=document.id,
        schema_version=SCHEMA_VERSION,
        fact_version=digest(candidate.fields()),
        observation_kind=kind,
        occurred_on=date.fromisoformat(candidate.occurred_on) if candidate.occurred_on else None,
        date_precision="day" if candidate.occurred_on else "unknown",
        candidate_payload={"candidate": asdict(candidate), "extractor_version": EXTRACTOR_VERSION},
        created_by=actor.id,
    )
    observed_at = utc_now()
    observation.created_at = observed_at
    evidence = EventEvidence(
        event_id=event.id,
        raw_document_id=document.id,
        visibility_scope="platform_shared",
        evidence_excerpt=candidate.evidence,
        span_hash=digest(candidate.evidence),
        support_type="context",
        display_source_name=urlsplit(document.canonical_url).hostname or source.name,
        display_source_quality=source_quality,
        display_title=document.title,
        display_canonical_url=document.canonical_url,
        display_final_url=document.canonical_url,
        display_published_at=document.published_at,
        display_published_on=document.published_on,
        display_observed_at=document.observed_at,
        display_url_health_status="healthy",
        display_url_http_status=verification.get("http_status"),
        display_license_status="public",
        display_allowed=True,
        display_detail_payload={
            "schema_version": SCHEMA_VERSION,
            "extractor_version": EXTRACTOR_VERSION,
            "financing_observation": {
                "kind": kind,
                "fact_version": observation.fact_version,
                "fields": candidate.fields(),
                "issues": list(candidate.issues),
                "observed_at": observed_at.isoformat(),
                "source_url": document.canonical_url,
                "source_title": document.title,
                "excerpt": candidate.evidence,
                "confirmed": False,
            },
        },
    )
    session.add(evidence)
    session.flush()
    session.add(observation)
    session.flush()
    return event, created
