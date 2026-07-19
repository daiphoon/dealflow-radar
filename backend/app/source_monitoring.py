from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from backend.app.config import Settings, SourceMonitoringPolicy
from backend.app.models import (
    ORGANIZATION_PRIVATE_SCOPE,
    CandidateDocument,
    Company,
    Event,
    EventEvidence,
    RawDocument,
    ResearchImport,
    SourceCheckRun,
    TrustedSource,
    UsageLedger,
    User,
    utc_now,
)
from backend.app.providers import (
    CandidateResearchImportProvider,
    CompanyIdentityEvidence,
    DocumentVerification,
    ManualResearchImportBatch,
    ManualResearchRecord,
)
from backend.app.schemas import (
    CandidateDocumentDecisionOut,
    CandidateDocumentOut,
    CandidateResearchImportIn,
    CandidateResearchImportOut,
    SourceCheckRunOut,
    TrustedSourceCreate,
    TrustedSourceOut,
    TrustedSourceUpdate,
)
from backend.app.services import (
    AccessDeniedError,
    ImportConflictError,
    import_manual_research,
    user_has_role,
)
from backend.app.source_fetcher import (
    FetchBatchResult,
    SourceFetchError,
    TrustedSourceFetcher,
    UrlSafetyError,
    canonicalize_source_url,
    normalize_list_path_prefix,
    normalize_root_domain,
)


class SourceMonitoringAccessError(Exception):
    pass


class SourceMonitoringNotFoundError(Exception):
    pass


class SourceMonitoringConflictError(Exception):
    pass


class SourceMonitoringValidationError(Exception):
    pass


PROCESSING_STATUSES = {
    "pending",
    "worth_research",
    "irrelevant",
    "duplicate",
    "source_unavailable",
}


@dataclass(frozen=True)
class SourceWorkerResult:
    status: str
    run_id: UUID | None = None
    request_count: int = 0
    downloaded_bytes: int = 0
    new_count: int = 0
    changed_count: int = 0
    unchanged_count: int = 0
    duplicate_count: int = 0
    failure_count: int = 0
    external_calls: int = 0
    estimated_cost: Decimal = Decimal("0")
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "run_id": str(self.run_id) if self.run_id else None,
            "request_count": self.request_count,
            "downloaded_bytes": self.downloaded_bytes,
            "new_count": self.new_count,
            "changed_count": self.changed_count,
            "unchanged_count": self.unchanged_count,
            "duplicate_count": self.duplicate_count,
            "failure_count": self.failure_count,
            "external_calls": self.external_calls,
            "estimated_cost": str(self.estimated_cost),
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class SourceScheduleResult:
    status: str
    due_count: int
    queued_count: int
    deferred_count: int
    source_ids: tuple[UUID, ...]
    external_calls: int = 0
    paid_api_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: Decimal = Decimal("0")

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "due_count": self.due_count,
            "queued_count": self.queued_count,
            "deferred_count": self.deferred_count,
            "source_ids": [str(source_id) for source_id in self.source_ids],
            "external_calls": self.external_calls,
            "paid_api_calls": self.paid_api_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost": str(self.estimated_cost),
        }


@dataclass(frozen=True)
class _RunLease:
    run_id: UUID
    tenant_id: UUID
    company_id: UUID
    trusted_source_id: UUID
    requested_by: UUID
    leased_until: datetime
    dry_run: bool
    policy_version: str
    max_requests: int
    max_download_bytes: int
    max_response_bytes: int
    timeout_seconds: int
    retry_limit: int
    max_redirects: int


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _require_platform_admin(session: Session, user: User) -> None:
    if not user_has_role(session, user.id, "platform_admin"):
        raise SourceMonitoringAccessError("platform administrator role required")


def _company_is_available_to_tenant(company: Company, tenant_id: UUID) -> bool:
    return company.tenant_id == tenant_id or (
        company.tenant_id is None
        and company.visibility_scope == "public"
        and company.identity_status == "verified"
    )


