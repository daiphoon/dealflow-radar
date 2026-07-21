from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from backend.app.config import PersonalEntitlementPolicy, RefreshPolicy
from backend.app.models import (
    PLATFORM_SHARED_SCOPE,
    Company,
    CompanyAlias,
    CompanySnapshot,
    PersonalCompanyRequest,
    PersonalUsageRecord,
    PersonalWatchlistItem,
    User,
    utc_now,
)
from backend.app.schemas import (
    PersonalCompanyRequestOut,
    PersonalQuotaOut,
    PersonalUsageSummaryOut,
    PersonalWatchlistItemOut,
)
from backend.app.services import user_has_role

_SHANGHAI = ZoneInfo("Asia/Shanghai")


class PersonalFeatureLimitError(Exception):
    def __init__(self, feature: str, limit: int) -> None:
        self.feature = feature
        self.limit = limit
        super().__init__(f"{feature} limit reached")


class PersonalFeatureNotFoundError(Exception):
    pass


class PersonalRequestConflictError(Exception):
    pass


class PersonalRequestTransitionError(Exception):
    pass


class PersonalFeatureAccessError(Exception):
    pass


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _usage_period(now: datetime) -> str:
    return now.astimezone(_SHANGHAI).strftime("%Y-%m")


def _normalized_identity_text(value: str) -> str:
    return "".join(value.split()).casefold()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _lock_user(session: Session, user: User) -> None:
    locked_user = session.scalar(
        select(User).where(User.id == user.id, User.status == "active").with_for_update()
    )
    if locked_user is None:
        raise PersonalFeatureAccessError("inactive user")


def _usage_count(session: Session, user_id: UUID, operation: str, period_key: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(PersonalUsageRecord)
            .where(
                PersonalUsageRecord.owner_user_id == user_id,
                PersonalUsageRecord.operation == operation,
                PersonalUsageRecord.period_key == period_key,
            )
        )
        or 0
    )


def _record_usage(
    session: Session,
    user: User,
    *,
    operation: str,
    limit: int,
    resource_id: UUID | None = None,
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> PersonalUsageRecord:
    recorded_at = now or utc_now()
    period_key = _usage_period(recorded_at)
    _lock_user(session, user)
    if _usage_count(session, user.id, operation, period_key) >= limit:
        raise PersonalFeatureLimitError(operation, limit)
    record = PersonalUsageRecord(
        owner_user_id=user.id,
        operation=operation,
        period_key=period_key,
        resource_id=resource_id,
        idempotency_key=idempotency_key or _sha256(f"{operation}:{user.id}:{uuid4()}"),
        created_at=recorded_at,
    )
    session.add(record)
    session.flush()
    return record


def record_company_search(
    session: Session,
    user: User,
    policy: PersonalEntitlementPolicy,
) -> None:
    _record_usage(
        session,
        user,
        operation="company_search",
        limit=policy.monthly_search_limit,
    )


def get_personal_usage_summary(
    session: Session,
    user: User,
    policy: PersonalEntitlementPolicy,
    *,
    now: datetime | None = None,
) -> PersonalUsageSummaryOut:
    period_key = _usage_period(now or utc_now())
    search_count = _usage_count(session, user.id, "company_search", period_key)
    report_count = _usage_count(session, user.id, "company_report", period_key)
    request_count = _usage_count(session, user.id, "company_request", period_key)
    watchlist_count = int(
        session.scalar(
            select(func.count())
            .select_from(PersonalWatchlistItem)
            .where(PersonalWatchlistItem.owner_user_id == user.id)
        )
        or 0
    )

    def quota(used: int, limit: int) -> PersonalQuotaOut:
        return PersonalQuotaOut(used=used, limit=limit, remaining=max(limit - used, 0))

    return PersonalUsageSummaryOut(
        period_key=period_key,
        searches=quota(search_count, policy.monthly_search_limit),
        watchlist_companies=quota(watchlist_count, policy.watchlist_company_limit),
        reports=quota(report_count, policy.monthly_report_limit),
        company_requests=quota(request_count, policy.monthly_request_limit),
    )


def _shared_company(session: Session, company_id: UUID) -> Company | None:
    return session.scalar(
        select(Company).where(
            Company.id == company_id,
            Company.tenant_id.is_(None),
            Company.visibility_scope == "public",
            Company.identity_status == "verified",
        )
    )


def _freshness_status(
    snapshot: CompanySnapshot | None,
    policy: RefreshPolicy,
    now: datetime,
) -> str:
    if snapshot is None:
        return "unknown"
    if snapshot.freshness_status in {"unknown", "budget_deferred"}:
        return snapshot.freshness_status
    checked_at = _aware_utc(snapshot.last_checked_at)
    expires_at = checked_at + timedelta(days=policy.recent_query_ttl_days)
    return "stale" if expires_at <= now else "fresh"


def list_personal_watchlist(
    session: Session,
    user: User,
    refresh_policy: RefreshPolicy,
) -> list[PersonalWatchlistItemOut]:
    rows = list(
        session.execute(
            select(PersonalWatchlistItem, Company)
            .join(Company, Company.id == PersonalWatchlistItem.company_id)
            .where(
                PersonalWatchlistItem.owner_user_id == user.id,
                Company.tenant_id.is_(None),
                Company.visibility_scope == "public",
                Company.identity_status == "verified",
            )
            .order_by(PersonalWatchlistItem.created_at.desc())
        )
    )
    now = utc_now()
    items: list[PersonalWatchlistItemOut] = []
    for watchlist_item, company in rows:
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == company.id,
                CompanySnapshot.is_current.is_(True),
                CompanySnapshot.visibility_scope == PLATFORM_SHARED_SCOPE,
                CompanySnapshot.owner_user_id.is_(None),
                CompanySnapshot.owner_tenant_id.is_(None),
            )
        )
        items.append(
            PersonalWatchlistItemOut(
                id=watchlist_item.id,
                company_id=company.id,
                legal_name=company.legal_name,
                credit_code=company.credit_code,
                registered_region=company.registered_region,
                identity_status=company.identity_status,
                freshness_status=_freshness_status(snapshot, refresh_policy, now),
                last_checked_at=snapshot.last_checked_at if snapshot else None,
                followed_at=watchlist_item.created_at,
            )
        )
    return items


