"""平台负责人确认资料的离线准入与原子导入。"""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.curated_workbook import (
    PARSER_VERSION,
    CuratedCompany,
    CuratedRecord,
    LoadedCuratedWorkbook,
    digest,
    exact_day,
)
from backend.app.fact_support import materialize_event_fact_ledger
from backend.app.models import (
    Company,
    CompanyAlias,
    CompanySnapshot,
    EntityMention,
    Event,
    EventEvidence,
    EventObservation,
    PersonalCompanyRequest,
    RawDocument,
    ResearchImport,
    Source,
    UsageLedger,
    User,
    utc_now,
)
from backend.app.services import (
    AccessDeniedError,
    ImportConflictError,
    _is_platform_shared_company,
    _normalized_identity_text,
    _refresh_company_snapshot,
    promote_private_event,
    user_has_role,
)

CURATED_SCHEMA = "curated-record-v1"
FINGERPRINT_VERSION = "curated-v1"


def _require_curator(session: Session, user: User) -> None:
    persisted = session.get(User, user.id, populate_existing=True)
    if (
        persisted is None
        or persisted.status != "active"
        or not user_has_role(session, user.id, "platform_admin")
    ):
        raise AccessDeniedError("active platform administrator required for curated import")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _existing_import(session: Session, user: User, loaded: LoadedCuratedWorkbook):
    return session.scalar(
        select(ResearchImport).where(
            ResearchImport.tenant_id == user.tenant_id,
            ResearchImport.file_hash == loaded.file_hash,
            ResearchImport.parser_version == PARSER_VERSION,
            ResearchImport.selection_key == loaded.selection_key,
        )
    )


def _fingerprint(
    loaded: LoadedCuratedWorkbook, company: CuratedCompany, record: CuratedRecord
) -> str:
    return digest([loaded.dataset_key, company.credit_code, record.sheet, record.key])


def _event(session: Session, company_id: UUID, fingerprint: str, user_id: UUID | None):
    return session.scalar(
        select(Event).where(
            Event.company_id == company_id,
            Event.fingerprint_version == FINGERPRINT_VERSION,
            Event.event_fingerprint == fingerprint,
            Event.visibility_scope == ("personal_private" if user_id else "platform_shared"),
            Event.owner_user_id == user_id,
            Event.owner_tenant_id.is_(None),
        )
    )


def _field_version(event: Event) -> str:
    return digest(
        {
            key: (value.isoformat() if isinstance(value, (date, datetime)) else value)
            for key in (
                "title",
                "summary",
                "facts",
                "uncertainties",
                "event_type",
                "event_subtype",
                "occurred_at",
                "published_at",
                "published_on",
            )
            for value in [getattr(event, key)]
        }
    )


def preview_curated_import(
    session: Session,
    user: User,
    loaded: LoadedCuratedWorkbook,
    *,
    confirmed_at: datetime,
    reason: str,
    request_id: UUID | None = None,
) -> dict:
    with session.no_autoflush:
        return _preview_curated_import(
            session, user, loaded, confirmed_at=confirmed_at, reason=reason, request_id=request_id
        )


def _last_confirmation(session: Session, event: Event) -> datetime | None:
    times = []
    if event.visibility_scope == "platform_shared":
        payloads = (
            e.display_detail_payload.get("version", {})
            for e in session.scalars(
                select(EventEvidence).where(EventEvidence.event_id == event.id)
            )
            if (e.display_detail_payload or {}).get("schema_version") == CURATED_SCHEMA
        )
    else:
        payloads = (
            o.candidate_payload
            for o in session.scalars(
                select(EventObservation).where(
                    EventObservation.event_id == event.id,
                    EventObservation.schema_version == CURATED_SCHEMA,
                )
            )
            if o.candidate_payload.get("confirmed") is True
        )
    for payload in payloads:
        if payload.get("reviewed_at"):
            times.append(_aware(datetime.fromisoformat(payload["reviewed_at"])))
    return max(times) if times else None


