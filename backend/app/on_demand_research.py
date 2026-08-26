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
    SYSTEM_RESTRICTED_SCOPE,
    Company,
    CompanyResearchJob,
    OfficialIdentityVerification,
    PersonalCompanyRequest,
    PersonalUsageRecord,
    RawDocument,
    Source,
    UsageLedger,
    User,
    utc_now,
)
from backend.app.services import user_has_role
from backend.app.tianyancha import (
    TianyanchaIdentityLookupResult,
    TianyanchaIdentityNeedsInputError,
    TianyanchaProviderError,
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
        return job, False
    job = CompanyResearchJob(
        company_id=company.id,
        created_by_user_id=request.owner_user_id,
        status="queued",
        current_stage="company_base",
        policy_version=policy.version,
        coverage={
            "identity": "completed",
            "modules": {module: "pending" for module in _MODULE_CODES},
            "automatic_publication": False,
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
    return OnDemandWorkerResult(status="idle")