def add_personal_watchlist_item(
    session: Session,
    user: User,
    company_id: UUID,
    policy: PersonalEntitlementPolicy,
    refresh_policy: RefreshPolicy,
) -> PersonalWatchlistItemOut:
    company = _shared_company(session, company_id)
    if company is None:
        raise PersonalFeatureNotFoundError("company not found")
    _lock_user(session, user)
    existing = session.scalar(
        select(PersonalWatchlistItem).where(
            PersonalWatchlistItem.owner_user_id == user.id,
            PersonalWatchlistItem.company_id == company_id,
        )
    )
    if existing is None:
        count = int(
            session.scalar(
                select(func.count())
                .select_from(PersonalWatchlistItem)
                .where(PersonalWatchlistItem.owner_user_id == user.id)
            )
            or 0
        )
        if count >= policy.watchlist_company_limit:
            raise PersonalFeatureLimitError("watchlist_company", policy.watchlist_company_limit)
        existing = PersonalWatchlistItem(owner_user_id=user.id, company_id=company_id)
        session.add(existing)
        session.flush()
    snapshot = session.scalar(
        select(CompanySnapshot).where(
            CompanySnapshot.company_id == company.id,
            CompanySnapshot.is_current.is_(True),
            CompanySnapshot.visibility_scope == PLATFORM_SHARED_SCOPE,
            CompanySnapshot.owner_user_id.is_(None),
            CompanySnapshot.owner_tenant_id.is_(None),
        )
    )
    output = PersonalWatchlistItemOut(
        id=existing.id,
        company_id=company.id,
        legal_name=company.legal_name,
        credit_code=company.credit_code,
        registered_region=company.registered_region,
        identity_status=company.identity_status,
        freshness_status=_freshness_status(snapshot, refresh_policy, utc_now()),
        last_checked_at=snapshot.last_checked_at if snapshot else None,
        followed_at=existing.created_at,
    )
    session.commit()
    return output


def remove_personal_watchlist_item(session: Session, user: User, company_id: UUID) -> None:
    session.execute(
        delete(PersonalWatchlistItem).where(
            PersonalWatchlistItem.owner_user_id == user.id,
            PersonalWatchlistItem.company_id == company_id,
        )
    )
    session.commit()


def _request_out(
    request: PersonalCompanyRequest,
    *,
    reused: bool = False,
) -> PersonalCompanyRequestOut:
    return PersonalCompanyRequestOut(
        id=request.id,
        owner_user_id=request.owner_user_id,
        request_type=request.request_type,
        company_id=request.company_id,
        requested_name=request.requested_name,
        requested_credit_code=request.requested_credit_code,
        status=request.status,
        reviewed_by_id=request.reviewed_by_id,
        reviewed_at=request.reviewed_at,
        decision_reason=request.decision_reason,
        created_at=request.created_at,
        reused=reused,
    )


def list_personal_company_requests(
    session: Session,
    user: User,
) -> list[PersonalCompanyRequestOut]:
    requests = session.scalars(
        select(PersonalCompanyRequest)
        .where(PersonalCompanyRequest.owner_user_id == user.id)
        .order_by(PersonalCompanyRequest.created_at.desc())
    )
    return [_request_out(request) for request in requests]


def list_platform_company_requests(
    session: Session,
    user: User,
) -> list[PersonalCompanyRequestOut]:
    if not user_has_role(session, user.id, "platform_admin"):
        raise PersonalFeatureAccessError("platform admin required")
    requests = session.scalars(
        select(PersonalCompanyRequest).order_by(PersonalCompanyRequest.created_at.desc())
    )
    return [_request_out(request) for request in requests]