def _preview_curated_import(
    session: Session,
    user: User,
    loaded: LoadedCuratedWorkbook,
    *,
    confirmed_at: datetime,
    reason: str,
    request_id: UUID | None = None,
) -> dict:
    _require_curator(session, user)
    if confirmed_at.tzinfo is None or _aware(confirmed_at) > utc_now():
        raise ValueError("confirmation time must include timezone and cannot be in the future")
    if not 5 <= len(reason.strip()) <= 1000:
        raise ValueError("confirmation reason must contain 5 to 1000 characters")
    if request_id is not None and len(loaded.companies) != 1:
        raise ValueError("request binding requires one selected company")
    companies, records = [], []
    for item in loaded.companies:
        matches = list(
            session.scalars(
                select(Company).where(
                    or_(
                        Company.credit_code == item.credit_code,
                        Company.legal_name == item.legal_name,
                    )
                )
            )
        )
        valid = [
            c
            for c in matches
            if c.credit_code == item.credit_code
            and _normalized_identity_text(c.legal_name)
            == _normalized_identity_text(item.legal_name)
            and c.tenant_id is None
            and c.visibility_scope == "public"
        ]
        identity_issues = [
            asdict(i) for i in loaded.issues if i.sheet == "公司总表" and i.company_key == item.key
        ]
        company = valid[0] if len(valid) == len(matches) == 1 else None
        conflict = bool(identity_issues) or bool(matches and company is None)
        if company and company.identity_status not in {"verified", "unresolved"}:
            conflict = True
        alias_matches = (
            list(
                session.scalars(
                    select(CompanyAlias).where(
                        CompanyAlias.visibility_scope == "platform_shared",
                        CompanyAlias.verification_status == "verified",
                        CompanyAlias.normalized_alias == _normalized_identity_text(item.alias),
                    )
                )
            )
            if item.alias
            else []
        )
        alias_conflict = any(
            company is None or alias.company_id != company.id for alias in alias_matches
        )
        companies.append(
            {
                "company_key": item.key,
                "legal_name": item.legal_name,
                "credit_code": item.credit_code,
                "company_id": str(company.id) if company else None,
                "action": "conflict" if conflict else "reuse" if company else "create",
                "basis": company.identity_verification_basis
                if company and company.identity_status == "verified"
                else "curator_confirmed",
                "alias_action": "conflict"
                if alias_conflict
                else "reuse"
                if alias_matches
                else "add"
                if item.alias and item.alias != item.legal_name
                else "none",
            }
        )
        for record in (r for r in loaded.records if r.company_key == item.key):
            current = (
                _event(session, company.id, _fingerprint(loaded, item, record), None)
                if company
                else None
            )
            if current is None and company:
                current = _event(session, company.id, _fingerprint(loaded, item, record), user.id)
            version = digest(record.event_fields())
            action = (
                "conflict"
                if conflict
                else "unconfirmed"
                if not record.confirmed
                else (
                    "create"
                    if current is None
                    else "reuse"
                    if _field_version(current) == version
                    else "correct"
                )
            )
            if (
                current
                and current.visibility_scope == "platform_shared"
                and current.status != "published"
            ):
                action = "conflict"
            # 避免旧文件的迟到导入把较新、已经确认的版本覆盖回去。
            latest_confirmation = _last_confirmation(session, current) if current else None
            if (
                action == "correct"
                and latest_confirmation
                and latest_confirmation > _aware(confirmed_at)
            ):
                action = "conflict"
            records.append(
                {
                    "record_key": record.key,
                    "company_key": item.key,
                    "sheet": record.sheet,
                    "row": record.row,
                    "title": record.title,
                    "action": action,
                    "current_version": _field_version(current) if current else None,
                    "incoming_version": version,
                    "incoming_fields": record.event_fields(),
                    "incoming_metadata": record.public_metadata(),
                    "sources": [asdict(source) for source in record.sources],
                    "previous_fields": {
                        "title": current.title,
                        "summary": current.summary,
                        "facts": current.facts,
                    }
                    if current
                    else None,
                    "source_count": len(record.sources),
                }
            )
    binding = None
    if request_id:
        request = session.get(PersonalCompanyRequest, request_id)
        company = loaded.companies[0]
        allowed = (
            request is not None
            and request.status == "in_review"
            and (
                request.last_error_code == "identity_evidence_missing"
                and request.company_id is None
                and request.research_job_id is None
                and request.cancel_requested_at is None
                and request.cancelled_at is None
                and request.requested_credit_code == company.credit_code
                and _normalized_identity_text(request.requested_name or "")
                == _normalized_identity_text(company.legal_name)
            )
        )
        binding = {
            "request_id": str(request_id),
            "action": "bind_identity" if allowed else "conflict",
            "previous_status": request.status if request else None,
        }
    existing = _existing_import(session, user, loaded)
    plan = {
        "status": "duplicate" if existing else "dry_run",
        "dataset_key": loaded.dataset_key,
        "file_hash": loaded.file_hash,
        "selection_key": loaded.selection_key,
        "mode": loaded.mode,
        "target_scope": {
            "company": "public",
            "original_records": "personal_private",
            "confirmed_events": "platform_shared",
            "unconfirmed_records": "personal_private",
        },
        "confirmed_at": _aware(confirmed_at).isoformat(),
        "reason": reason.strip(),
        "companies": companies,
        "records": records,
        "issues": [asdict(i) for i in loaded.issues],
        "request_binding": binding,
        "database_writes": 0,
        "external_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": "0",
    }
    plan["preview_hash"] = digest(plan)
    return plan


