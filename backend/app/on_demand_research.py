from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.config import OnDemandResearchPolicy
from backend.app.database import set_request_context
from backend.app.models import (
    PLATFORM_SHARED_SCOPE,
    SYSTEM_RESTRICTED_SCOPE,
    Company,
    CompanyResearchJob,
    CompanySnapshot,
    Event,
    EventEvidence,
    OfficialIdentityVerification,
    PersonalCompanyRequest,
    PersonalUsageRecord,
    RawDocument,
    Source,
    UsageLedger,
    User,
    utc_now,
)
from backend.app.services import _refresh_company_snapshot, user_has_role
from backend.app.tianyancha import (
    TianyanchaIdentityLookupResult,
    TianyanchaIdentityNeedsInputError,
    TianyanchaProviderError,
    TianyanchaResearchModuleCode,
    TianyanchaResearchModuleResult,
    TianyanchaResearchRecord,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_TIANYANCHA_SOURCE_CODE = "tianyancha_licensed_business_data"
_TIANYANCHA_PROVIDER_PREFIX = "tianyancha"
_MODULE_CODES = (
    "company_base",
    "risk",
    "intellectual_property",
    "operation",
    "history",
    "executive",
)


class IdentityLookupProvider(Protocol):
    code: str
    external_calls: int
    cache_hits: int

    def lookup_cached_identity(
        self,
        *,
        company_name: str | None,
        credit_code: str | None,
    ) -> TianyanchaIdentityLookupResult | None: ...

    def lookup_identity(
        self,
        *,
        company_name: str | None,
        credit_code: str | None,
    ) -> TianyanchaIdentityLookupResult: ...

    def lookup_cached_research_module(
        self,
        *,
        module_code: TianyanchaResearchModuleCode,
        legal_name: str,
        credit_code: str,
        provider_company_id: str | None,
    ) -> TianyanchaResearchModuleResult | None: ...

    def lookup_research_module(
        self,
        *,
        module_code: TianyanchaResearchModuleCode,
        legal_name: str,
        credit_code: str,
        provider_company_id: str | None,
    ) -> TianyanchaResearchModuleResult: ...


class ExistingPrivateCompanyRequiresReviewError(RuntimeError):
    """Prevent a personal request from silently publishing a legacy private company."""


@dataclass(frozen=True)
class OnDemandWorkerResult:
    status: str
    request_id: UUID | None = None
    company_id: UUID | None = None
    research_job_id: UUID | None = None
    outcome: str | None = None
    external_calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: Decimal = Decimal("0")

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "request_id": str(self.request_id) if self.request_id else None,
            "company_id": str(self.company_id) if self.company_id else None,
            "research_job_id": str(self.research_job_id) if self.research_job_id else None,
            "outcome": self.outcome,
            "external_calls": self.external_calls,
            "cache_hits": self.cache_hits,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost": str(self.estimated_cost),
        }


@dataclass(frozen=True)
class _IdentityLease:
    request_id: UUID
    leased_until: datetime
    company_name: str | None
    credit_code: str | None