def _recent_request(
    session: Session,
    user: User,
    target_key: str,
) -> PersonalCompanyRequest | None:
    return session.scalar(
        select(PersonalCompanyRequest)
        .where(
            PersonalCompanyRequest.owner_user_id == user.id,
            PersonalCompanyRequest.target_key == target_key,
        )
        .order_by(PersonalCompanyRequest.created_at.desc())
        .limit(1)
    )


def _create_company_request(
    session: Session,
    user: User,
    policy: PersonalEntitlementPolicy,
    *,
    request_type: str,
    company_id: UUID | None,
    requested_name: str | None,
    requested_credit_code: str | None,
    target_key: str,
) -> PersonalCompanyRequestOut:
    now = utc_now()
    existing = _recent_request(session, user, target_key)
    if existing is not None:
        within_cooldown = (
            _aware_utc(existing.created_at) + timedelta(hours=policy.request_cooldown_hours) > now
        )
        if existing.status in {"pending", "in_review"} or within_cooldown:
            return _request_out(existing, reused=True)

    _lock_user(session, user)
    existing = _recent_request(session, user, target_key)
    if existing is not None:
        within_cooldown = (
            _aware_utc(existing.created_at) + timedelta(hours=policy.request_cooldown_hours) > now
        )
        if existing.status in {"pending", "in_review"} or within_cooldown:
            return _request_out(existing, reused=True)

    request = PersonalCompanyRequest(
        owner_user_id=user.id,
        request_type=request_type,
        company_id=company_id,
        requested_name=requested_name,
        requested_credit_code=requested_credit_code,
        target_key=target_key,
        status="pending",
    )
    session.add(request)
    session.flush()
    _record_usage(
        session,
        user,
        operation="company_request",
        limit=policy.monthly_request_limit,
        resource_id=request.id,
        idempotency_key=_sha256(f"company-request:{request.id}"),
        now=now,
    )
    output = _request_out(request)
    session.commit()
    return output


def create_inclusion_request(
    session: Session,
    user: User,
    policy: PersonalEntitlementPolicy,
    *,
    company_name: str | None,
    credit_code: str | None,
) -> PersonalCompanyRequestOut:
    normalized_name = company_name.strip() if company_name else None
    normalized_code = credit_code.strip().upper() if credit_code else None
    if not normalized_name and not normalized_code:
        raise PersonalRequestConflictError("company identifier required")
    identity_clauses = []
    if normalized_code:
        identity_clauses.append(Company.credit_code == normalized_code)
    if normalized_name:
        identity_clauses.append(Company.legal_name == normalized_name)
        alias_company_ids = select(CompanyAlias.company_id).where(
            CompanyAlias.visibility_scope == PLATFORM_SHARED_SCOPE,
            CompanyAlias.owner_user_id.is_(None),
            CompanyAlias.owner_tenant_id.is_(None),
            CompanyAlias.verification_status == "verified",
            CompanyAlias.normalized_alias == _normalized_identity_text(normalized_name),
        )
        identity_clauses.append(Company.id.in_(alias_company_ids))
    matching_company = session.scalar(
        select(Company).where(
            Company.tenant_id.is_(None),
            Company.visibility_scope == "public",
            Company.identity_status == "verified",
            or_(*identity_clauses),
        )
    )
    if matching_company is not None:
        raise PersonalRequestConflictError("company already available")
    target_key = (
        f"credit:{normalized_code}"
        if normalized_code
        else f"name:{_normalized_identity_text(normalized_name or '')}"
    )
    return _create_company_request(
        session,
        user,
        policy,
        request_type="inclusion",
        company_id=None,
        requested_name=normalized_name,
        requested_credit_code=normalized_code,
        target_key=target_key,
    )


def create_refresh_request(
    session: Session,
    user: User,
    policy: PersonalEntitlementPolicy,
    company_id: UUID,
) -> PersonalCompanyRequestOut:
    company = _shared_company(session, company_id)
    if company is None:
        raise PersonalFeatureNotFoundError("company not found")
    return _create_company_request(
        session,
        user,
        policy,
        request_type="refresh",
        company_id=company.id,
        requested_name=company.legal_name,
        requested_credit_code=company.credit_code,
        target_key=f"company:{company.id}",
    )


def decide_platform_company_request(
    session: Session,
    user: User,
    request_id: UUID,
    *,
    status: str,
    reason: str,
) -> PersonalCompanyRequestOut:
    if not user_has_role(session, user.id, "platform_admin"):
        raise PersonalFeatureAccessError("platform admin required")
    request = session.get(PersonalCompanyRequest, request_id)
    if request is None:
        raise PersonalFeatureNotFoundError("request not found")
    if request.status in {"completed", "rejected"}:
        raise PersonalRequestTransitionError("request already closed")
    request.status = status
    request.reviewed_by_id = user.id
    request.reviewed_at = utc_now()
    request.decision_reason = reason.strip()
    output = _request_out(request)
    session.commit()
    return output