def _source(session: Session, code: str, name: str, quality: str) -> Source:
    source = session.scalar(select(Source).where(Source.code == code))
    if source is None:
        source = Source(code=code, name=name, source_quality=quality, license_status="public")
        session.add(source)
        session.flush()
    elif (
        source.name != name or source.license_status != "public" or source.source_quality != quality
    ):
        raise ImportConflictError("curated source metadata conflicts with existing source")
    return source


def _document(
    session: Session,
    user: User,
    batch: ResearchImport,
    company: Company,
    *,
    source: Source,
    identifier: str,
    title: str,
    url: str,
    payload: dict,
    confirmed_at: datetime,
    published_on: date | None = None,
) -> tuple[RawDocument, bool]:
    content_hash = digest(payload)
    key = digest([identifier, source.id, content_hash])
    existing = session.scalar(
        select(RawDocument).where(
            RawDocument.visibility_scope == "personal_private",
            RawDocument.owner_user_id == user.id,
            RawDocument.owner_tenant_id.is_(None),
            RawDocument.document_dedupe_key == key,
        )
    )
    if existing:
        return existing, False
    document = RawDocument(
        research_import_id=batch.id,
        source_id=source.id,
        visibility_scope="personal_private",
        owner_user_id=user.id,
        external_record_id=f"curated:{key}",
        document_dedupe_key=key,
        content_hash=content_hash,
        title=title,
        canonical_url=url,
        license_status="public",
        published_on=published_on,
        observed_at=utc_now(),
        payload={
            "curated_record": payload,
            "_curation": {
                "schema_version": CURATED_SCHEMA,
                "confirmed_by": str(user.id),
                "confirmed_at": _aware(confirmed_at).isoformat(),
                "origin": "curator_record_not_webpage",
            },
            "_source_verification": {
                "status": "unchecked",
                "reason": "curated_source_not_fetched",
                "external_calls": 0,
            },
        },
    )
    session.add(document)
    session.flush()
    session.add(
        EntityMention(
            raw_document_id=document.id,
            candidate_company_id=company.id,
            visibility_scope="personal_private",
            owner_user_id=user.id,
            mention_text=company.legal_name,
            match_rule="curator_confirmed",
            match_confidence=Decimal("1"),
            resolution_status="verified",
        )
    )
    session.flush()
    return document, True