def _source_out(source: TrustedSource, company: Company) -> TrustedSourceOut:
    return TrustedSourceOut(
        id=source.id,
        company_id=source.company_id,
        company_legal_name=company.legal_name,
        company_identity_status=company.identity_status,
        name=source.name,
        source_type=source.source_type,
        root_domain=source.root_domain,
        start_url=source.start_url,
        list_path_prefix=source.list_path_prefix,
        enabled=source.enabled,
        access_basis=source.access_basis,
        license_status=source.license_status,
        check_frequency_minutes=source.check_frequency_minutes,
        content_retention_policy=source.content_retention_policy,
        visibility_scope=source.visibility_scope,
        last_checked_at=source.last_checked_at,
        last_success_at=source.last_success_at,
        last_failure_code=source.last_failure_code,
        last_http_status=source.last_http_status,
        consecutive_failures=source.consecutive_failures,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def _run_out(run: SourceCheckRun, source: TrustedSource) -> SourceCheckRunOut:
    return SourceCheckRunOut(
        id=run.id,
        company_id=run.company_id,
        trusted_source_id=run.trusted_source_id,
        source_name=source.name,
        trigger_type=run.trigger_type,
        scheduled_for=run.scheduled_for,
        status=run.status,
        dry_run=run.dry_run,
        policy_version=run.policy_version,
        request_count=run.request_count,
        downloaded_bytes=run.downloaded_bytes,
        new_count=run.new_count,
        changed_count=run.changed_count,
        unchanged_count=run.unchanged_count,
        duplicate_count=run.duplicate_count,
        failure_count=run.failure_count,
        external_calls=run.external_calls,
        paid_api_calls=run.paid_api_calls,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        estimated_cost=run.estimated_cost,
        robots_status=run.robots_status,
        error_code=run.error_code,
        error_message=run.error_message,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
    )


def _candidate_out(
    candidate: CandidateDocument,
    company: Company,
    source: TrustedSource,
) -> CandidateDocumentOut:
    return CandidateDocumentOut(
        id=candidate.id,
        company_id=candidate.company_id,
        company_legal_name=company.legal_name,
        trusted_source_id=candidate.trusted_source_id,
        source_name=source.name,
        canonical_url=candidate.canonical_url,
        title=candidate.title,
        published_at=candidate.published_at,
        first_discovered_at=candidate.first_discovered_at,
        last_observed_at=candidate.last_observed_at,
        content_hash=candidate.content_hash,
        change_type=candidate.change_type,
        link_health_status=candidate.link_health_status,
        http_status=candidate.http_status,
        excerpt=candidate.excerpt,
        license_status=candidate.license_status,
        current_source_license_status=source.license_status,
        processing_status=candidate.processing_status,
        identity_status_at_discovery=candidate.identity_status_at_discovery,
        visibility_scope=candidate.visibility_scope,
        handoff_payload=candidate.handoff_payload,
        processed_at=candidate.processed_at,
        decision_reason=candidate.decision_reason,
    )


def create_trusted_source(
    session: Session,
    user: User,
    payload: TrustedSourceCreate,
) -> TrustedSourceOut:
    _require_platform_admin(session, user)
    company = session.get(Company, payload.company_id)
    if company is None or not _company_is_available_to_tenant(company, user.tenant_id):
        raise SourceMonitoringNotFoundError("company not found")
    try:
        root_domain = normalize_root_domain(payload.root_domain)
        start_url = canonicalize_source_url(payload.start_url, root_domain)
        list_path_prefix = (
            normalize_list_path_prefix(payload.list_path_prefix)
            if payload.list_path_prefix is not None
            else None
        )
    except UrlSafetyError as error:
        raise SourceMonitoringValidationError(str(error)) from error
    if payload.source_type != "list_page" and list_path_prefix is not None:
        raise SourceMonitoringValidationError(
            "list path prefix is only valid for list-page sources"
        )
    if (
        payload.license_status in {"unclear", "restricted"}
        and payload.content_retention_policy != "metadata_only"
    ):
        raise SourceMonitoringValidationError(
            "unclear or restricted sources must use metadata-only retention"
        )
    existing = session.scalar(
        select(TrustedSource).where(
            TrustedSource.tenant_id == user.tenant_id,
            TrustedSource.company_id == company.id,
            TrustedSource.start_url == start_url,
        )
    )
    if existing is not None:
        raise SourceMonitoringConflictError("trusted source already exists")
    source = TrustedSource(
        tenant_id=user.tenant_id,
        company_id=company.id,
        name=payload.name.strip(),
        source_type=payload.source_type,
        root_domain=root_domain,
        start_url=start_url,
        list_path_prefix=list_path_prefix,
        enabled=True,
        access_basis=payload.access_basis.strip(),
        license_status=payload.license_status.strip(),
        check_frequency_minutes=payload.check_frequency_minutes,
        content_retention_policy=payload.content_retention_policy,
        visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
        created_by=user.id,
        updated_by=user.id,
    )
    session.add(source)
    session.commit()
    return _source_out(source, company)


def list_trusted_sources(
    session: Session,
    user: User,
    company_id: UUID | None = None,
) -> list[TrustedSourceOut]:
    _require_platform_admin(session, user)
    query = select(TrustedSource).where(TrustedSource.tenant_id == user.tenant_id)
    if company_id is not None:
        query = query.where(TrustedSource.company_id == company_id)
    sources = list(session.scalars(query.order_by(TrustedSource.created_at.desc())))
    output: list[TrustedSourceOut] = []
    for source in sources:
        company = session.get(Company, source.company_id)
        if company is not None and _company_is_available_to_tenant(company, user.tenant_id):
            output.append(_source_out(source, company))
    return output


def update_trusted_source(
    session: Session,
    user: User,
    source_id: UUID,
    payload: TrustedSourceUpdate,
) -> TrustedSourceOut:
    _require_platform_admin(session, user)
    source = session.scalar(
        select(TrustedSource).where(
            TrustedSource.id == source_id,
            TrustedSource.tenant_id == user.tenant_id,
        )
    )
    if source is None:
        raise SourceMonitoringNotFoundError("trusted source not found")
    if not payload.model_fields_set:
        raise SourceMonitoringValidationError("source update is empty")
    next_license_status = (
        payload.license_status
        if "license_status" in payload.model_fields_set
        else source.license_status
    )
    next_retention_policy = (
        payload.content_retention_policy
        if "content_retention_policy" in payload.model_fields_set
        else source.content_retention_policy
    )
    if next_license_status is None or next_retention_policy is None:
        raise SourceMonitoringValidationError("license and retention values cannot be null")
    if (
        next_license_status in {"unclear", "restricted"}
        and next_retention_policy != "metadata_only"
    ):
        raise SourceMonitoringValidationError(
            "unclear or restricted sources must use metadata-only retention"
        )
    if "enabled" in payload.model_fields_set:
        if payload.enabled is None:
            raise SourceMonitoringValidationError("enabled cannot be null")
        source.enabled = payload.enabled
    if "list_path_prefix" in payload.model_fields_set:
        if source.source_type != "list_page" and payload.list_path_prefix is not None:
            raise SourceMonitoringValidationError(
                "list path prefix is only valid for list-page sources"
            )
        try:
            normalized_prefix = (
                normalize_list_path_prefix(payload.list_path_prefix)
                if payload.list_path_prefix is not None
                else None
            )
        except UrlSafetyError as error:
            raise SourceMonitoringValidationError(str(error)) from error
        if source.list_path_prefix != normalized_prefix:
            source.list_path_prefix = normalized_prefix
            source.last_etag = None
            source.last_modified = None
            source.last_content_hash = None
    if "access_basis" in payload.model_fields_set:
        if payload.access_basis is None:
            raise SourceMonitoringValidationError("access basis cannot be null")
        source.access_basis = payload.access_basis.strip()
    if "license_status" in payload.model_fields_set:
        if payload.license_status is None:
            raise SourceMonitoringValidationError("license status cannot be null")
        source.license_status = payload.license_status
    if "content_retention_policy" in payload.model_fields_set:
        if payload.content_retention_policy is None:
            raise SourceMonitoringValidationError("content retention policy cannot be null")
        source.content_retention_policy = payload.content_retention_policy
    source.updated_by = user.id
    company = session.get(Company, source.company_id)
    if company is None:
        raise SourceMonitoringNotFoundError("company not found")
    session.commit()
    return _source_out(source, company)


def _active_run(session: Session, source_id: UUID) -> SourceCheckRun | None:
    return session.scalar(
        select(SourceCheckRun).where(
            SourceCheckRun.trusted_source_id == source_id,
            SourceCheckRun.status.in_(["queued", "running"]),
        )
    )


def queue_source_check(
    session: Session,
    user: User,
    source_id: UUID,
    policy: SourceMonitoringPolicy,
    *,
    dry_run: bool,
    trigger_type: str = "manual",
    scheduled_for: datetime | None = None,
    idempotency_key: str | None = None,
) -> SourceCheckRunOut:
    _require_platform_admin(session, user)
    source = session.scalar(
        select(TrustedSource).where(
            TrustedSource.id == source_id,
            TrustedSource.tenant_id == user.tenant_id,
        )
    )
    if source is None:
        raise SourceMonitoringNotFoundError("trusted source not found")
    if not source.enabled:
        raise SourceMonitoringValidationError("trusted source is disabled")
    if trigger_type not in {"manual", "scheduled"}:
        raise SourceMonitoringValidationError("unsupported source check trigger")
    existing = _active_run(session, source.id)
    if existing is not None:
        return _run_out(existing, source)
    if idempotency_key is not None:
        existing = session.scalar(
            select(SourceCheckRun).where(SourceCheckRun.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return _run_out(existing, source)
    run_id = uuid4()
    run = SourceCheckRun(
        id=run_id,
        tenant_id=user.tenant_id,
        company_id=source.company_id,
        trusted_source_id=source.id,
        requested_by=user.id,
        trigger_type=trigger_type,
        scheduled_for=scheduled_for,
        status="queued",
        dry_run=dry_run,
        visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
        policy_version=policy.version,
        idempotency_key=idempotency_key or _sha256(f"trusted-source-run:{run_id}"),
        max_requests=policy.max_requests_per_run,
        max_download_bytes=policy.max_download_bytes_per_run,
        max_response_bytes=policy.max_response_bytes,
        timeout_seconds=policy.timeout_seconds,
        retry_limit=policy.retry_limit,
        max_redirects=policy.max_redirects,
        request_log=[],
    )
    session.add(run)
    session.commit()
    return _run_out(run, source)


def queue_company_source_checks(
    session: Session,
    user: User,
    company_id: UUID,
    policy: SourceMonitoringPolicy,
    *,
    dry_run: bool,
) -> list[SourceCheckRunOut]:
    _require_platform_admin(session, user)
    sources = list(
        session.scalars(
            select(TrustedSource)
            .where(
                TrustedSource.tenant_id == user.tenant_id,
                TrustedSource.company_id == company_id,
                TrustedSource.enabled.is_(True),
            )
            .order_by(TrustedSource.created_at)
            .limit(10)
        )
    )
    if not sources:
        raise SourceMonitoringNotFoundError("no enabled trusted sources for company")
    return [
        queue_source_check(session, user, source.id, policy, dry_run=dry_run) for source in sources
    ]


def queue_source_check_batch(
    session: Session,
    user: User,
    source_ids: list[UUID],
    policy: SourceMonitoringPolicy,
    *,
    dry_run: bool,
) -> list[SourceCheckRunOut]:
    if len(set(source_ids)) != len(source_ids):
        raise SourceMonitoringValidationError("duplicate source IDs are not allowed")
    return [
        queue_source_check(session, user, source_id, policy, dry_run=dry_run)
        for source_id in source_ids
    ]


def _source_due_at(source: TrustedSource, policy: SourceMonitoringPolicy) -> datetime:
    if source.last_checked_at is None:
        return _as_utc(source.created_at)
    backoff_multiplier = min(
        2 ** max(source.consecutive_failures, 0),
        policy.failure_backoff_max_multiplier,
    )
    return _as_utc(source.last_checked_at) + timedelta(
        minutes=source.check_frequency_minutes * backoff_multiplier
    )


def queue_due_source_checks(
    session: Session,
    user: User,
    settings: Settings,
    *,
    now: datetime | None = None,
    dry_run: bool = True,
) -> SourceScheduleResult:
    _require_platform_admin(session, user)
    checked_at = _as_utc(now or utc_now())
    if not dry_run:
        if settings.paid_api_calls_enabled or settings.publication_policy.enabled:
            raise SourceMonitoringValidationError(
                "paid API calls and automatic publication must remain disabled"
            )
        if not all(
            (
                settings.auto_refresh_enabled,
                settings.source_monitor_scheduler_enabled,
                settings.external_calls_enabled,
                settings.trusted_source_calls_enabled,
            )
        ):
            raise SourceMonitoringValidationError(
                "AUTO_REFRESH_ENABLED, SOURCE_MONITOR_SCHEDULER_ENABLED, "
                "EXTERNAL_CALLS_ENABLED and TRUSTED_SOURCE_CALLS_ENABLED are required"
            )

    _set_worker_context(session, user)
    sources = list(
        session.scalars(
            select(TrustedSource).where(
                TrustedSource.tenant_id == user.tenant_id,
                TrustedSource.enabled.is_(True),
            )
        )
    )
    active_source_ids = set(
        session.scalars(
            select(SourceCheckRun.trusted_source_id).where(
                SourceCheckRun.tenant_id == user.tenant_id,
                SourceCheckRun.status.in_(["queued", "running"]),
            )
        )
    )
    due_sources = sorted(
        (
            (source, _source_due_at(source, settings.source_monitoring_policy))
            for source in sources
            if source.id not in active_source_ids
            and _source_due_at(source, settings.source_monitoring_policy) <= checked_at
        ),
        key=lambda item: (item[1], str(item[0].id)),
    )
    max_sources = settings.source_monitoring_policy.scheduler_max_sources_per_run
    selected = due_sources[:max_sources]
    deferred_count = max(0, len(due_sources) - len(selected))
    if dry_run:
        session.rollback()
        return SourceScheduleResult(
            status="dry_run",
            due_count=len(due_sources),
            queued_count=0,
            deferred_count=deferred_count,
            source_ids=tuple(source.id for source, _ in selected),
        )

    queued_runs: list[SourceCheckRunOut] = []
    for source, scheduled_for in selected:
        queued_runs.append(
            queue_source_check(
                session,
                user,
                source.id,
                settings.source_monitoring_policy,
                dry_run=False,
                trigger_type="scheduled",
                scheduled_for=scheduled_for,
                idempotency_key=_sha256(
                    f"trusted-source-scheduled:{source.id}:{scheduled_for.isoformat()}"
                ),
            )
        )
    if queued_runs:
        session.add(
            UsageLedger(
                tenant_id=user.tenant_id,
                provider="trusted_source_scheduler",
                operation="trusted_source_schedule",
                external_calls=0,
                input_tokens=0,
                output_tokens=0,
                estimated_cost=Decimal("0"),
                metrics={
                    "scheduled_at": checked_at.isoformat(),
                    "due_count": len(due_sources),
                    "queued_count": len(queued_runs),
                    "deferred_count": deferred_count,
                    "source_ids": [str(item.trusted_source_id) for item in queued_runs],
                    "auto_publish": 0,
                    "paid_api_calls": 0,
                },
                idempotency_key=_sha256(
                    "trusted-source-schedule:"
                    + ":".join(sorted(str(item.id) for item in queued_runs))
                ),
            )
        )
        session.commit()
    return SourceScheduleResult(
        status="queued" if queued_runs else "idle",
        due_count=len(due_sources),
        queued_count=len(queued_runs),
        deferred_count=deferred_count,
        source_ids=tuple(item.trusted_source_id for item in queued_runs),
    )


def list_source_check_runs(
    session: Session,
    user: User,
    company_id: UUID | None = None,
) -> list[SourceCheckRunOut]:
    _require_platform_admin(session, user)
    query = select(SourceCheckRun).where(SourceCheckRun.tenant_id == user.tenant_id)
    if company_id is not None:
        query = query.where(SourceCheckRun.company_id == company_id)
    runs = list(session.scalars(query.order_by(SourceCheckRun.created_at.desc()).limit(100)))
    sources = {
        source.id: source
        for source in session.scalars(
            select(TrustedSource).where(TrustedSource.tenant_id == user.tenant_id)
        )
    }
    return [
        _run_out(run, sources[run.trusted_source_id])
        for run in runs
        if run.trusted_source_id in sources
    ]


def list_candidate_documents(
    session: Session,
    user: User,
    *,
    company_id: UUID | None = None,
    processing_status: str | None = None,
) -> list[CandidateDocumentOut]:
    _require_platform_admin(session, user)
    query = select(CandidateDocument).where(CandidateDocument.tenant_id == user.tenant_id)
    if company_id is not None:
        query = query.where(CandidateDocument.company_id == company_id)
    if processing_status is not None:
        if processing_status not in PROCESSING_STATUSES:
            raise SourceMonitoringValidationError("unsupported candidate processing status")
        query = query.where(CandidateDocument.processing_status == processing_status)
    candidates = list(
        session.scalars(query.order_by(CandidateDocument.first_discovered_at.desc()).limit(200))
    )
    output: list[CandidateDocumentOut] = []
    for candidate in candidates:
        company = session.get(Company, candidate.company_id)
        source = session.get(TrustedSource, candidate.trusted_source_id)
        if company is not None and source is not None:
            output.append(_candidate_out(candidate, company, source))
    return output


def decide_candidate_document(
    session: Session,
    user: User,
    candidate_id: UUID,
    decision: str,
    reason: str,
) -> CandidateDocumentDecisionOut:
    _require_platform_admin(session, user)
    candidate = session.scalar(
        select(CandidateDocument).where(
            CandidateDocument.id == candidate_id,
            CandidateDocument.tenant_id == user.tenant_id,
        )
    )
    if candidate is None:
        raise SourceMonitoringNotFoundError("candidate document not found")
    if candidate.processing_status != "pending":
        if candidate.processing_status == decision:
            return CandidateDocumentDecisionOut(
                candidate_id=candidate.id,
                processing_status=candidate.processing_status,
                handoff_payload=candidate.handoff_payload,
            )
        raise SourceMonitoringConflictError("candidate document was already processed")
    company = session.get(Company, candidate.company_id)
    source = session.get(TrustedSource, candidate.trusted_source_id)
    if company is None or source is None:
        raise SourceMonitoringNotFoundError("candidate lineage is incomplete")
    candidate.processing_status = decision
    candidate.processed_by = user.id
    candidate.processed_at = utc_now()
    candidate.decision_reason = reason.strip()
    if decision == "worth_research":
        candidate.handoff_payload = {
            "workflow": "manual_research_import",
            "company_id": str(company.id),
            "company_legal_name": company.legal_name,
            "company_identity_status": company.identity_status,
            "source_name": source.name,
            "canonical_url": candidate.canonical_url,
            "title": candidate.title,
            "published_at": (
                candidate.published_at.isoformat() if candidate.published_at is not None else None
            ),
            "candidate_document_id": str(candidate.id),
            "requires_structured_research_import": True,
            "event_created": False,
            "shared_fact_created": False,
        }
    session.commit()
    return CandidateDocumentDecisionOut(
        candidate_id=candidate.id,
        processing_status=candidate.processing_status,
        handoff_payload=candidate.handoff_payload,
    )


def _candidate_research_import_out(
    session: Session,
    candidate: CandidateDocument,
    *,
    reused: bool,
) -> CandidateResearchImportOut | None:
    document = session.scalar(
        select(RawDocument).where(
            RawDocument.candidate_document_id == candidate.id,
            RawDocument.visibility_scope == ORGANIZATION_PRIVATE_SCOPE,
            RawDocument.owner_tenant_id == candidate.tenant_id,
        )
    )
    if document is None or document.research_import_id is None:
        return None
    evidence = session.scalar(
        select(EventEvidence).where(
            EventEvidence.raw_document_id == document.id,
            EventEvidence.visibility_scope == ORGANIZATION_PRIVATE_SCOPE,
            EventEvidence.owner_tenant_id == candidate.tenant_id,
        )
    )
    if evidence is None:
        raise SourceMonitoringConflictError("candidate research lineage is incomplete")
    event = session.get(Event, evidence.event_id)
    research_import = session.get(ResearchImport, document.research_import_id)
    if (
        event is None
        or research_import is None
        or event.visibility_scope != ORGANIZATION_PRIVATE_SCOPE
        or event.owner_tenant_id != candidate.tenant_id
        or research_import.tenant_id != candidate.tenant_id
    ):
        raise SourceMonitoringConflictError("candidate research lineage is invalid")
    return CandidateResearchImportOut(
        candidate_id=candidate.id,
        research_import_id=research_import.id,
        raw_document_id=document.id,
        private_event_id=event.id,
        status=research_import.status,
        reused=reused,
        auto_published=False,
        shared_fact_created=False,
        external_calls=0,
    )


def import_candidate_research(
    session: Session,
    user: User,
    candidate_id: UUID,
    payload: CandidateResearchImportIn,
    settings: Settings,
) -> CandidateResearchImportOut:
    _require_platform_admin(session, user)
    if not user_has_role(session, user.id, "institution_admin"):
        raise SourceMonitoringAccessError("institution administrator role required")
    if settings.publication_policy.enabled:
        raise SourceMonitoringValidationError("automatic publication must remain disabled")
    candidate = session.scalar(
        select(CandidateDocument).where(
            CandidateDocument.id == candidate_id,
            CandidateDocument.tenant_id == user.tenant_id,
        )
    )
    if candidate is None:
        raise SourceMonitoringNotFoundError("candidate document not found")
    existing = _candidate_research_import_out(session, candidate, reused=True)
    if existing is not None:
        return existing
    if candidate.processing_status != "worth_research":
        raise SourceMonitoringConflictError("candidate is not marked worth researching")
    if candidate.identity_status_at_discovery != "verified":
        raise SourceMonitoringValidationError("candidate company identity was unresolved")
    if candidate.link_health_status == "broken":
        raise SourceMonitoringValidationError("broken candidate source cannot enter research")
    company = session.get(Company, candidate.company_id)
    source = session.get(TrustedSource, candidate.trusted_source_id)
    if company is None or source is None:
        raise SourceMonitoringNotFoundError("candidate lineage is incomplete")
    if company.identity_status != "verified":
        raise SourceMonitoringValidationError("company identity is not currently verified")
    if source.tenant_id != user.tenant_id or source.company_id != company.id:
        raise SourceMonitoringNotFoundError("candidate source is outside the current tenant")
    if source.license_status in {"unclear", "restricted"}:
        raise SourceMonitoringValidationError(
            "candidate source license must be clarified before research import"
        )

    published_at = _as_utc(candidate.published_at) if candidate.published_at is not None else None
    batch = ManualResearchImportBatch(
        schema_version="1.0",
        batch_id=f"candidate-{candidate.id}",
        queried_at=_as_utc(candidate.processed_at or utc_now()),
        research_tool="trusted_source_monitoring",
        agent_name="受控候选研究交接",
        original_query=f"复核候选文档 {candidate.id}：{candidate.title}",
        target_company_hint=company.legal_name,
        license_status=source.license_status,
        records=[
            ManualResearchRecord(
                external_record_id=f"candidate-{candidate.id}-{candidate.content_hash[:12]}",
                company_identity_evidence=CompanyIdentityEvidence(
                    legal_name=company.legal_name,
                    credit_code=company.credit_code,
                    registered_region=company.registered_region,
                    official_website=company.official_website,
                ),
                source_code=f"trusted_source_{source.id.hex}",
                source_name=source.name,
                canonical_url=candidate.canonical_url,
                source_published_at=published_at,
                occurred_at=payload.occurred_at,
                title=payload.title,
                evidence_excerpt=payload.evidence_excerpt,
                event_type=payload.event_type,
                event_subtype=payload.event_subtype,
                direction=payload.direction,
                materiality_score=payload.materiality_score,
                risk_severity=payload.risk_severity,
                confidence_score=payload.confidence_score,
                source_quality=payload.source_quality,
                facts=[
                    {
                        "name": payload.fact_name,
                        "value": payload.fact_value,
                        "unit": payload.fact_unit,
                    }
                ],
                uncertainties=payload.uncertainties,
                requires_human_review=True,
            )
        ],
    )
    verification = DocumentVerification(
        status=candidate.link_health_status,
        checked_at=_as_utc(candidate.last_observed_at),
        http_status=candidate.http_status,
        final_url=candidate.canonical_url,
        reason="trusted_source_monitoring_observation",
        external_calls=0,
    )
    provider = CandidateResearchImportProvider(candidate.id, batch)
    candidate.handoff_payload = {
        **candidate.handoff_payload,
        "research_reason": payload.research_reason,
    }
    try:
        import_manual_research(
            session,
            user,
            provider,
            settings.publication_policy,
            candidate_document=candidate,
            verification_overrides={candidate.canonical_url: verification},
        )
    except AccessDeniedError as error:
        raise SourceMonitoringAccessError(str(error)) from error
    except ImportConflictError as error:
        raise SourceMonitoringConflictError(str(error)) from error
    # The reused import service commits atomically. PostgreSQL transaction-local
    # RLS context must be restored before reading the committed lineage.
    _set_worker_context(session, user)
    candidate = session.get(CandidateDocument, candidate.id)
    if candidate is None:
        raise SourceMonitoringConflictError("candidate disappeared after research import")
    output = _candidate_research_import_out(session, candidate, reused=False)
    if output is None:
        raise SourceMonitoringConflictError("candidate research import did not create lineage")
    return output


def _set_worker_context(session: Session, user: User) -> None:
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text(
            "SELECT set_config('app.current_user_id', :user_id, true), "
            "set_config('app.current_tenant_id', :tenant_id, true)"
        ),
        {"user_id": str(user.id), "tenant_id": str(user.tenant_id)},
    )


def _lease_next_run(
    session: Session,
    user: User,
    policy: SourceMonitoringPolicy,
    now: datetime,
) -> _RunLease | None:
    _set_worker_context(session, user)
    run = session.scalar(
        select(SourceCheckRun)
        .join(TrustedSource, TrustedSource.id == SourceCheckRun.trusted_source_id)
        .where(
            SourceCheckRun.tenant_id == user.tenant_id,
            TrustedSource.enabled.is_(True),
            or_(
                SourceCheckRun.status == "queued",
                and_(
                    SourceCheckRun.status == "running",
                    or_(
                        SourceCheckRun.leased_until.is_(None),
                        SourceCheckRun.leased_until <= now,
                    ),
                ),
            ),
        )
        .order_by(SourceCheckRun.created_at)
        .with_for_update(skip_locked=True)
    )
    if run is None:
        session.rollback()
        return None
    leased_until = now + timedelta(seconds=policy.worker_lease_seconds)
    run.status = "running"
    run.started_at = run.started_at or now
    run.heartbeat_at = now
    run.leased_until = leased_until
    session.commit()
    return _RunLease(
        run_id=run.id,
        tenant_id=run.tenant_id,
        company_id=run.company_id,
        trusted_source_id=run.trusted_source_id,
        requested_by=run.requested_by,
        leased_until=leased_until,
        dry_run=run.dry_run,
        policy_version=run.policy_version,
        max_requests=run.max_requests,
        max_download_bytes=run.max_download_bytes,
        max_response_bytes=run.max_response_bytes,
        timeout_seconds=run.timeout_seconds,
        retry_limit=run.retry_limit,
        max_redirects=run.max_redirects,
    )


def _latest_candidate_state(
    session: Session,
    user: User,
    source: TrustedSource,
) -> dict[str, dict[str, str | None]]:
    _set_worker_context(session, user)
    rows = list(
        session.scalars(
            select(CandidateDocument)
            .where(CandidateDocument.trusted_source_id == source.id)
            .order_by(CandidateDocument.first_discovered_at.desc())
        )
    )
    state: dict[str, dict[str, str | None]] = {}
    for candidate in rows:
        state.setdefault(
            candidate.canonical_url,
            {
                "etag": candidate.etag,
                "last_modified": candidate.last_modified,
                "content_hash": candidate.content_hash,
            },
        )
    state[source.start_url] = {
        "etag": source.last_etag,
        "last_modified": source.last_modified,
        "content_hash": source.last_content_hash,
    }
    session.commit()
    return state


def _usage_ledger(
    run: SourceCheckRun,
    *,
    outcome: str,
) -> UsageLedger:
    return UsageLedger(
        tenant_id=run.tenant_id,
        company_id=run.company_id,
        provider="trusted_source_http",
        operation="trusted_source_check",
        external_calls=run.external_calls,
        input_tokens=0,
        output_tokens=0,
        estimated_cost=Decimal("0"),
        metrics={
            "run_id": str(run.id),
            "trusted_source_id": str(run.trusted_source_id),
            "outcome": outcome,
            "dry_run": run.dry_run,
            "request_count": run.request_count,
            "downloaded_bytes": run.downloaded_bytes,
            "new_count": run.new_count,
            "changed_count": run.changed_count,
            "unchanged_count": run.unchanged_count,
            "duplicate_count": run.duplicate_count,
            "failure_count": run.failure_count,
            "paid_api_calls": 0,
            "auto_publish": 0,
        },
        idempotency_key=_sha256(f"trusted-source-ledger:{run.id}"),
    )


def _complete_dry_run(
    session: Session,
    user: User,
    lease: _RunLease,
    now: datetime,
) -> SourceWorkerResult:
    _set_worker_context(session, user)
    run = session.get(SourceCheckRun, lease.run_id)
    if run is None or run.status != "running":
        session.rollback()
        return SourceWorkerResult(status="lease_lost", run_id=lease.run_id)
    run.status = "dry_run_completed"
    run.finished_at = now
    run.heartbeat_at = now
    run.leased_until = None
    run.request_log = []
    session.add(_usage_ledger(run, outcome="dry_run"))
    session.commit()
    return SourceWorkerResult(status=run.status, run_id=run.id)


def _load_locked_run_and_source(
    session: Session,
    user: User,
    lease: _RunLease,
    now: datetime,
) -> tuple[SourceCheckRun, TrustedSource] | None:
    _set_worker_context(session, user)
    run = session.scalar(
        select(SourceCheckRun)
        .where(SourceCheckRun.id == lease.run_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if run is None or run.status != "running" or run.leased_until is None:
        session.rollback()
        return None
    if _as_utc(run.leased_until) != _as_utc(lease.leased_until) or _as_utc(run.leased_until) <= now:
        session.rollback()
        return None
    source = session.scalar(
        select(TrustedSource).where(TrustedSource.id == lease.trusted_source_id).with_for_update()
    )
    if source is None or source.tenant_id != lease.tenant_id:
        session.rollback()
        return None
    return run, source


def _apply_fetch_result(
    session: Session,
    user: User,
    lease: _RunLease,
    result: FetchBatchResult,
    now: datetime,
) -> SourceWorkerResult:
    locked = _load_locked_run_and_source(session, user, lease, now)
    if locked is None:
        return SourceWorkerResult(status="lease_lost", run_id=lease.run_id)
    run, source = locked
    company = session.get(Company, source.company_id)
    if company is None:
        session.rollback()
        return SourceWorkerResult(
            status="failed", run_id=lease.run_id, error_code="company_missing"
        )
    existing_rows = list(
        session.scalars(
            select(CandidateDocument)
            .where(CandidateDocument.trusted_source_id == source.id)
            .order_by(CandidateDocument.first_discovered_at.desc())
        )
    )
    latest_by_url: dict[str, CandidateDocument] = {}
    existing_hashes: set[str] = set()
    for existing in existing_rows:
        latest_by_url.setdefault(existing.canonical_url, existing)
        existing_hashes.add(existing.content_hash)

    new_count = 0
    changed_count = 0
    unchanged_count = len(result.unchanged_urls)
    duplicate_count = 0
    for unchanged_url in set(result.unchanged_urls):
        existing = latest_by_url.get(unchanged_url)
        if existing is not None:
            existing.last_observed_at = now
    for document in result.documents:
        latest = latest_by_url.get(document.canonical_url)
        if latest is not None and latest.content_hash == document.content_hash:
            latest.last_observed_at = now
            latest.link_health_status = document.link_health_status
            latest.http_status = document.http_status
            latest.etag = document.etag or latest.etag
            latest.last_modified = document.last_modified or latest.last_modified
            unchanged_count += 1
            continue
        if document.content_hash in existing_hashes:
            duplicate_count += 1
            continue
        change_type = "changed" if latest is not None else "new"
        candidate = CandidateDocument(
            tenant_id=source.tenant_id,
            company_id=source.company_id,
            trusted_source_id=source.id,
            discovery_run_id=run.id,
            previous_candidate_id=latest.id if latest is not None else None,
            canonical_url=document.canonical_url,
            title=document.title,
            published_at=document.published_at,
            first_discovered_at=now,
            last_observed_at=now,
            content_hash=document.content_hash,
            change_type=change_type,
            link_health_status=document.link_health_status,
            http_status=document.http_status,
            etag=document.etag,
            last_modified=document.last_modified,
            excerpt=document.excerpt,
            license_status=source.license_status,
            processing_status="pending",
            identity_status_at_discovery=company.identity_status,
            visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
            document_metadata=document.metadata,
            handoff_payload={},
        )
        session.add(candidate)
        existing_hashes.add(document.content_hash)
        latest_by_url[document.canonical_url] = candidate
        if change_type == "new":
            new_count += 1
        else:
            changed_count += 1

    for failure in result.failures:
        failed_url = failure.get("url")
        if not isinstance(failed_url, str):
            continue
        existing = latest_by_url.get(failed_url)
        if existing is not None and failure.get("http_status") == 404:
            existing.link_health_status = "broken"
            existing.http_status = 404
            existing.last_observed_at = now

    run.request_count = result.request_count
    run.downloaded_bytes = result.downloaded_bytes
    run.new_count = new_count
    run.changed_count = changed_count
    run.unchanged_count = unchanged_count
    run.duplicate_count = duplicate_count
    run.failure_count = len(result.failures)
    run.external_calls = result.request_count
    run.paid_api_calls = 0
    run.input_tokens = 0
    run.output_tokens = 0
    run.estimated_cost = Decimal("0")
    run.robots_status = result.robots_status
    run.request_log = result.request_log
    run.status = "partial" if result.partial or result.failures else "completed"
    run.error_code = "partial_failures" if result.failures else None
    run.error_message = None
    run.finished_at = now
    run.heartbeat_at = now
    run.leased_until = None
    source.last_checked_at = now
    source.last_success_at = now
    source.last_failure_code = run.error_code
    source.last_http_status = None
    source.consecutive_failures = 0
    source.last_etag = result.start_etag or source.last_etag
    source.last_modified = result.start_last_modified or source.last_modified
    source.last_content_hash = result.start_content_hash or source.last_content_hash
    session.add(_usage_ledger(run, outcome=run.status))
    session.commit()
    return SourceWorkerResult(
        status=run.status,
        run_id=run.id,
        request_count=run.request_count,
        downloaded_bytes=run.downloaded_bytes,
        new_count=run.new_count,
        changed_count=run.changed_count,
        unchanged_count=run.unchanged_count,
        duplicate_count=run.duplicate_count,
        failure_count=run.failure_count,
        external_calls=run.external_calls,
    )


def _record_fetch_failure(
    session: Session,
    user: User,
    lease: _RunLease,
    fetcher: TrustedSourceFetcher,
    error: SourceFetchError,
    now: datetime,
) -> SourceWorkerResult:
    locked = _load_locked_run_and_source(session, user, lease, now)
    if locked is None:
        return SourceWorkerResult(status="lease_lost", run_id=lease.run_id)
    run, source = locked
    run.status = "failed"
    run.request_count = fetcher.request_count
    run.downloaded_bytes = fetcher.downloaded_bytes
    run.failure_count = 1
    run.external_calls = fetcher.request_count
    run.request_log = fetcher.request_log
    run.error_code = error.code
    run.error_message = str(error)[:2000]
    run.finished_at = now
    run.heartbeat_at = now
    run.leased_until = None
    source.last_checked_at = now
    source.last_failure_code = error.code
    source.last_http_status = error.http_status
    source.consecutive_failures += 1
    session.add(_usage_ledger(run, outcome="failed"))
    session.commit()
    return SourceWorkerResult(
        status="failed",
        run_id=run.id,
        request_count=run.request_count,
        downloaded_bytes=run.downloaded_bytes,
        failure_count=1,
        external_calls=run.external_calls,
        error_code=run.error_code,
    )


def run_trusted_source_worker_once(
    session: Session,
    user: User,
    settings: Settings,
    *,
    now: datetime | None = None,
    fetcher_factory: Callable[[SourceMonitoringPolicy], TrustedSourceFetcher] | None = None,
) -> SourceWorkerResult:
    _require_platform_admin(session, user)
    checked_at = _as_utc(now or utc_now())
    lease = _lease_next_run(session, user, settings.source_monitoring_policy, checked_at)
    if lease is None:
        return SourceWorkerResult(status="idle")
    if lease.dry_run:
        return _complete_dry_run(session, user, lease, checked_at)
    run_policy = replace(
        settings.source_monitoring_policy,
        version=lease.policy_version,
        max_requests_per_run=lease.max_requests,
        max_download_bytes_per_run=lease.max_download_bytes,
        max_response_bytes=lease.max_response_bytes,
        timeout_seconds=lease.timeout_seconds,
        retry_limit=lease.retry_limit,
        max_redirects=lease.max_redirects,
    )
    if not settings.external_calls_enabled or not settings.trusted_source_calls_enabled:
        fetcher = TrustedSourceFetcher(run_policy)
        try:
            return _record_fetch_failure(
                session,
                user,
                lease,
                fetcher,
                SourceFetchError(
                    "external_calls_disabled",
                    "EXTERNAL_CALLS_ENABLED and TRUSTED_SOURCE_CALLS_ENABLED are required",
                ),
                checked_at,
            )
        finally:
            fetcher.close()

    _set_worker_context(session, user)
    source = session.scalar(
        select(TrustedSource).where(
            TrustedSource.id == lease.trusted_source_id,
            TrustedSource.tenant_id == user.tenant_id,
        )
    )
    if source is None:
        session.rollback()
        return SourceWorkerResult(status="failed", run_id=lease.run_id, error_code="source_missing")
    conditional_state = _latest_candidate_state(session, user, source)
    factory = fetcher_factory or TrustedSourceFetcher
    fetcher = factory(run_policy)
    try:
        try:
            result = fetcher.check(
                source_type=source.source_type,
                root_domain=source.root_domain,
                start_url=source.start_url,
                list_path_prefix=source.list_path_prefix,
                retention_policy=source.content_retention_policy,
                conditional_state=conditional_state,
            )
        except SourceFetchError as error:
            completed_at = checked_at if now is not None else _as_utc(utc_now())
            return _record_fetch_failure(session, user, lease, fetcher, error, completed_at)
        completed_at = checked_at if now is not None else _as_utc(utc_now())
        return _apply_fetch_result(session, user, lease, result, completed_at)
    finally:
        fetcher.close()