@dataclass(frozen=True)
class _ResearchLease:
    job_id: UUID
    company_id: UUID
    module_code: TianyanchaResearchModuleCode
    leased_until: datetime
    legal_name: str
    credit_code: str
    provider_company_id: str | None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_company_credit_code_conflict(error: IntegrityError) -> bool:
    constraint_name = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    return constraint_name == "companies_credit_code_key" or "companies.credit_code" in str(
        error.orig
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _period_bounds(now: datetime) -> tuple[datetime, datetime, datetime, datetime]:
    local = _as_utc(now).astimezone(_SHANGHAI)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    next_day = day_start + timedelta(days=1)
    month_start = day_start.replace(day=1)
    if month_start.month == 12:
        next_month = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month = month_start.replace(month=month_start.month + 1)
    return (
        day_start.astimezone(UTC),
        next_day.astimezone(UTC),
        month_start.astimezone(UTC),
        next_month.astimezone(UTC),
    )


def _provider_usage(
    session: Session,
    *,
    start: datetime,
    end: datetime,
) -> int:
    return int(
        session.scalar(
            select(func.coalesce(func.sum(UsageLedger.external_calls), 0)).where(
                UsageLedger.provider.like(f"{_TIANYANCHA_PROVIDER_PREFIX}%"),
                UsageLedger.created_at >= start,
                UsageLedger.created_at < end,
            )
        )
        or 0
    )


def _budget_deferral(
    session: Session,
    policy: OnDemandResearchPolicy,
    *,
    expected_calls: int,
    now: datetime,
) -> tuple[str, datetime] | None:
    day_start, next_day, month_start, next_month = _period_bounds(now)
    daily_used = _provider_usage(session, start=day_start, end=next_day)
    monthly_used = _provider_usage(session, start=month_start, end=next_month)
    if monthly_used + expected_calls > policy.effective_monthly_call_limit:
        return "provider_monthly_limit", next_month
    if daily_used + expected_calls > policy.effective_daily_call_limit:
        return "provider_daily_limit", next_day
    return None


def _record_provider_usage(
    session: Session,
    user: User,
    request: PersonalCompanyRequest,
    *,
    external_calls: int,
    cache_hits: int,
    outcome: str,
) -> None:
    session.add(
        UsageLedger(
            tenant_id=user.tenant_id,
            company_id=request.company_id,
            provider="tianyancha_licensed_identity",
            operation="on_demand_identity_lookup",
            external_calls=external_calls,
            input_tokens=0,
            output_tokens=0,
            estimated_cost=Decimal("0"),
            metrics={
                "request_id": str(request.id),
                "request_type": request.request_type,
                "outcome": outcome,
                "cache_hits": cache_hits,
                "automatic_publication": False,
            },
            idempotency_key=_sha256(f"on-demand-identity:{request.id}:{uuid4()}"),
        )
    )


def _restore_monthly_request_usage(
    session: Session,
    request: PersonalCompanyRequest,
) -> None:
    usage = session.scalar(
        select(PersonalUsageRecord).where(
            PersonalUsageRecord.owner_user_id == request.owner_user_id,
            PersonalUsageRecord.operation == "company_request",
            PersonalUsageRecord.resource_id == request.id,
        )
    )
    if usage is not None and usage.voided_at is not None:
        usage.voided_at = None
        usage.void_reason = None


def _void_monthly_request_usage(
    session: Session,
    request: PersonalCompanyRequest,
    now: datetime,
) -> None:
    usage = session.scalar(
        select(PersonalUsageRecord).where(
            PersonalUsageRecord.owner_user_id == request.owner_user_id,
            PersonalUsageRecord.operation == "company_request",
            PersonalUsageRecord.resource_id == request.id,
            PersonalUsageRecord.voided_at.is_(None),
        )
    )
    if usage is not None:
        usage.voided_at = now
        usage.void_reason = "cancelled_before_external_call"


def _claim_identity_request(
    session: Session,
    policy: OnDemandResearchPolicy,
    now: datetime,
) -> _IdentityLease | None:
    request = session.scalar(
        select(PersonalCompanyRequest)
        .where(
            or_(
                PersonalCompanyRequest.status == "identity_queued",
                and_(
                    PersonalCompanyRequest.status == "identity_checking",
                    or_(
                        PersonalCompanyRequest.leased_until.is_(None),
                        PersonalCompanyRequest.leased_until <= now,
                    ),
                ),
                and_(
                    PersonalCompanyRequest.status == "budget_deferred",
                    PersonalCompanyRequest.research_job_id.is_(None),
                    PersonalCompanyRequest.confirmed_at.is_(None),
                    or_(
                        PersonalCompanyRequest.leased_until.is_(None),
                        PersonalCompanyRequest.leased_until <= now,
                    ),
                ),
            )
        )
        .order_by(PersonalCompanyRequest.created_at, PersonalCompanyRequest.id)
        .with_for_update(skip_locked=True)
    )
    if request is None:
        session.rollback()
        return None
    leased_until = now + timedelta(seconds=policy.worker_lease_seconds)
    request.status = "identity_checking"
    request.leased_until = leased_until
    request.heartbeat_at = now
    request.last_error_code = None
    session.commit()
    return _IdentityLease(
        request_id=request.id,
        leased_until=leased_until,
        company_name=request.requested_name,
        credit_code=request.requested_credit_code,
    )


def _finish_stale_cancellation(session: Session, now: datetime) -> UUID | None:
    request = session.scalar(
        select(PersonalCompanyRequest)
        .where(
            PersonalCompanyRequest.status == "cancel_requested",
            PersonalCompanyRequest.research_job_id.is_(None),
            or_(
                PersonalCompanyRequest.leased_until.is_(None),
                PersonalCompanyRequest.leased_until <= now,
            ),
        )
        .order_by(PersonalCompanyRequest.cancel_requested_at, PersonalCompanyRequest.id)
        .with_for_update(skip_locked=True)
    )
    if request is None:
        session.rollback()
        return None
    request.status = "cancelled"
    request.cancelled_at = now
    request.leased_until = None
    request.heartbeat_at = now
    session.commit()
    return request.id


def _cancel_orphaned_research_job(
    session: Session,
    now: datetime,
) -> OnDemandWorkerResult | None:
    active_request = select(PersonalCompanyRequest.id).where(
        PersonalCompanyRequest.research_job_id == CompanyResearchJob.id,
        PersonalCompanyRequest.status.in_(
            ["research_queued", "researching", "partial", "budget_deferred"]
        ),
    )
    job = session.scalar(
        select(CompanyResearchJob)
        .where(
            CompanyResearchJob.status.in_(["queued", "running", "partial", "budget_deferred"]),
            ~active_request.exists(),
        )
        .order_by(CompanyResearchJob.created_at, CompanyResearchJob.id)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        session.rollback()
        return None
    job.cancel_requested_at = now
    job.heartbeat_at = now
    if job.status == "running":
        outcome = "orphaned_research_job_cancel_requested"
    else:
        job.status = "cancelled"
        job.cancelled_at = now
        job.leased_until = None
        outcome = "orphaned_research_job_cancelled"
    session.commit()
    return OnDemandWorkerResult(
        status="completed",
        company_id=job.company_id,
        research_job_id=job.id,
        outcome=outcome,
    )


def _lease_is_current(request: PersonalCompanyRequest, lease: _IdentityLease) -> bool:
    return request.leased_until is not None and _as_utc(request.leased_until) == _as_utc(
        lease.leased_until
    )


def _process_identity_request(
    session: Session,
    user: User,
    provider: IdentityLookupProvider,
    policy: OnDemandResearchPolicy,
    lease: _IdentityLease,
    now: datetime,
    *,
    provider_retry_limit: int,
) -> OnDemandWorkerResult:
    request = session.scalar(
        select(PersonalCompanyRequest)
        .where(PersonalCompanyRequest.id == lease.request_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if request is None or request.status not in {"identity_checking", "cancel_requested"}:
        session.rollback()
        return OnDemandWorkerResult(
            status="lease_lost",
            request_id=lease.request_id,
        )
    if not _lease_is_current(request, lease):
        session.rollback()
        return OnDemandWorkerResult(
            status="lease_lost",
            request_id=lease.request_id,
        )
    if request.status == "cancel_requested":
        request.status = "cancelled"
        request.cancelled_at = now
        request.leased_until = None
        request.heartbeat_at = now
        _void_monthly_request_usage(session, request, now)
        session.commit()
        return OnDemandWorkerResult(
            status="completed",
            request_id=request.id,
            outcome="cancelled_before_external_call",
        )

    calls_before = provider.external_calls
    cache_before = provider.cache_hits
    lookup: TianyanchaIdentityLookupResult | None = None
    outcome = "verified_candidate"
    error_code: str | None = None
    try:
        lookup = provider.lookup_cached_identity(
            company_name=lease.company_name,
            credit_code=lease.credit_code,
        )
    except TianyanchaIdentityNeedsInputError:
        outcome = "needs_credit_code"
        error_code = "identity_needs_credit_code"
    except TianyanchaProviderError:
        outcome = "provider_error"
        error_code = "identity_provider_error"

    if lookup is None and error_code is None:
        base_calls = 1 if lease.credit_code else 2
        expected_calls = base_calls * (provider_retry_limit + 1)
        deferral = _budget_deferral(
            session,
            policy,
            expected_calls=expected_calls,
            now=now,
        )
        if deferral is not None:
            reason, retry_at = deferral
            request.status = "budget_deferred"
            request.last_error_code = reason
            request.leased_until = retry_at
            request.heartbeat_at = now
            session.commit()
            return OnDemandWorkerResult(
                status="completed",
                request_id=request.id,
                outcome=reason,
            )

        worker_user_id = user.id
        worker_tenant_id = user.tenant_id
        session.commit()
        try:
            lookup = provider.lookup_identity(
                company_name=lease.company_name,
                credit_code=lease.credit_code,
            )
        except TianyanchaIdentityNeedsInputError:
            outcome = "needs_credit_code"
            error_code = "identity_needs_credit_code"
        except TianyanchaProviderError:
            outcome = "provider_error"
            error_code = "identity_provider_error"
        set_request_context(session, worker_user_id, worker_tenant_id)
    external_calls = provider.external_calls - calls_before
    cache_hits = provider.cache_hits - cache_before

    request = session.scalar(
        select(PersonalCompanyRequest)
        .where(PersonalCompanyRequest.id == lease.request_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if request is None:
        session.rollback()
        return OnDemandWorkerResult(
            status="lease_lost",
            request_id=lease.request_id,
            outcome=outcome,
            external_calls=external_calls,
            cache_hits=cache_hits,
        )
    if not _lease_is_current(request, lease):
        request.external_calls += external_calls
        request.cache_hits += cache_hits
        if external_calls > 0:
            _restore_monthly_request_usage(session, request)
        _record_provider_usage(
            session,
            user,
            request,
            external_calls=external_calls,
            cache_hits=cache_hits,
            outcome=f"lease_lost_after_{outcome}",
        )
        session.commit()
        return OnDemandWorkerResult(
            status="lease_lost",
            request_id=lease.request_id,
            outcome=outcome,
            external_calls=external_calls,
            cache_hits=cache_hits,
        )
    was_cancel_requested = request.status == "cancel_requested"
    request.external_calls += external_calls
    request.cache_hits += cache_hits
    if external_calls > 0:
        _restore_monthly_request_usage(session, request)
    request.heartbeat_at = utc_now()
    request.leased_until = None
    request.last_error_code = error_code
    if lookup is not None:
        request.resolved_legal_name = lookup.legal_name
        request.resolved_credit_code = lookup.credit_code
        request.resolved_registered_region = lookup.registered_region
        request.resolved_registration_status = lookup.registration_status
        request.resolved_registration_authority = lookup.registration_authority
        request.provider_company_id = lookup.provider_company_id
        request.identity_response_hash = lookup.response_hash
        request.identity_checked_at = lookup.checked_at
        request.confirmation_expires_at = utc_now() + timedelta(
            hours=policy.identity_confirmation_ttl_hours
        )
    if was_cancel_requested:
        request.status = "cancelled"
        request.cancelled_at = utc_now()
        if external_calls == 0:
            _void_monthly_request_usage(session, request, utc_now())
    elif lookup is not None:
        request.status = "awaiting_confirmation"
    elif error_code == "identity_needs_credit_code":
        request.status = "needs_input"
    else:
        request.status = "failed"
    _record_provider_usage(
        session,
        user,
        request,
        external_calls=external_calls,
        cache_hits=cache_hits,
        outcome=outcome,
    )
    session.commit()
    return OnDemandWorkerResult(
        status="completed",
        request_id=request.id,
        outcome=("cancelled_after_identity" if request.status == "cancelled" else outcome),
        external_calls=external_calls,
        cache_hits=cache_hits,
    )


def _identity_source(session: Session) -> Source:
    source = session.scalar(select(Source).where(Source.code == _TIANYANCHA_SOURCE_CODE))
    if source is None:
        source = Source(
            code=_TIANYANCHA_SOURCE_CODE,
            name="天眼查授权工商数据",
            source_quality="B",
            license_status="permission_confirmed",
            base_url="https://www.tianyancha.com/",
        )
        session.add(source)
        session.flush()
    elif (
        source.name != "天眼查授权工商数据"
        or source.source_quality != "B"
        or source.license_status != "permission_confirmed"
        or (source.base_url or "").rstrip("/") != "https://www.tianyancha.com"
    ):
        raise RuntimeError("Tianyancha source configuration conflicts with the approved source")
    return source


def _ensure_shared_company(
    session: Session,
    request: PersonalCompanyRequest,
) -> Company:
    if not request.resolved_credit_code or not request.resolved_legal_name:
        raise RuntimeError("confirmed request is missing resolved identity")
    company = session.scalar(
        select(Company).where(Company.credit_code == request.resolved_credit_code).with_for_update()
    )
    if company is None:
        try:
            with session.begin_nested():
                company = Company(
                    tenant_id=None,
                    credit_code=request.resolved_credit_code,
                    legal_name=request.resolved_legal_name,
                    registered_region=request.resolved_registered_region,
                    official_website=None,
                    identity_status="verified",
                    identity_verification_basis="licensed_business_data",
                    last_identity_checked_at=request.identity_checked_at,
                    visibility_scope="public",
                )
                session.add(company)
                session.flush()
            return company
        except IntegrityError as error:
            if not _is_company_credit_code_conflict(error):
                raise
            company = session.scalar(
                select(Company)
                .where(
                    Company.credit_code == request.resolved_credit_code,
                    Company.tenant_id.is_(None),
                    Company.visibility_scope == "public",
                )
                .with_for_update()
            )
            if company is None:
                raise ExistingPrivateCompanyRequiresReviewError(
                    "an existing private company requires platform review before catalog promotion"
                ) from error

    if company.tenant_id is not None or company.visibility_scope != "public":
        raise ExistingPrivateCompanyRequiresReviewError(
            "an existing private company requires platform review before catalog promotion"
        )

    # A previous local name may be stale or simply wrong. The licensed current
    # identity can update the company master, but it cannot prove that the old
    # value was a verified former legal name and therefore must not publish it
    # as a searchable alias.
    company.legal_name = request.resolved_legal_name
    company.tenant_id = None
    company.visibility_scope = "public"
    company.identity_status = "verified"
    if company.identity_verification_basis is None:
        company.identity_verification_basis = "licensed_business_data"
    if request.resolved_registered_region:
        company.registered_region = request.resolved_registered_region
    if request.identity_checked_at is not None:
        current = company.last_identity_checked_at
        if current is None or _as_utc(request.identity_checked_at) > _as_utc(current):
            company.last_identity_checked_at = request.identity_checked_at
    session.flush()
    return company


def _persist_identity_evidence(
    session: Session,
    user: User,
    request: PersonalCompanyRequest,
    company: Company,
    source: Source,
) -> None:
    if (
        not request.resolved_credit_code
        or not request.resolved_legal_name
        or not request.resolved_registration_status
        or not request.identity_response_hash
        or not request.identity_checked_at
    ):
        raise RuntimeError("confirmed request is missing identity evidence fields")
    payload = {
        "query_text": request.requested_credit_code or request.requested_name,
        "legal_name": request.resolved_legal_name,
        "credit_code": request.resolved_credit_code,
        "registered_region": request.resolved_registered_region,
        "registration_status": request.resolved_registration_status,
        "registration_authority": request.resolved_registration_authority,
        "provider_company_id": request.provider_company_id,
        "checked_at": request.identity_checked_at.isoformat(),
        "response_hash": request.identity_response_hash,
        "policy_version": "licensed-business-on-demand-v1",
    }
    content_hash = _sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    external_record_id = (
        f"on-demand-identity:{request.resolved_credit_code}:{request.identity_response_hash[:16]}"
    )
    document = session.scalar(
        select(RawDocument).where(
            RawDocument.source_id == source.id,
            RawDocument.visibility_scope == SYSTEM_RESTRICTED_SCOPE,
            RawDocument.external_record_id == external_record_id,
        )
    )
    if document is None:
        canonical_url = (
            f"https://www.tianyancha.com/company/{request.provider_company_id}"
            if request.provider_company_id
            else "https://www.tianyancha.com/"
        )
        document = RawDocument(
            source_id=source.id,
            research_import_id=None,
            candidate_document_id=None,
            visibility_scope=SYSTEM_RESTRICTED_SCOPE,
            owner_user_id=None,
            owner_tenant_id=None,
            external_record_id=external_record_id,
            canonical_url=canonical_url,
            title=f"授权工商身份核验：{request.resolved_legal_name}",
            published_at=None,
            published_on=None,
            observed_at=request.identity_checked_at,
            content_hash=content_hash,
            document_dedupe_key=_sha256(f"{source.id}:{external_record_id}"),
            license_status="permission_confirmed",
            payload=payload,
        )
        session.add(document)
        session.flush()
    elif document.content_hash != content_hash:
        raise RuntimeError("identity evidence record conflicts with an existing document")

    existing_verification = session.scalar(
        select(OfficialIdentityVerification).where(
            OfficialIdentityVerification.raw_document_id == document.id
        )
    )
    if existing_verification is None:
        session.add(
            OfficialIdentityVerification(
                tenant_id=user.tenant_id,
                company_id=company.id,
                raw_document_id=document.id,
                query_text=request.requested_credit_code
                or request.requested_name
                or request.resolved_credit_code,
                legal_name=request.resolved_legal_name,
                credit_code=request.resolved_credit_code,
                registered_region=request.resolved_registered_region,
                registration_status=request.resolved_registration_status,
                verification_status="verified",
                verification_basis="licensed_business_data",
                match_rule="licensed_business_user_confirmed_exact",
                checked_at=request.identity_checked_at,
            )
        )


def _active_research_job(session: Session, company_id: UUID) -> CompanyResearchJob | None:
    return session.scalar(
        select(CompanyResearchJob).where(
            CompanyResearchJob.company_id == company_id,
            CompanyResearchJob.status.in_(["queued", "running", "partial", "budget_deferred"]),
        )
    )


def _create_or_reuse_research_job(
    session: Session,
    request: PersonalCompanyRequest,
    company: Company,
    policy: OnDemandResearchPolicy,
) -> tuple[CompanyResearchJob, bool]:
    job = _active_research_job(session, company.id)
    if job is not None:
        if job.status == "partial":
            coverage = dict(job.coverage)
            modules = dict(coverage.get("modules", {}))
            for module, value in modules.items():
                if isinstance(value, dict) and value.get("status") == "failed":
                    modules[module] = {**value, "status": "pending", "error_code": None}
                elif value == "failed":
                    modules[module] = {"status": "pending", "error_code": None}
            coverage["modules"] = modules
            job.coverage = coverage
            job.status = "queued"
            job.last_error_code = None
        return job, False
    job = CompanyResearchJob(
        company_id=company.id,
        created_by_user_id=request.owner_user_id,
        status="queued",
        current_stage="company_base",
        policy_version=policy.version,
        coverage={
            "identity": "completed",
            "modules": {
                module: {
                    "status": "pending",
                    "external_calls": 0,
                    "cache_hits": 0,
                    "records_created": 0,
                    "events_created": 0,
                    "error_code": None,
                }
                for module in _MODULE_CODES
            },
            "automatic_publication": False,
            "reports_generated": 0,
        },
        external_calls=0,
        input_tokens=0,
        output_tokens=0,
    )
    session.add(job)
    session.flush()
    return job, True


def _prepare_research_request(
    session: Session,
    user: User,
    policy: OnDemandResearchPolicy,
    now: datetime,
) -> OnDemandWorkerResult | None:
    request = session.scalar(
        select(PersonalCompanyRequest)
        .where(PersonalCompanyRequest.status == "research_queued")
        .order_by(PersonalCompanyRequest.confirmed_at, PersonalCompanyRequest.created_at)
        .with_for_update(skip_locked=True)
    )
    if request is None:
        session.rollback()
        return None
    if request.request_type == "refresh":
        company = session.get(Company, request.company_id) if request.company_id else None
        if company is None or company.tenant_id is not None or company.visibility_scope != "public":
            request.status = "failed"
            request.last_error_code = "shared_company_not_found"
            session.commit()
            return OnDemandWorkerResult(
                status="completed",
                request_id=request.id,
                outcome="shared_company_not_found",
            )
    else:
        if request.confirmed_at is None:
            request.status = "failed"
            request.last_error_code = "identity_not_confirmed"
            session.commit()
            return OnDemandWorkerResult(
                status="completed",
                request_id=request.id,
                outcome="identity_not_confirmed",
            )
        try:
            company = _ensure_shared_company(session, request)
        except ExistingPrivateCompanyRequiresReviewError:
            request.status = "in_review"
            request.last_error_code = "existing_private_company_requires_admin"
            request.leased_until = None
            request.heartbeat_at = now
            session.commit()
            return OnDemandWorkerResult(
                status="completed",
                request_id=request.id,
                outcome="existing_private_company_requires_admin",
            )
        source = _identity_source(session)
        _persist_identity_evidence(session, user, request, company, source)
        request.company_id = company.id

    job, created = _create_or_reuse_research_job(session, request, company, policy)
    request.research_job_id = job.id
    request.status = "researching"
    request.last_error_code = None
    request.leased_until = None
    request.heartbeat_at = now
    session.commit()
    return OnDemandWorkerResult(
        status="completed",
        request_id=request.id,
        company_id=company.id,
        research_job_id=job.id,
        outcome="research_job_queued" if created else "research_job_reused",
    )


def _module_status(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("status"), str):
        return str(value["status"])
    return "pending"


def _next_pending_module(coverage: dict[str, object]) -> TianyanchaResearchModuleCode | None:
    modules = coverage.get("modules", {})
    if not isinstance(modules, dict):
        return None
    for module in _MODULE_CODES:
        if _module_status(modules.get(module)) == "pending":
            return module  # type: ignore[return-value]
    return None


def _module_coverage(
    coverage: dict[str, object], module_code: TianyanchaResearchModuleCode
) -> dict[str, object]:
    modules = coverage.get("modules", {})
    value = modules.get(module_code) if isinstance(modules, dict) else None
    if isinstance(value, dict):
        return dict(value)
    return {
        "status": _module_status(value),
        "external_calls": 0,
        "cache_hits": 0,
        "records_created": 0,
        "events_created": 0,
        "error_code": None,
    }


def _set_module_coverage(
    job: CompanyResearchJob,
    module_code: TianyanchaResearchModuleCode,
    **changes: object,
) -> None:
    coverage = dict(job.coverage)
    modules = dict(coverage.get("modules", {}))
    current = _module_coverage(coverage, module_code)
    current.update(changes)
    modules[module_code] = current
    coverage["modules"] = modules
    job.coverage = coverage


def _active_research_request_count(session: Session, job_id: UUID) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(PersonalCompanyRequest)
            .where(
                PersonalCompanyRequest.research_job_id == job_id,
                PersonalCompanyRequest.status.in_(
                    ["research_queued", "researching", "partial", "budget_deferred"]
                ),
            )
        )
        or 0
    )


def _sync_linked_research_requests(
    session: Session,
    job: CompanyResearchJob,
    *,
    active_status: str,
    now: datetime,
) -> None:
    requests = list(
        session.scalars(
            select(PersonalCompanyRequest).where(
                PersonalCompanyRequest.research_job_id == job.id,
                PersonalCompanyRequest.status.in_(
                    [
                        "research_queued",
                        "researching",
                        "partial",
                        "budget_deferred",
                        "cancel_requested",
                    ]
                ),
            )
        )
    )
    for request in requests:
        if request.status == "cancel_requested":
            request.status = "cancelled"
            request.cancelled_at = now
            request.leased_until = None
            request.heartbeat_at = now
            continue
        request.status = active_status
        request.last_error_code = job.last_error_code
        request.heartbeat_at = now


def _provider_company_id_for_job(session: Session, job_id: UUID) -> str | None:
    return session.scalar(
        select(PersonalCompanyRequest.provider_company_id)
        .where(
            PersonalCompanyRequest.research_job_id == job_id,
            PersonalCompanyRequest.provider_company_id.is_not(None),
        )
        .order_by(PersonalCompanyRequest.updated_at.desc())
        .limit(1)
    )


def _claim_research_job(
    session: Session,
    policy: OnDemandResearchPolicy,
    now: datetime,
) -> _ResearchLease | None:
    jobs = list(
        session.scalars(
            select(CompanyResearchJob)
            .where(
                CompanyResearchJob.status.in_(["queued", "running", "partial", "budget_deferred"]),
                or_(
                    CompanyResearchJob.leased_until.is_(None),
                    CompanyResearchJob.leased_until <= now,
                ),
            )
            .order_by(CompanyResearchJob.created_at, CompanyResearchJob.id)
            .with_for_update(skip_locked=True)
        )
    )
    for job in jobs:
        module_code = _next_pending_module(job.coverage)
        if module_code is None:
            continue
        if _active_research_request_count(session, job.id) == 0:
            continue
        company = session.get(Company, job.company_id)
        if (
            company is None
            or company.tenant_id is not None
            or company.visibility_scope != "public"
            or company.identity_status != "verified"
            or not company.credit_code
        ):
            job.status = "failed"
            job.last_error_code = "shared_verified_company_required"
            _sync_linked_research_requests(
                session,
                job,
                active_status="failed",
                now=now,
            )
            session.commit()
            return None
        leased_until = now + timedelta(seconds=policy.worker_lease_seconds)
        job.status = "running"
        job.current_stage = module_code
        job.leased_until = leased_until
        job.heartbeat_at = now
        job.last_error_code = None
        _set_module_coverage(
            job,
            module_code,
            status="running",
            started_at=now.isoformat(),
            error_code=None,
        )
        _sync_linked_research_requests(
            session,
            job,
            active_status="researching",
            now=now,
        )
        provider_company_id = _provider_company_id_for_job(session, job.id)
        session.commit()
        return _ResearchLease(
            job_id=job.id,
            company_id=company.id,
            module_code=module_code,
            leased_until=leased_until,
            legal_name=company.legal_name,
            credit_code=company.credit_code,
            provider_company_id=provider_company_id,
        )
    session.rollback()
    return None


def _research_lease_is_current(job: CompanyResearchJob, lease: _ResearchLease) -> bool:
    return (
        job.status == "running"
        and job.current_stage == lease.module_code
        and job.leased_until is not None
        and _as_utc(job.leased_until) == _as_utc(lease.leased_until)
    )


def _record_research_usage(
    session: Session,
    user: User,
    job: CompanyResearchJob,
    module_code: TianyanchaResearchModuleCode,
    *,
    external_calls: int,
    cache_hits: int,
    outcome: str,
) -> None:
    session.add(
        UsageLedger(
            tenant_id=user.tenant_id,
            company_id=job.company_id,
            provider="tianyancha_licensed_research",
            operation="on_demand_research_module",
            external_calls=external_calls,
            input_tokens=0,
            output_tokens=0,
            estimated_cost=Decimal("0"),
            metrics={
                "research_job_id": str(job.id),
                "module_code": module_code,
                "outcome": outcome,
                "cache_hits": cache_hits,
                "automatic_publication": False,
                "model_calls": 0,
                "reports_generated": 0,
            },
            idempotency_key=_sha256(f"on-demand-research:{job.id}:{module_code}:{uuid4()}"),
        )
    )


def _persist_research_record(
    session: Session,
    company: Company,
    source: Source,
    job: CompanyResearchJob,
    module_code: TianyanchaResearchModuleCode,
    result: TianyanchaResearchModuleResult,
    record: TianyanchaResearchRecord,
) -> tuple[bool, bool]:
    safe_payload = {
        "schema_version": "licensed-research-record-v1",
        "research_job_id": str(job.id),
        "module_code": module_code,
        "provider_tool": result.tool_name,
        "provider_record_id": record.external_record_id,
        "provider_response_hash": result.response_hash,
        "checked_at": result.checked_at.isoformat(),
        "title": record.title,
        "summary": record.summary,
        "facts": record.facts,
        "uncertainties": record.uncertainties,
        "classification": record.classification,
        "classification_reasons": record.classification_reasons,
        "policy_version": "licensed-research-display-v1",
    }
    dedupe_payload = {
        key: value
        for key, value in safe_payload.items()
        if key not in {"research_job_id", "checked_at"}
    }
    serialized_payload = json.dumps(
        dedupe_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    content_hash = _sha256(serialized_payload)
    external_record_id = f"research:{module_code}:{content_hash}"
    document_dedupe_key = _sha256(
        f"{source.id}:{company.id}:{module_code}:{record.external_record_id}:{content_hash}"
    )
    document = session.scalar(
        select(RawDocument).where(
            RawDocument.visibility_scope == SYSTEM_RESTRICTED_SCOPE,
            RawDocument.document_dedupe_key == document_dedupe_key,
        )
    )
    document_created = False
    if document is None:
        document = RawDocument(
            source_id=source.id,
            research_import_id=None,
            candidate_document_id=None,
            owner_user_id=None,
            owner_tenant_id=None,
            visibility_scope=SYSTEM_RESTRICTED_SCOPE,
            external_record_id=external_record_id,
            canonical_url=record.canonical_url,
            title=record.title,
            published_at=None,
            published_on=record.published_on,
            observed_at=result.checked_at,
            content_hash=content_hash,
            document_dedupe_key=document_dedupe_key,
            license_status="permission_confirmed",
            payload=safe_payload,
        )
        session.add(document)
        session.flush()
        document_created = True

    event_fingerprint = _sha256(
        f"{company.id}:{module_code}:{record.external_record_id}:{content_hash}"
    )
    event = session.scalar(
        select(Event).where(
            Event.company_id == company.id,
            Event.visibility_scope == PLATFORM_SHARED_SCOPE,
            Event.fingerprint_version == "tyc-v1",
            Event.event_fingerprint == event_fingerprint,
        )
    )
    event_created = False
    if event is None:
        is_verified = record.classification == "verified_fact"
        event = Event(
            company_id=company.id,
            owner_user_id=None,
            owner_tenant_id=None,
            visibility_scope=PLATFORM_SHARED_SCOPE,
            event_type=record.event_type,
            event_subtype=record.event_subtype,
            status="published" if is_verified else "candidate",
            direction=record.direction,
            materiality_score=record.materiality_score,
            risk_severity=record.risk_severity,
            confidence_score=record.confidence_score,
            source_quality="B",
            title=record.title,
            summary=record.summary,
            facts=record.facts,
            uncertainties=record.uncertainties,
            occurred_at=record.occurred_at,
            published_at=None,
            published_on=record.published_on,
            observed_at=result.checked_at,
            fingerprint_version="tyc-v1",
            event_fingerprint=event_fingerprint,
            publication_route=("licensed_structured_fact" if is_verified else "unconfirmed_lead"),
            publication_policy_version="licensed-research-display-v1",
            publication_reasons=[
                *record.classification_reasons,
                "subject_identity_verified",
                "licensed_source_record",
            ],
        )
        session.add(event)
        session.flush()
        event_created = True

    evidence = session.scalar(
        select(EventEvidence).where(
            EventEvidence.event_id == event.id,
            EventEvidence.raw_document_id == document.id,
            EventEvidence.span_hash == _sha256(record.evidence_excerpt),
        )
    )
    if evidence is None:
        session.add(
            EventEvidence(
                event_id=event.id,
                raw_document_id=document.id,
                source_event_evidence_id=None,
                owner_user_id=None,
                owner_tenant_id=None,
                visibility_scope=PLATFORM_SHARED_SCOPE,
                evidence_excerpt=record.evidence_excerpt,
                span_hash=_sha256(record.evidence_excerpt),
                support_type="supports",
                display_source_name=source.name,
                display_source_quality=source.source_quality,
                display_title=record.title,
                display_canonical_url=record.canonical_url,
                display_published_at=None,
                display_published_on=record.published_on,
                display_observed_at=result.checked_at,
                display_url_health_status="unchecked",
                display_url_http_status=None,
                display_url_checked_at=None,
                display_final_url=None,
                display_license_status="permission_confirmed",
                display_allowed=True,
            )
        )
    return document_created, event_created


def _update_shared_snapshot_for_research(
    session: Session,
    company: Company,
    coverage: dict[str, object],
) -> None:
    _refresh_company_snapshot(
        session,
        company,
        PLATFORM_SHARED_SCOPE,
        None,
        None,
    )
    snapshot = session.scalar(
        select(CompanySnapshot).where(
            CompanySnapshot.company_id == company.id,
            CompanySnapshot.visibility_scope == PLATFORM_SHARED_SCOPE,
            CompanySnapshot.owner_user_id.is_(None),
            CompanySnapshot.owner_tenant_id.is_(None),
            CompanySnapshot.is_current.is_(True),
        )
    )
    if snapshot is None:
        return
    modules = coverage.get("modules", {})
    no_data_labels = {
        "company_base": "工商与股东基础",
        "risk": "司法与合规风险",
        "intellectual_property": "知识产权",
        "operation": "经营与公示",
        "history": "历史变更",
        "executive": "董监高与人员",
    }
    module_gaps = [
        f"{no_data_labels[module]}：暂无可靠公开数据。"
        for module in _MODULE_CODES
        if isinstance(modules, dict) and _module_status(modules.get(module)) == "no_data"
    ]
    snapshot.information_gaps = list(dict.fromkeys([*snapshot.information_gaps, *module_gaps]))
    snapshot.summary = {
        **snapshot.summary,
        "research_modules": {
            module: _module_status(modules.get(module))
            for module in _MODULE_CODES
            if isinstance(modules, dict)
        },
    }


def _complete_research_module(
    session: Session,
    user: User,
    lease: _ResearchLease,
    result: TianyanchaResearchModuleResult,
    *,
    external_calls: int,
    cache_hits: int,
) -> OnDemandWorkerResult:
    job = session.scalar(
        select(CompanyResearchJob)
        .where(CompanyResearchJob.id == lease.job_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if job is None or not _research_lease_is_current(job, lease):
        session.rollback()
        return OnDemandWorkerResult(
            status="lease_lost",
            company_id=lease.company_id,
            research_job_id=lease.job_id,
            external_calls=external_calls,
            cache_hits=cache_hits,
        )
    company = session.get(Company, lease.company_id)
    if company is None:
        session.rollback()
        return OnDemandWorkerResult(
            status="lease_lost",
            company_id=lease.company_id,
            research_job_id=lease.job_id,
        )
    source = _identity_source(session)
    documents_created = 0
    events_created = 0
    for record in result.records:
        document_created, event_created = _persist_research_record(
            session,
            company,
            source,
            job,
            lease.module_code,
            result,
            record,
        )
        documents_created += int(document_created)
        events_created += int(event_created)

    job.external_calls += external_calls
    job.input_tokens += 0
    job.output_tokens += 0
    job.heartbeat_at = utc_now()
    job.leased_until = None
    job.last_error_code = None
    _set_module_coverage(
        job,
        lease.module_code,
        status="no_data" if result.no_reliable_data else "completed",
        checked_at=result.checked_at.isoformat(),
        response_hash=result.response_hash,
        provider_tool=result.tool_name,
        external_calls=external_calls,
        cache_hits=cache_hits,
        records_created=documents_created,
        events_created=events_created,
        warnings=result.warnings,
        error_code=None,
    )
    _record_research_usage(
        session,
        user,
        job,
        lease.module_code,
        external_calls=external_calls,
        cache_hits=cache_hits,
        outcome="no_reliable_data" if result.no_reliable_data else "completed",
    )

    if _active_research_request_count(session, job.id) == 0:
        job.status = "cancelled"
        job.cancelled_at = utc_now()
        job.current_stage = "cancelled"
        _sync_linked_research_requests(
            session,
            job,
            active_status="cancelled",
            now=utc_now(),
        )
        outcome = "cancelled_after_module"
    else:
        next_module = _next_pending_module(job.coverage)
        if next_module is not None:
            job.status = "queued"
            job.current_stage = next_module
            _sync_linked_research_requests(
                session,
                job,
                active_status="researching",
                now=utc_now(),
            )
            outcome = "module_completed"
        else:
            modules = job.coverage.get("modules", {})
            has_failure = isinstance(modules, dict) and any(
                _module_status(modules.get(module)) == "failed" for module in _MODULE_CODES
            )
            job.status = "partial" if has_failure else "completed"
            job.current_stage = "complete" if not has_failure else "partial"
            _sync_linked_research_requests(
                session,
                job,
                active_status="partial" if has_failure else "completed",
                now=utc_now(),
            )
            _update_shared_snapshot_for_research(session, company, job.coverage)
            outcome = "research_partial" if has_failure else "research_completed"
    session.commit()
    return OnDemandWorkerResult(
        status="completed",
        company_id=lease.company_id,
        research_job_id=lease.job_id,
        outcome=outcome,
        external_calls=external_calls,
        cache_hits=cache_hits,
    )


def _fail_research_module(
    session: Session,
    user: User,
    lease: _ResearchLease,
    *,
    error_code: str,
    external_calls: int,
    cache_hits: int,
) -> OnDemandWorkerResult:
    job = session.scalar(
        select(CompanyResearchJob)
        .where(CompanyResearchJob.id == lease.job_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if job is None or not _research_lease_is_current(job, lease):
        session.rollback()
        return OnDemandWorkerResult(status="lease_lost", research_job_id=lease.job_id)
    job.external_calls += external_calls
    job.leased_until = None
    job.heartbeat_at = utc_now()
    job.last_error_code = error_code
    _set_module_coverage(
        job,
        lease.module_code,
        status="failed",
        checked_at=utc_now().isoformat(),
        external_calls=external_calls,
        cache_hits=cache_hits,
        error_code=error_code,
    )
    _record_research_usage(
        session,
        user,
        job,
        lease.module_code,
        external_calls=external_calls,
        cache_hits=cache_hits,
        outcome=error_code,
    )
    next_module = _next_pending_module(job.coverage)
    if next_module is not None and _active_research_request_count(session, job.id) > 0:
        job.status = "queued"
        job.current_stage = next_module
        _sync_linked_research_requests(
            session,
            job,
            active_status="researching",
            now=utc_now(),
        )
        outcome = "module_failed_continuing"
    else:
        job.status = "partial"
        job.current_stage = "partial"
        _sync_linked_research_requests(
            session,
            job,
            active_status="partial",
            now=utc_now(),
        )
        outcome = "research_partial"
    session.commit()
    return OnDemandWorkerResult(
        status="completed",
        company_id=lease.company_id,
        research_job_id=lease.job_id,
        outcome=outcome,
        external_calls=external_calls,
        cache_hits=cache_hits,
    )


def _process_research_job(
    session: Session,
    user: User,
    provider: IdentityLookupProvider,
    policy: OnDemandResearchPolicy,
    lease: _ResearchLease,
    now: datetime,
    *,
    provider_retry_limit: int,
) -> OnDemandWorkerResult:
    calls_before = provider.external_calls
    cache_before = provider.cache_hits
    try:
        result = provider.lookup_cached_research_module(
            module_code=lease.module_code,
            legal_name=lease.legal_name,
            credit_code=lease.credit_code,
            provider_company_id=lease.provider_company_id,
        )
    except TianyanchaProviderError:
        result = None

    if result is None:
        job = session.scalar(
            select(CompanyResearchJob)
            .where(CompanyResearchJob.id == lease.job_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if job is None or not _research_lease_is_current(job, lease):
            session.rollback()
            return OnDemandWorkerResult(status="lease_lost", research_job_id=lease.job_id)
        expected_calls = provider_retry_limit + 1
        if job.external_calls + expected_calls > policy.max_provider_calls_per_company:
            return _fail_research_module(
                session,
                user,
                lease,
                error_code="company_provider_call_limit",
                external_calls=0,
                cache_hits=provider.cache_hits - cache_before,
            )
        deferral = _budget_deferral(
            session,
            policy,
            expected_calls=expected_calls,
            now=now,
        )
        if deferral is not None:
            reason, retry_at = deferral
            job.status = "budget_deferred"
            job.last_error_code = reason
            job.leased_until = retry_at
            job.heartbeat_at = now
            _set_module_coverage(job, lease.module_code, status="pending", error_code=reason)
            _sync_linked_research_requests(
                session,
                job,
                active_status="budget_deferred",
                now=now,
            )
            session.commit()
            return OnDemandWorkerResult(
                status="completed",
                company_id=lease.company_id,
                research_job_id=lease.job_id,
                outcome=reason,
                cache_hits=provider.cache_hits - cache_before,
            )
        worker_user_id = user.id
        worker_tenant_id = user.tenant_id
        session.commit()
        try:
            result = provider.lookup_research_module(
                module_code=lease.module_code,
                legal_name=lease.legal_name,
                credit_code=lease.credit_code,
                provider_company_id=lease.provider_company_id,
            )
        except TianyanchaProviderError:
            set_request_context(session, worker_user_id, worker_tenant_id)
            return _fail_research_module(
                session,
                user,
                lease,
                error_code="research_provider_error",
                external_calls=provider.external_calls - calls_before,
                cache_hits=provider.cache_hits - cache_before,
            )
        set_request_context(session, worker_user_id, worker_tenant_id)

    return _complete_research_module(
        session,
        user,
        lease,
        result,
        external_calls=provider.external_calls - calls_before,
        cache_hits=provider.cache_hits - cache_before,
    )


def validate_on_demand_worker_user(session: Session, user: User) -> None:
    if user.status != "active":
        raise RuntimeError("on-demand worker user must be active")
    required_roles = {"institution_admin", "platform_admin"}
    missing = [role for role in required_roles if not user_has_role(session, user.id, role)]
    if missing:
        raise RuntimeError(
            "on-demand worker user is missing required roles: " + ", ".join(sorted(missing))
        )


def run_on_demand_worker_once(
    session: Session,
    user: User,
    provider: IdentityLookupProvider,
    policy: OnDemandResearchPolicy,
    *,
    provider_retry_limit: int,
    research_calls_enabled: bool = False,
    now: datetime | None = None,
) -> OnDemandWorkerResult:
    checked_at = _as_utc(now or utc_now())
    worker_user_id = user.id
    worker_tenant_id = user.tenant_id
    set_request_context(session, worker_user_id, worker_tenant_id)
    cancelled_request_id = _finish_stale_cancellation(session, checked_at)
    if cancelled_request_id is not None:
        return OnDemandWorkerResult(
            status="completed",
            request_id=cancelled_request_id,
            outcome="stale_cancellation_completed",
        )
    set_request_context(session, worker_user_id, worker_tenant_id)
    orphaned_job = _cancel_orphaned_research_job(session, checked_at)
    if orphaned_job is not None:
        return orphaned_job
    set_request_context(session, worker_user_id, worker_tenant_id)
    lease = _claim_identity_request(session, policy, checked_at)
    if lease is not None:
        set_request_context(session, worker_user_id, worker_tenant_id)
        return _process_identity_request(
            session,
            user,
            provider,
            policy,
            lease,
            checked_at,
            provider_retry_limit=provider_retry_limit,
        )
    set_request_context(session, worker_user_id, worker_tenant_id)
    prepared = _prepare_research_request(session, user, policy, checked_at)
    if prepared is not None:
        return prepared
    if research_calls_enabled:
        set_request_context(session, worker_user_id, worker_tenant_id)
        research_lease = _claim_research_job(session, policy, checked_at)
        if research_lease is not None:
            set_request_context(session, worker_user_id, worker_tenant_id)
            return _process_research_job(
                session,
                user,
                provider,
                policy,
                research_lease,
                checked_at,
                provider_retry_limit=provider_retry_limit,
            )
    return OnDemandWorkerResult(status="idle")