def _store_record(
    session: Session,
    user: User,
    batch: ResearchImport,
    loaded: LoadedCuratedWorkbook,
    company_input: CuratedCompany,
    company: Company,
    record: CuratedRecord,
    confirmed_at: datetime,
    reason: str,
) -> tuple[int, int, int]:
    fingerprint = _fingerprint(loaded, company_input, record)
    fields, version = record.event_fields(), digest(record.event_fields())
    event = _event(session, company.id, fingerprint, user.id)
    created = event is None
    record_hash = digest(
        {
            "values": record.values,
            "fields": fields,
            "confirmed": record.confirmed,
            "sources": [asdict(source) for source in record.sources],
        }
    )
    previous_observation = (
        session.scalar(
            select(EventObservation)
            .where(
                EventObservation.event_id == event.id,
                EventObservation.schema_version == CURATED_SCHEMA,
            )
            .order_by(EventObservation.created_at.desc(), EventObservation.id.desc())
            .limit(1)
        )
        if event
        else None
    )
    if (
        previous_observation
        and previous_observation.candidate_payload.get("record_hash") == record_hash
    ):
        return 0, 0, 0
    if event is None:
        event = Event(
            company_id=company.id,
            visibility_scope="personal_private",
            owner_user_id=user.id,
            fingerprint_version=FINGERPRINT_VERSION,
            event_fingerprint=fingerprint,
            status="candidate",
            publication_route="unconfirmed_lead",
            publication_policy_version=CURATED_SCHEMA,
            direction="unknown",
            materiality_score=0,
            risk_severity="none",
            confidence_score=Decimal("0"),
            source_quality="E",
            **{k: v for k, v in fields.items() if k != "published_on"},
            published_on=exact_day(fields["published_on"] or ""),
        )
        session.add(event)
        session.flush()
    quality = record.values.get("证据等级", "")
    source_quality = (
        "A"
        if quality.startswith("A1")
        else "B"
        if quality.startswith("A2")
        else ("C" if quality.startswith("B") else "E")
    )
    source_records = record.sources or (None,)
    documents, evidence_ids, new_documents = [], [], 0
    for source_record in source_records:
        name = source_record.name if source_record else "人工待核记录"
        url = source_record.url if source_record else f"urn:curated:{fingerprint}"
        source = _source(
            session, f"curated_{digest([name, url, source_quality])[:40]}", name, source_quality
        )
        document, new = _document(
            session,
            user,
            batch,
            company,
            source=source,
            identifier=fingerprint,
            title=f"{company.legal_name}｜{record.title}（人工整理记录）",
            url=url,
            payload={
                "dataset_key": loaded.dataset_key,
                "company_key": company_input.key,
                "sheet": record.sheet,
                "row": record.row,
                "record_key": record.key,
                "record_hash": record_hash,
                "previous_observation_id": str(previous_observation.id)
                if previous_observation
                else None,
                "values": record.values,
                "event_fields": fields,
                "fact_version": version,
                "confirmed": record.confirmed,
                "metadata": record.public_metadata(),
                "source_metadata": source_record.metadata if source_record else {},
            },
            confirmed_at=confirmed_at,
            published_on=exact_day(fields["published_on"] or ""),
        )
        documents.append(document)
        new_documents += int(new)
        for excerpt in _evidence_excerpts(fields["facts"]):
            if len(excerpt) > 1000:
                raise ImportConflictError("curated field exceeds shared evidence excerpt limit")
            # span_hash 与既有证据规范一致，是 UTF-8 摘录自身的 SHA-256。
            span_hash = hashlib.sha256(excerpt.encode()).hexdigest()
            evidence = session.scalar(
                select(EventEvidence).where(
                    EventEvidence.event_id == event.id,
                    EventEvidence.raw_document_id == document.id,
                    EventEvidence.span_hash == span_hash,
                )
            )
            if evidence is None:
                evidence = EventEvidence(
                    event_id=event.id,
                    raw_document_id=document.id,
                    visibility_scope="personal_private",
                    owner_user_id=user.id,
                    evidence_excerpt=excerpt,
                    span_hash=span_hash,
                    support_type="supports",
                )
                session.add(evidence)
                session.flush()
            evidence_ids.append(evidence.id)
    observation = session.scalar(
        select(EventObservation).where(
            EventObservation.event_id == event.id,
            EventObservation.raw_document_id == documents[0].id,
            EventObservation.schema_version == CURATED_SCHEMA,
        )
    )
    if observation is None:
        observation = EventObservation(
            event_id=event.id,
            raw_document_id=documents[0].id,
            schema_version=CURATED_SCHEMA,
            fact_version=version,
            observation_kind="initial"
            if created
            else ("same_facts" if _field_version(event) == version else "correction_candidate"),
            occurred_on=exact_day(record.values.get("实际发生日期", "")),
            date_precision="day" if exact_day(record.values.get("实际发生日期", "")) else "unknown",
            candidate_payload={
                "record_hash": record_hash,
                "event_fields": fields,
                "metadata": record.public_metadata(),
                "confirmed": record.confirmed,
                "evidence_ids": [str(i) for i in evidence_ids],
                "sources": [{"name": s.name, "url": s.url} for s in record.sources],
                "reviewed_at": _aware(confirmed_at).isoformat(),
            },
            created_by=user.id,
        )
        session.add(observation)
        session.flush()
    promoted = 0
    for key, value in fields.items():
        setattr(event, key, exact_day(value or "") if key == "published_on" else value)
    event.observed_at = observation.created_at
    event.source_quality = source_quality
    materialize_event_fact_ledger(session, event)
    if record.confirmed:
        result = promote_private_event(
            session,
            event.id,
            user,
            title=record.title,
            summary=record.summary,
            reason=reason,
            evidence_ids=evidence_ids,
            confirm_evidence_support=True,
            confirm_unchecked_links=True,
            auto_publish_enabled=False,
            observation_id=observation.id,
            commit=False,
            refresh_snapshot=False,
        )
        promoted = int(not result.reused_shared_event)
    return new_documents, int(created), promoted


def _evidence_excerpts(facts: list[dict]) -> list[str]:
    prefix = "人工整理记录（非网页原文）"
    excerpts, current = [], prefix
    for fact in facts:
        line = f"\n{fact['name']}：{fact['value']}"
        if len(current + line) > 1000:
            excerpts.append(current)
            current = prefix
        current += line
    excerpts.append(current)
    return excerpts


def apply_curated_import(
    session: Session,
    user: User,
    loaded: LoadedCuratedWorkbook,
    *,
    confirmed_at: datetime,
    reason: str,
    preview_hash: str,
    request_id: UUID | None = None,
) -> dict:
    try:
        _require_curator(session, user)
        if len(loaded.company_keys) != 1 or len(loaded.companies) != 1:
            raise ValueError("E4.1 applies exactly one explicitly selected company")
        if session.get_bind().dialect.name == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": int(digest("curated-import-v1")[:15], 16)},
            )
        if request_id:
            session.get(
                PersonalCompanyRequest, request_id, populate_existing=True, with_for_update=True
            )
        plan = preview_curated_import(
            session, user, loaded, confirmed_at=confirmed_at, reason=reason, request_id=request_id
        )
        previous = _existing_import(session, user, loaded)
        if previous:
            if request_id and plan["request_binding"]["action"] == "bind_identity":
                raise ImportConflictError(
                    "batch already imported; no new request binding was applied"
                )
            result = {
                "status": "duplicate",
                "research_import_id": str(previous.id),
                "external_calls": 0,
                "database_writes": 0,
            }
            session.rollback()
            return result
        if plan["preview_hash"] != preview_hash:
            raise ImportConflictError("preview changed; review the current plan before applying")
        if plan["request_binding"] and plan["request_binding"]["action"] == "conflict":
            raise ImportConflictError("request is not eligible for curator identity binding")
        item, company_plan = loaded.companies[0], plan["companies"][0]
        if company_plan["action"] == "conflict":
            raise ImportConflictError("company identity requires targeted review")
        company = (
            session.get(Company, UUID(company_plan["company_id"]))
            if company_plan["company_id"]
            else None
        )
        if company is None:
            company = Company(
                credit_code=item.credit_code,
                legal_name=item.legal_name,
                registered_region=item.region or None,
                tenant_id=None,
                visibility_scope="public",
                identity_status="verified",
                identity_verification_basis="curator_confirmed",
                last_identity_checked_at=_aware(confirmed_at),
            )
            session.add(company)
            session.flush()
        elif not _is_platform_shared_company(company):
            company.identity_status = "verified"
            company.identity_verification_basis = "curator_confirmed"
            company.last_identity_checked_at = _aware(confirmed_at)
        previous_snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == company.id,
                CompanySnapshot.is_current.is_(True),
                CompanySnapshot.visibility_scope == "platform_shared",
            )
        )
        previous_check = previous_snapshot.last_checked_at if previous_snapshot else None
        previous_freshness = previous_snapshot.freshness_status if previous_snapshot else "unknown"
        batch = ResearchImport(
            tenant_id=user.tenant_id,
            imported_by=user.id,
            schema_version="curated-v1",
            batch_id=f"curated-{loaded.file_hash[:32]}-{loaded.selection_key[:32]}",
            queried_at=_aware(confirmed_at),
            research_tool="curator_reviewed_workbook",
            original_query=f"{loaded.dataset_key} / {loaded.mode}",
            target_company_hint=item.legal_name,
            source_filename=loaded.source_filename,
            file_format="xlsx",
            file_hash=loaded.file_hash,
            parser_version=PARSER_VERSION,
            selection_key=loaded.selection_key,
            license_status="public",
            status="processing",
            record_count=len(loaded.records),
            resolved_count=0,
            unresolved_count=sum(i.company_key == item.key for i in loaded.issues)
            + int(company_plan["alias_action"] == "conflict"),
            auto_published_count=0,
            unconfirmed_count=0,
            identity_review_count=0,
        )
        session.add(batch)
        session.flush()
        identity_source = _source(session, "curator_identity", "负责人确认的主体资料", "E")
        identity_document, new = _document(
            session,
            user,
            batch,
            company,
            source=identity_source,
            identifier=digest([loaded.dataset_key, item.key, "identity"]),
            title=f"{company.legal_name}｜负责人确认主体",
            url=(item.sources[0].url if item.sources else f"urn:curated:identity:{company.id}"),
            payload={
                "dataset_key": loaded.dataset_key,
                "company_key": item.key,
                "sheet": "公司总表",
                "row": item.row,
                "identity": item.identity(),
            },
            confirmed_at=confirmed_at,
        )
        if company_plan["alias_action"] == "add":
            session.add(
                CompanyAlias(
                    company_id=company.id,
                    source_id=identity_source.id,
                    visibility_scope="platform_shared",
                    alias=item.alias,
                    normalized_alias=_normalized_identity_text(item.alias),
                    alias_type="brand",
                    verification_status="verified",
                )
            )
        counts = {
            "documents_created": int(new),
            "private_events_created": 0,
            "shared_events_created": 0,
            "confirmed_records": 0,
            "unconfirmed_records": 0,
            "corrected_records": 0,
        }
        incoming = {(r.sheet, r.key): r for r in loaded.records}
        changed_content = False
        for planned in plan["records"]:
            if planned["action"] == "conflict":
                batch.unresolved_count += 1
                continue
            record = incoming[(planned["sheet"], planned["record_key"])]
            docs, events, shared = _store_record(
                session, user, batch, loaded, item, company, record, confirmed_at, reason
            )
            counts["documents_created"] += docs
            counts["private_events_created"] += events
            counts["shared_events_created"] += shared
            counts["confirmed_records" if record.confirmed else "unconfirmed_records"] += 1
            counts["corrected_records"] += int(planned["action"] == "correct" and docs > 0)
            batch.resolved_count += 1
            changed_content |= record.confirmed and docs > 0
        if changed_content:
            _refresh_company_snapshot(session, company, "platform_shared", None, None)
            session.flush()
            snapshot = session.scalar(
                select(CompanySnapshot).where(
                    CompanySnapshot.company_id == company.id,
                    CompanySnapshot.is_current.is_(True),
                    CompanySnapshot.visibility_scope == "platform_shared",
                )
            )
            snapshot.last_checked_at = previous_check
            snapshot.freshness_status = previous_freshness
            snapshot.summary = {
                **snapshot.summary,
                "curated_imported_at": utc_now().isoformat(),
                "curated_confirmed_at": _aware(confirmed_at).isoformat(),
            }
            snapshot.information_gaps = [
                *(
                    gap
                    for gap in snapshot.information_gaps
                    if "数据基准使用系统发现日期" not in gap
                ),
                "人工资料保留原日期精度；未知发生日期不使用导入时间补填。",
                "初始资料由负责人复核；系统尚未因本次导入重新联网检查。",
                "人工初始资料未作重要性、风险与置信度评分；不表示公司没有风险。",
            ]
            accepted = {
                (r["sheet"], r["record_key"]) for r in plan["records"] if r["action"] != "conflict"
            }
            as_of_dates = [
                exact_day(r.values.get("资料截止日期", ""))
                for r in loaded.records
                if r.confirmed and (r.sheet, r.key) in accepted
            ]
            as_of_dates = [day for day in as_of_dates if day]
            if as_of_dates:
                snapshot.data_as_of = max(
                    [*as_of_dates, *([snapshot.data_as_of] if snapshot.data_as_of else [])]
                )
        request_before = None
        if request_id:
            request = session.scalar(
                select(PersonalCompanyRequest)
                .where(PersonalCompanyRequest.id == request_id)
                .with_for_update()
            )
            if (
                request is None
                or request.status != "in_review"
                or request.cancel_requested_at
                or request.last_error_code != "identity_evidence_missing"
            ):
                raise ImportConflictError("request changed during import")
            request_before = {
                "status": request.status,
                "last_error_code": request.last_error_code,
                "external_calls": request.external_calls,
                "cache_hits": request.cache_hits,
            }
            request.company_id = company.id
            request.resolved_legal_name, request.resolved_credit_code = (
                company.legal_name,
                company.credit_code,
            )
            request.resolved_registered_region = company.registered_region
            request.identity_checked_at = company.last_identity_checked_at
            request.confirmed_at, request.reviewed_at = _aware(confirmed_at), utc_now()
            request.reviewed_by_id = user.id
            request.decision_reason = reason.strip()
            request.last_error_code = "curator_identity_confirmed"
            # 保留 in_review 作为明确的等待业务安排状态；此切片不创建可运行研究任务。
        batch.status = "completed_with_unresolved" if batch.unresolved_count else "completed"
        batch.unconfirmed_count = counts["unconfirmed_records"]
        session.add(
            UsageLedger(
                tenant_id=user.tenant_id,
                provider="curated_workbook_import",
                operation="curated_import",
                external_calls=0,
                input_tokens=0,
                output_tokens=0,
                estimated_cost=Decimal("0"),
                idempotency_key=digest(["curated-import", batch.id]),
                metrics={
                    "research_import_id": str(batch.id),
                    "mode": loaded.mode,
                    "confirmed_by": str(user.id),
                    "reason": reason.strip(),
                    "identity_document_id": str(identity_document.id),
                    "request_id": str(request_id) if request_id else None,
                    "request_before": request_before,
                    "preview_hash": preview_hash,
                    "issues": plan["issues"],
                    "records": plan["records"],
                    **counts,
                },
            )
        )
        result = {
            "status": batch.status,
            "research_import_id": str(batch.id),
            "company_id": str(company.id),
            "external_calls": 0,
            "mode": loaded.mode,
            "unresolved_records": batch.unresolved_count,
            **counts,
        }
        session.commit()
        return result
    except IntegrityError as error:
        session.rollback()
        raise ImportConflictError(
            "import conflicts with an existing record; no changes applied"
        ) from error
    except Exception:
        session.rollback()
        raise
