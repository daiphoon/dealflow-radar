from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from backend.app.config import PersonalEntitlementPolicy, RefreshPolicy
from backend.app.models import (
    PLATFORM_SHARED_SCOPE,
    Company,
    CompanyAlias,
    CompanyResearchJob,
    CompanySnapshot,
    Event,
    PersonalCompanyReport,
    PersonalCompanyRequest,
    PersonalCompanyViewState,
    PersonalEventViewReceipt,
    PersonalQuotaIncreaseRequest,
    PersonalUsageRecord,
    PersonalWatchlistItem,
    User,
    utc_now,
)
from backend.app.providers import validate_unified_credit_code
from backend.app.research_outcome import research_result
from backend.app.schemas import (
    EventOut,
    PersonalCompanyReportOut,
    PersonalCompanyReportSummaryOut,
    PersonalCompanyRequestOut,
    PersonalCompanyViewOut,
    PersonalQuotaIncreaseRequestOut,
    PersonalQuotaOut,
    PersonalUsageSummaryOut,
    PersonalWatchlistItemOut,
)
from backend.app.services import (
    current_shared_company_content,
    platform_shared_event_out,
    user_has_role,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_REPORT_VERSION = "personal-company-v3"
_FIRST_VIEW_LOOKBACK_DAYS = 90

_EVENT_TYPE_LABELS = {
    "financial_operation": "财务与经营",
    "financing_cap_table": "融资与股权",
    "contract_commercial": "合同与商业进展",
    "product_technology": "产品与技术",
    "governance_people": "治理与人员",
    "legal_compliance": "司法与合规",
    "capacity_assets": "产能与资产",
    "exit_liquidity": "退出与流动性",
    "information_quality": "信息质量",
}
_DIRECTION_LABELS = {
    "positive": "积极",
    "negative": "消极",
    "neutral": "中性",
    "mixed": "有利有弊",
    "unknown": "影响方向待确认",
}
_RISK_LABELS = {
    "none": "暂无显著风险",
    "low": "低风险",
    "moderate": "中等风险",
    "high": "高风险",
    "critical": "严重风险",
}
_FRESHNESS_LABELS = {
    "fresh": "数据较新",
    "stale": "数据可能已过期",
    "refreshing": "正在后台更新",
    "unknown": "尚未确认",
    "budget_deferred": "因预算限制暂缓更新",
}
_LINK_STATUS_LABELS = {
    "healthy": "链接正常",
    "unchecked": "尚未自动检查",
    "broken": "链接已失效",
    "unavailable": "当前无法访问",
}

_ACTIVE_REQUEST_STATUSES = {
    "pending",
    "in_review",
    "identity_queued",
    "identity_checking",
    "awaiting_confirmation",
    "research_queued",
    "researching",
    "partial",
    "budget_deferred",
    "cancel_requested",
}
_CANCELLABLE_REQUEST_STATUSES = _ACTIVE_REQUEST_STATUSES | {"needs_input"}
_REQUEST_STATUS_MESSAGES = {
    "pending": "已受理，等待后台核对公司名称和信用代码；无需上传营业执照。",
    "in_review": "等待平台定点核对资料；当前没有正在执行的身份查证任务。",
    "identity_queued": "工商主体核验排队中。",
    "identity_checking": "正在查找公开资料核对公司主体；关闭页面不会中断进度。",
    "awaiting_confirmation": "请确认本次查询对应的工商主体。",
    "needs_input": "名称或信用代码无法唯一一致定位，请核对后重新提交",
    "research_queued": "已进入受限公开网络研究队列；页面可关闭，进度会持续保存。",
    "researching": "正在按固定范围查找并核对公开资料；页面可关闭。",
    "partial": "已取得部分可复用资料，剩余步骤将在预算允许时继续。",
    "budget_deferred": "已保存当前进度，等待平台公开搜索预算恢复。",
    "cancel_requested": "已请求取消；当前网络步骤结束后不会继续新的外部调用。",
    "cancelled": "查询已取消，已经取得的可复用资料会按规则保留",
    "completed": "研究已经完成",
    "rejected": "申请未通过",
    "failed": "本次处理失败，没有继续调用外部服务",
}


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


def _daily_usage_period(now: datetime) -> str:
    return now.astimezone(_SHANGHAI).strftime("%Y-%m-%d")


def _daily_period_bounds(now: datetime) -> tuple[datetime, datetime]:
    local = now.astimezone(_SHANGHAI)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


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


def _usage_count(
    session: Session,
    user_id: UUID,
    operation: str,
    period_key: str,
    *,
    include_voided: bool = False,
) -> int:
    conditions = [
        PersonalUsageRecord.owner_user_id == user_id,
        PersonalUsageRecord.operation == operation,
        PersonalUsageRecord.period_key == period_key,
    ]
    if not include_voided:
        conditions.append(PersonalUsageRecord.voided_at.is_(None))
    return int(
        session.scalar(select(func.count()).select_from(PersonalUsageRecord).where(*conditions))
        or 0
    )


def _daily_request_count(session: Session, user_id: UUID, now: datetime) -> int:
    start, end = _daily_period_bounds(now)
    return int(
        session.scalar(
            select(func.count())
            .select_from(PersonalUsageRecord)
            .where(
                PersonalUsageRecord.owner_user_id == user_id,
                PersonalUsageRecord.operation == "company_request",
                PersonalUsageRecord.created_at >= start,
                PersonalUsageRecord.created_at < end,
            )
        )
        or 0
    )


def _active_quota_extras(
    session: Session,
    user_id: UUID,
    now: datetime,
) -> tuple[int, int]:
    rows = session.scalars(
        select(PersonalQuotaIncreaseRequest).where(
            PersonalQuotaIncreaseRequest.owner_user_id == user_id,
            PersonalQuotaIncreaseRequest.status == "approved",
            PersonalQuotaIncreaseRequest.effective_until.is_not(None),
            PersonalQuotaIncreaseRequest.effective_until > now,
        )
    )
    daily_extra = 0
    monthly_extra = 0
    for row in rows:
        daily_extra += row.approved_daily_extra
        monthly_extra += row.approved_monthly_extra
    return daily_extra, monthly_extra


def _effective_request_limits(
    session: Session,
    user: User,
    policy: PersonalEntitlementPolicy,
    now: datetime,
) -> tuple[int, int]:
    daily_extra, monthly_extra = _active_quota_extras(session, user.id, now)
    return policy.daily_request_limit + daily_extra, policy.monthly_request_limit + monthly_extra


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


def ensure_company_search_available(
    session: Session,
    user: User,
    policy: PersonalEntitlementPolicy,
) -> None:
    period_key = _usage_period(utc_now())
    if _usage_count(session, user.id, "company_search", period_key) >= policy.monthly_search_limit:
        raise PersonalFeatureLimitError("company_search", policy.monthly_search_limit)


def get_personal_usage_summary(
    session: Session,
    user: User,
    policy: PersonalEntitlementPolicy,
    *,
    now: datetime | None = None,
) -> PersonalUsageSummaryOut:
    checked_at = now or utc_now()
    period_key = _usage_period(checked_at)
    daily_period_key = _daily_usage_period(checked_at)
    search_count = _usage_count(session, user.id, "company_search", period_key)
    report_count = _usage_count(session, user.id, "company_report", period_key)
    request_count = _usage_count(session, user.id, "company_request", period_key)
    daily_request_count = _daily_request_count(session, user.id, checked_at)
    daily_request_limit, monthly_request_limit = _effective_request_limits(
        session,
        user,
        policy,
        checked_at,
    )
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
        daily_request_period_key=daily_period_key,
        searches=quota(search_count, policy.monthly_search_limit),
        watchlist_companies=quota(watchlist_count, policy.watchlist_company_limit),
        reports=quota(report_count, policy.monthly_report_limit),
        daily_company_requests=quota(daily_request_count, daily_request_limit),
        company_requests=quota(request_count, monthly_request_limit),
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
    if snapshot is None or snapshot.last_checked_at is None:
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
    monitor_policy=None,
) -> list[PersonalWatchlistItemOut]:
    from backend.app.watchlist_monitoring import monitoring_summary

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
                monitoring=monitoring_summary(session, company.id, monitor_policy),
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
    session: Session,
    request: PersonalCompanyRequest,
    *,
    reused: bool = False,
    now: datetime | None = None,
    include_internal_details: bool = False,
) -> PersonalCompanyRequestOut:
    research_job = (
        session.get(CompanyResearchJob, request.research_job_id)
        if request.research_job_id is not None
        else None
    )
    research_modules: dict[str, str] = {}
    if research_job is not None:
        coverage_modules = research_job.coverage.get("modules", {})
        if isinstance(coverage_modules, dict):
            for code, value in coverage_modules.items():
                if isinstance(value, str):
                    research_modules[str(code)] = value
                elif isinstance(value, dict):
                    status = value.get("status")
                    if isinstance(status, str):
                        research_modules[str(code)] = status
    queue_position: int | None = None
    if request.status in {"identity_queued", "budget_deferred"} and research_job is None:
        queue_position = int(
            session.scalar(
                select(func.count())
                .select_from(PersonalCompanyRequest)
                .where(
                    PersonalCompanyRequest.status.in_(["identity_queued", "budget_deferred"]),
                    or_(
                        PersonalCompanyRequest.created_at < request.created_at,
                        and_(
                            PersonalCompanyRequest.created_at == request.created_at,
                            PersonalCompanyRequest.id <= request.id,
                        ),
                    ),
                )
            )
            or 0
        )
    elif research_job is not None and research_job.status in {
        "queued",
        "partial",
        "budget_deferred",
    }:
        queue_position = int(
            session.scalar(
                select(func.count())
                .select_from(CompanyResearchJob)
                .where(
                    CompanyResearchJob.status.in_(["queued", "partial", "budget_deferred"]),
                    or_(
                        CompanyResearchJob.created_at < research_job.created_at,
                        and_(
                            CompanyResearchJob.created_at == research_job.created_at,
                            CompanyResearchJob.id <= research_job.id,
                        ),
                    ),
                )
            )
            or 0
        )
    status_message = _REQUEST_STATUS_MESSAGES[request.status]
    result = research_result(research_job) if request.status == "completed" else None
    if result is not None:
        status_message = result.message
    last_error_code = request.last_error_code
    identity_messages = {
        "identity_input_required": "请填写公司工商全称和有效的统一社会信用代码，无需上传营业执照。",
        "identity_conflict": "公开资料中的主体信息存在冲突，请核对名称和信用代码；尚未绑定公司。",
        "identity_evidence_missing": (
            "已查找公开资料，但暂不足以可靠核对主体；平台保留进度待补证，无需上传营业执照。"
        ),
        "identity_interrupted": (
            "查证步骤曾中断，已保存进度；平台确认调用结果后继续，避免重复消耗额度。"
        ),
        "identity_budget_deferred": "已保存主体查证进度，等待平台搜索预算恢复。",
        "identity_review_required": "该申请需要进一步核对主体，尚未绑定公司；无需上传营业执照。",
        "curator_identity_confirmed": "负责人已确认主体；业务更新尚待安排。",
    }
    if last_error_code in identity_messages and (
        last_error_code != "curator_identity_confirmed" or request.status == "in_review"
    ):
        status_message = identity_messages[last_error_code]
    retired_provider_result = request.last_error_code == "legacy_provider_retired"
    if retired_provider_result:
        status_message = "旧研究来源已停用，历史结果不再作为当前研究结果；可重新提交申请。"
    if request.last_error_code == "existing_private_company_requires_admin":
        if include_internal_details:
            status_message = "平台已有同一主体的受限档案，需管理员确认数据边界后才能继续。"
        else:
            status_message = "该申请需要进一步核验，处理完成后会更新结果。"
            last_error_code = "manual_review_required"
    return PersonalCompanyRequestOut(
        id=request.id,
        owner_user_id=request.owner_user_id,
        request_type=request.request_type,
        company_id=request.company_id,
        requested_name=request.requested_name,
        requested_credit_code=request.requested_credit_code,
        status=request.status,
        research_job_id=request.research_job_id,
        research_job_status=research_job.status if research_job is not None else None,
        research_modules=research_modules,
        research_result=result,
        queue_position=queue_position,
        resolved_legal_name=None if retired_provider_result else request.resolved_legal_name,
        resolved_credit_code=None if retired_provider_result else request.resolved_credit_code,
        resolved_registered_region=(
            None if retired_provider_result else request.resolved_registered_region
        ),
        resolved_registration_status=(
            None if retired_provider_result else request.resolved_registration_status
        ),
        resolved_registration_authority=(
            None if retired_provider_result else request.resolved_registration_authority
        ),
        identity_checked_at=None if retired_provider_result else request.identity_checked_at,
        confirmation_expires_at=request.confirmation_expires_at,
        confirmed_at=request.confirmed_at,
        external_calls=request.external_calls if include_internal_details else 0,
        cache_hits=request.cache_hits if include_internal_details else 0,
        cancelled_at=request.cancelled_at,
        cancellation_stage=request.cancellation_stage,
        cancellation_reason=request.cancellation_reason,
        last_error_code=last_error_code,
        can_confirm=False,
        can_cancel=request.status in _CANCELLABLE_REQUEST_STATUSES,
        status_message=status_message,
        reviewed_by_id=request.reviewed_by_id,
        reviewed_at=request.reviewed_at,
        decision_reason=request.decision_reason,
        created_at=request.created_at,
        updated_at=request.updated_at,
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
    outputs = [_request_out(session, request) for request in requests]
    current = current_shared_company_content(
        session, {request.company_id for request in outputs if request.company_id is not None}
    )
    for output in outputs:
        output.current_company_content = current.get(output.company_id)
    return outputs


def list_platform_company_requests(
    session: Session,
    user: User,
) -> list[PersonalCompanyRequestOut]:
    if not user_has_role(session, user.id, "platform_admin"):
        raise PersonalFeatureAccessError("platform admin required")
    requests = session.scalars(
        select(PersonalCompanyRequest).order_by(PersonalCompanyRequest.created_at.desc())
    )
    return [_request_out(session, request, include_internal_details=True) for request in requests]


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
    initial_status: str,
) -> PersonalCompanyRequestOut:
    now = utc_now()
    existing = _recent_request(session, user, target_key)
    if existing is not None:
        within_cooldown = (
            _aware_utc(existing.created_at) + timedelta(hours=policy.request_cooldown_hours) > now
        )
        if existing.status in _ACTIVE_REQUEST_STATUSES or within_cooldown:
            return _request_out(session, existing, reused=True, now=now)

    _lock_user(session, user)
    existing = _recent_request(session, user, target_key)
    if existing is not None:
        within_cooldown = (
            _aware_utc(existing.created_at) + timedelta(hours=policy.request_cooldown_hours) > now
        )
        if existing.status in _ACTIVE_REQUEST_STATUSES or within_cooldown:
            return _request_out(session, existing, reused=True, now=now)

    daily_limit, monthly_limit = _effective_request_limits(session, user, policy, now)
    if _daily_request_count(session, user.id, now) >= daily_limit:
        raise PersonalFeatureLimitError("daily_company_request", daily_limit)

    request = PersonalCompanyRequest(
        owner_user_id=user.id,
        request_type=request_type,
        company_id=company_id,
        requested_name=requested_name,
        requested_credit_code=requested_credit_code,
        target_key=target_key,
        status=initial_status,
    )
    session.add(request)
    session.flush()
    _record_usage(
        session,
        user,
        operation="company_request",
        limit=monthly_limit,
        resource_id=request.id,
        idempotency_key=_sha256(f"company-request:{request.id}"),
        now=now,
    )
    output = _request_out(session, request, now=now)
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
    if normalized_code:
        try:
            normalized_code = validate_unified_credit_code(normalized_code)
        except ValueError as error:
            raise PersonalRequestConflictError("invalid unified social credit code") from error
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
        initial_status="pending",
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
        initial_status="research_queued",
    )


def cancel_personal_company_request(
    session: Session,
    user: User,
    request_id: UUID,
    *,
    reason: str | None = None,
) -> PersonalCompanyRequestOut:
    now = utc_now()
    request = session.scalar(
        select(PersonalCompanyRequest)
        .where(
            PersonalCompanyRequest.id == request_id,
            PersonalCompanyRequest.owner_user_id == user.id,
        )
        .with_for_update()
    )
    if request is None:
        raise PersonalFeatureNotFoundError("request not found")
    if request.status == "cancelled":
        return _request_out(session, request, reused=True, now=now)
    if request.status not in _CANCELLABLE_REQUEST_STATUSES:
        raise PersonalRequestTransitionError("request can no longer be cancelled")

    prior_status = request.status
    request.cancel_requested_at = now
    request.cancellation_stage = prior_status
    if reason and reason.strip():
        request.cancellation_reason = reason.strip()

    research_job = (
        session.get(CompanyResearchJob, request.research_job_id)
        if request.research_job_id is not None
        else None
    )
    other_active_request_count = 0
    if research_job is not None:
        other_active_request_count = int(
            session.scalar(
                select(func.count())
                .select_from(PersonalCompanyRequest)
                .where(
                    PersonalCompanyRequest.research_job_id == research_job.id,
                    PersonalCompanyRequest.id != request.id,
                    PersonalCompanyRequest.status.in_(
                        ["research_queued", "researching", "partial", "budget_deferred"]
                    ),
                )
            )
            or 0
        )

    if prior_status == "identity_checking":
        request.status = "cancel_requested"
    elif research_job is not None and research_job.status == "running":
        if other_active_request_count == 0:
            request.status = "cancel_requested"
            research_job.cancel_requested_at = now
        else:
            request.status = "cancelled"
            request.cancelled_at = now
            request.leased_until = None
            request.heartbeat_at = None
    else:
        request.status = "cancelled"
        request.cancelled_at = now
        request.leased_until = None
        request.heartbeat_at = None
        if research_job is not None and other_active_request_count == 0:
            research_job.cancel_requested_at = now
            research_job.status = "cancelled"
            research_job.current_stage = "cancelled"
            research_job.cancelled_at = now
            research_job.leased_until = None
            research_job.heartbeat_at = None

    research_external_calls = 0
    if research_job is not None:
        research_external_calls = research_job.external_calls
    if request.status == "cancelled" and request.external_calls + research_external_calls == 0:
        usage = session.scalar(
            select(PersonalUsageRecord).where(
                PersonalUsageRecord.owner_user_id == user.id,
                PersonalUsageRecord.operation == "company_request",
                PersonalUsageRecord.resource_id == request.id,
                PersonalUsageRecord.voided_at.is_(None),
            )
        )
        if usage is not None:
            usage.voided_at = now
            usage.void_reason = "cancelled_before_external_call"

    output = _request_out(session, request, now=now)
    session.commit()
    return output


def _quota_increase_out(
    session: Session,
    request: PersonalQuotaIncreaseRequest,
) -> PersonalQuotaIncreaseRequestOut:
    owner = session.get(User, request.owner_user_id)
    if owner is None:
        raise PersonalFeatureNotFoundError("quota request owner not found")
    status = request.status
    if (
        status == "approved"
        and request.effective_until is not None
        and _aware_utc(request.effective_until) <= utc_now()
    ):
        status = "expired"
    return PersonalQuotaIncreaseRequestOut(
        id=request.id,
        owner_user_id=request.owner_user_id,
        owner_display_name=owner.display_name,
        owner_email=owner.email,
        requested_daily_extra=request.requested_daily_extra,
        requested_monthly_extra=request.requested_monthly_extra,
        request_reason=request.request_reason,
        status=status,
        approved_daily_extra=request.approved_daily_extra,
        approved_monthly_extra=request.approved_monthly_extra,
        effective_until=request.effective_until,
        reviewed_by_id=request.reviewed_by_id,
        reviewed_at=request.reviewed_at,
        decision_reason=request.decision_reason,
        created_at=request.created_at,
        updated_at=request.updated_at,
    )


def create_quota_increase_request(
    session: Session,
    user: User,
    *,
    requested_daily_extra: int,
    requested_monthly_extra: int,
    reason: str,
) -> PersonalQuotaIncreaseRequestOut:
    _lock_user(session, user)
    pending = session.scalar(
        select(PersonalQuotaIncreaseRequest).where(
            PersonalQuotaIncreaseRequest.owner_user_id == user.id,
            PersonalQuotaIncreaseRequest.status == "pending",
        )
    )
    if pending is not None:
        return _quota_increase_out(session, pending)
    request = PersonalQuotaIncreaseRequest(
        owner_user_id=user.id,
        requested_daily_extra=requested_daily_extra,
        requested_monthly_extra=requested_monthly_extra,
        request_reason=reason.strip(),
        status="pending",
        approved_daily_extra=0,
        approved_monthly_extra=0,
    )
    session.add(request)
    session.flush()
    output = _quota_increase_out(session, request)
    session.commit()
    return output


def list_personal_quota_increase_requests(
    session: Session,
    user: User,
) -> list[PersonalQuotaIncreaseRequestOut]:
    rows = session.scalars(
        select(PersonalQuotaIncreaseRequest)
        .where(PersonalQuotaIncreaseRequest.owner_user_id == user.id)
        .order_by(PersonalQuotaIncreaseRequest.created_at.desc())
    )
    return [_quota_increase_out(session, row) for row in rows]


def list_platform_quota_increase_requests(
    session: Session,
    user: User,
) -> list[PersonalQuotaIncreaseRequestOut]:
    if not user_has_role(session, user.id, "platform_admin"):
        raise PersonalFeatureAccessError("platform admin required")
    rows = session.scalars(
        select(PersonalQuotaIncreaseRequest).order_by(
            PersonalQuotaIncreaseRequest.created_at.desc()
        )
    )
    return [_quota_increase_out(session, row) for row in rows]


def decide_platform_quota_increase_request(
    session: Session,
    user: User,
    request_id: UUID,
    *,
    status: str,
    approved_daily_extra: int,
    approved_monthly_extra: int,
    effective_until: datetime | None,
    reason: str,
) -> PersonalQuotaIncreaseRequestOut:
    if not user_has_role(session, user.id, "platform_admin"):
        raise PersonalFeatureAccessError("platform admin required")
    request = session.scalar(
        select(PersonalQuotaIncreaseRequest)
        .where(PersonalQuotaIncreaseRequest.id == request_id)
        .with_for_update()
    )
    if request is None:
        raise PersonalFeatureNotFoundError("quota request not found")
    if request.status != "pending":
        raise PersonalRequestTransitionError("quota request is already closed")
    now = utc_now()
    if status == "approved":
        if effective_until is None or _aware_utc(effective_until) <= now:
            raise PersonalRequestTransitionError("approved quota expiry must be in the future")
        request.approved_daily_extra = approved_daily_extra
        request.approved_monthly_extra = approved_monthly_extra
        request.effective_until = effective_until
    else:
        request.approved_daily_extra = 0
        request.approved_monthly_extra = 0
        request.effective_until = None
    request.status = status
    request.reviewed_by_id = user.id
    request.reviewed_at = now
    request.decision_reason = reason.strip()
    output = _quota_increase_out(session, request)
    session.commit()
    return output


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
    if request.status not in {"pending", "in_review"}:
        raise PersonalRequestTransitionError(
            "active research requests cannot be closed through the manual review endpoint"
        )
    if request.last_error_code == "existing_private_company_requires_admin":
        raise PersonalRequestTransitionError(
            "private company collision requires a dedicated catalog decision"
        )
    request.status = status
    request.reviewed_by_id = user.id
    request.reviewed_at = utc_now()
    request.decision_reason = reason.strip()
    output = _request_out(session, request, include_internal_details=True)
    session.commit()
    return output


def approve_platform_company_request_for_research(
    session: Session,
    user: User,
    request_id: UUID,
    *,
    company_id: UUID | None,
    reason: str,
) -> PersonalCompanyRequestOut:
    if not user_has_role(session, user.id, "platform_admin"):
        raise PersonalFeatureAccessError("platform admin required")
    request = session.get(PersonalCompanyRequest, request_id)
    if request is None:
        raise PersonalFeatureNotFoundError("request not found")
    if request.request_type != "inclusion" or request.status not in {"pending", "in_review"}:
        raise PersonalRequestTransitionError("only pending inclusion requests can be approved")
    target_company_id = company_id or request.company_id
    shared_company_conditions = (
        Company.tenant_id.is_(None),
        Company.visibility_scope == "public",
        Company.identity_status == "verified",
        Company.credit_code.is_not(None),
    )
    company: Company | None = None
    if target_company_id is not None:
        company = session.scalar(
            select(Company).where(Company.id == target_company_id, *shared_company_conditions)
        )
    elif request.requested_credit_code:
        company = session.scalar(
            select(Company).where(
                Company.credit_code == request.requested_credit_code,
                *shared_company_conditions,
            )
        )
    elif request.requested_name:
        normalized_name = _normalized_identity_text(request.requested_name)
        candidates = list(
            session.scalars(
                select(Company)
                .outerjoin(CompanyAlias, CompanyAlias.company_id == Company.id)
                .where(
                    *shared_company_conditions,
                    or_(
                        Company.legal_name == request.requested_name.strip(),
                        and_(
                            CompanyAlias.visibility_scope == PLATFORM_SHARED_SCOPE,
                            CompanyAlias.owner_user_id.is_(None),
                            CompanyAlias.owner_tenant_id.is_(None),
                            CompanyAlias.verification_status == "verified",
                            CompanyAlias.normalized_alias == normalized_name,
                        ),
                    ),
                )
                .distinct()
                .limit(2)
            )
        )
        if len(candidates) == 1:
            company = candidates[0]
        elif len(candidates) > 1:
            raise PersonalRequestTransitionError("requested identity matches multiple companies")
    if company is None:
        raise PersonalRequestTransitionError(
            "research requires a verified company in the platform shared catalog"
        )
    if request.requested_credit_code and request.requested_credit_code != company.credit_code:
        raise PersonalRequestTransitionError("requested credit code does not match the company")
    if request.requested_name:
        normalized_name = _normalized_identity_text(request.requested_name)
        name_matches = normalized_name == _normalized_identity_text(company.legal_name)
        if not name_matches:
            name_matches = (
                session.scalar(
                    select(func.count())
                    .select_from(CompanyAlias)
                    .where(
                        CompanyAlias.company_id == company.id,
                        CompanyAlias.visibility_scope == PLATFORM_SHARED_SCOPE,
                        CompanyAlias.owner_user_id.is_(None),
                        CompanyAlias.owner_tenant_id.is_(None),
                        CompanyAlias.verification_status == "verified",
                        CompanyAlias.normalized_alias == normalized_name,
                    )
                )
                or 0
            ) > 0
        if not name_matches:
            raise PersonalRequestTransitionError("requested name does not match the company")
    now = utc_now()
    request.company_id = company.id
    request.resolved_legal_name = company.legal_name
    request.resolved_credit_code = company.credit_code
    request.resolved_registered_region = company.registered_region
    request.identity_checked_at = company.last_identity_checked_at
    request.confirmed_at = now
    request.status = "research_queued"
    request.reviewed_by_id = user.id
    request.reviewed_at = now
    request.decision_reason = reason.strip()
    request.last_error_code = None
    output = _request_out(session, request, include_internal_details=True)
    session.commit()
    return output


def _unseen_shared_events(
    session: Session,
    user: User,
    company_id: UUID,
    viewed_after: datetime | None = None,
) -> list[Event]:
    from backend.app.semantic_content import event_version
    from backend.app.services import _company_event_outputs

    rows = list(
        session.scalars(
            select(Event)
            .where(
                Event.company_id == company_id,
                Event.status == "published",
                Event.visibility_scope == PLATFORM_SHARED_SCOPE,
                Event.owner_user_id.is_(None),
                Event.owner_tenant_id.is_(None),
            )
            .order_by(Event.created_at, Event.id)
        )
    )
    receipts = {
        r.event_id: r.semantic_version
        for r in session.scalars(
            select(PersonalEventViewReceipt).where(
                PersonalEventViewReceipt.owner_user_id == user.id,
                PersonalEventViewReceipt.event_id.in_([e.id for e in rows]),
            )
        )
    }
    outputs = _company_event_outputs(session, rows, user, allow_organization_private=False)
    return [
        event
        for event, output in zip(rows, outputs)
        if event.id not in receipts or receipts[event.id] != event_version(output)
    ]


def record_personal_company_view(
    session: Session,
    user: User,
    company_id: UUID,
    *,
    rendered_versions: dict[UUID, str] | None = None,
) -> PersonalCompanyViewOut:
    if _shared_company(session, company_id) is None:
        raise PersonalFeatureNotFoundError("company not found")
    _lock_user(session, user)
    state = session.scalar(
        select(PersonalCompanyViewState)
        .where(
            PersonalCompanyViewState.owner_user_id == user.id,
            PersonalCompanyViewState.company_id == company_id,
        )
        .with_for_update()
    )
    first_view = state is None
    previous_viewed_at = state.last_viewed_at if state is not None else None
    unseen_events = _unseen_shared_events(session, user, company_id, previous_viewed_at)
    viewed_at = utc_now()
    window_start_at = previous_viewed_at or viewed_at - timedelta(days=_FIRST_VIEW_LOOKBACK_DAYS)
    event_outputs = [platform_shared_event_out(session, event, user) for event in unseen_events]
    event_outputs = [item for item in event_outputs if item.display_kind != "unconfirmed"]
    if first_view:
        event_outputs = [
            event
            for event in event_outputs
            if event.display_kind == "confirmed_change"
            and (
                event.occurred_on >= window_start_at.date()
                if event.occurred_on
                else _aware_utc(event.occurred_at or event.published_at or event.observed_at)
                >= _aware_utc(window_start_at)
            )
        ]
    from backend.app.semantic_content import event_version

    receipts = {
        r.event_id: r
        for r in session.scalars(
            select(PersonalEventViewReceipt).where(
                PersonalEventViewReceipt.owner_user_id == user.id,
                PersonalEventViewReceipt.event_id.in_([event.id for event in unseen_events]),
            )
        )
    }
    # 使用本次返回投影的版本；之后发生的 V2 不会被确认成 V1。
    delivered = {e.id: e for e in event_outputs}
    for event in unseen_events:
        if rendered_versions is not None and event.id not in rendered_versions:
            continue
        projected = delivered.get(event.id)
        if projected is None:
            # 首次打开的旧资料建立显式基线，保持既有不推送历史的语义。
            projected = platform_shared_event_out(session, event, user)
        version = (
            rendered_versions[event.id]
            if rendered_versions is not None
            else event_version(projected)
        )
        receipt = receipts.get(event.id)
        if receipt is None:
            session.add(
                PersonalEventViewReceipt(
                    owner_user_id=user.id,
                    event_id=event.id,
                    first_seen_at=viewed_at,
                    semantic_version=version,
                )
            )
        else:
            receipt.semantic_version = version
    if state is None:
        session.add(
            PersonalCompanyViewState(
                owner_user_id=user.id,
                company_id=company_id,
                last_viewed_at=viewed_at,
            )
        )
    else:
        state.last_viewed_at = viewed_at
    output = PersonalCompanyViewOut(
        company_id=company_id,
        first_view=first_view,
        previous_viewed_at=previous_viewed_at,
        window_start_at=window_start_at,
        viewed_at=viewed_at,
        new_events=event_outputs,
    )
    session.commit()
    return output


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _event_date_label(event: EventOut) -> str:
    if event.curated_versions:
        current = next(
            (v for v in event.curated_versions if v.is_current), event.curated_versions[0]
        )
        return f"{current.date_text or '未知'}（{current.date_precision}；{current.date_basis}）"
    if event.tender_observations:
        return event.occurred_on.strftime("%Y年%m月%d日") if event.occurred_on else "事件日期未知"
    if event.occurred_at is not None:
        return _aware_utc(event.occurred_at).astimezone(_SHANGHAI).strftime("%Y年%m月%d日")
    if event.published_on is not None:
        return event.published_on.strftime("%Y年%m月%d日")
    if event.published_at is not None:
        return _aware_utc(event.published_at).astimezone(_SHANGHAI).strftime("%Y年%m月%d日")
    return "日期未公开"


def _report_markdown(
    company: Company,
    snapshot: CompanySnapshot | None,
    events: list[EventOut],
    as_of: datetime,
    refresh_policy: RefreshPolicy,
) -> str:
    lines = [
        f"# {_single_line(company.legal_name)}",
        "",
        "> 本报告根据平台已经审核的信息生成，不包含投资建议、机构私有数据或未确认线索。",
        "",
        "## 工商主体身份",
        "",
        f"- 工商全称：{_single_line(company.legal_name)}",
        f"- 统一社会信用代码：{company.credit_code or '暂无可靠公开数据'}",
        f"- 注册地区：{company.registered_region or '暂无可靠公开数据'}",
        "- 工商主体身份："
        + (
            "负责人已确认（人工整理资料）"
            if company.identity_verification_basis == "curator_confirmed"
            else "公开资料已交叉核对（非官方登记核验）"
            if company.identity_verification_basis == "public_crosscheck"
            else (
                "已核验"
                if company.identity_status == "verified"
                and company.identity_verification_basis
                in {"official_government", "exchange_disclosure"}
                else "待核验"
            )
        ),
        f"- 报告生成时间：{as_of.astimezone(_SHANGHAI).strftime('%Y年%m月%d日 %H:%M')}",
        "",
        "## 已审核的重要信息",
        "",
    ]
    if not events:
        lines.append("暂无已审核的重要信息。")
    for event in events:
        curated = next((v for v in event.curated_versions if v.is_current), None)
        lines.extend(
            [
                f"### {_event_date_label(event)}｜{_single_line(event.title)}",
                "",
                f"- 分类：{_EVENT_TYPE_LABELS.get(event.event_type, '其他')}",
                f"- 方向：{_DIRECTION_LABELS.get(event.direction, '影响方向待确认')}",
                *(
                    [
                        "- 资料性质：人工整理、负责人已复核；本次导入未重新读取网页。",
                        f"- 人工确认时间：{curated.reviewed_at.isoformat()}",
                        f"- 资料基准日：{curated.as_of_date or '未知'}",
                        f"- 实际发生日期：{curated.occurred_date_text or '未知'}",
                        f"- 主体归属口径：{curated.subject_scope or '未说明'}",
                        f"- 原资料证据等级：{curated.source_grade}；{curated.content_support}",
                        "- 重要性、风险与置信度：尚未评价。",
                    ]
                    if curated
                    else [
                        f"- 风险级别：{_RISK_LABELS.get(event.risk_severity, '风险待确认')}",
                        f"- 重要性：{event.materiality_score}/100",
                        f"- 可信度：{event.confidence_score * 100:.0f}%",
                    ]
                ),
                "",
                event.summary.strip(),
                "",
                "证据引用：",
            ]
        )
        if event.fact_version:
            lines[-1:-1] = [
                *(
                    f"- {item['name']}：{item['value']}{item.get('unit') or ''}"
                    for item in event.facts
                ),
                f"- 事实版本：{event.fact_version}",
                "",
            ]
        current = next(
            (
                item
                for item in event.tender_observations
                if item.is_current and item.evidence_available
            ),
            None,
        )
        report_evidence = [
            item for item in event.evidence if current is None or item.id in current.evidence_ids
        ]
        if not report_evidence:
            lines.append("- 暂无允许展示的证据引用。")
        for evidence in report_evidence:
            source_name = _single_line(evidence.source_name)
            if evidence.link_display_allowed:
                url = evidence.final_url or evidence.canonical_url
                link_status = _LINK_STATUS_LABELS.get(evidence.url_health_status, "状态尚未确认")
                lines.append(f"- {source_name}：<{url}>（链接状态：{link_status}）")
            else:
                link_status = _LINK_STATUS_LABELS.get(evidence.url_health_status, "状态尚未确认")
                lines.append(f"- {source_name}（链接不开放；状态：{link_status}）")
        lines.append("")
    lines.extend(["## 数据状态与信息缺口", ""])
    if snapshot is None:
        lines.append("- 尚无可展示的数据概况。")
    else:
        freshness_status = _freshness_status(snapshot, refresh_policy, as_of)
        lines.append(f"- 数据更新状态：{_FRESHNESS_LABELS.get(freshness_status, '尚未确认')}")
        last_checked_label = (
            _aware_utc(snapshot.last_checked_at)
            .astimezone(_SHANGHAI)
            .strftime("%Y年%m月%d日 %H:%M")
            if snapshot.last_checked_at
            else "尚未联网检查"
        )
        lines.append(f"- 最后检查时间：{last_checked_label}")
        for gap in snapshot.information_gaps:
            lines.append(f"- 信息缺口：{_single_line(gap)}")
    lines.extend(
        [
            "",
            "---",
            "本报告记录生成时的信息；后续新增、纠正或撤回请以公司最新详情为准。",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _report_summary_out(
    report: PersonalCompanyReport,
) -> PersonalCompanyReportSummaryOut:
    return PersonalCompanyReportSummaryOut(
        id=report.id,
        company_id=report.company_id,
        company_legal_name=report.company_legal_name,
        report_version=report.report_version,
        title=report.title,
        as_of=report.as_of,
        content_hash=report.content_hash,
        source_event_count=len(report.source_event_ids),
        created_at=report.created_at,
    )


def _report_out(
    report: PersonalCompanyReport,
    *,
    reused: bool = False,
) -> PersonalCompanyReportOut:
    summary = _report_summary_out(report)
    return PersonalCompanyReportOut(
        **summary.model_dump(),
        markdown=report.markdown,
        source_event_ids=[UUID(event_id) for event_id in report.source_event_ids],
        reused=reused,
    )


def _safe_report_out(session, user, report, *, reused=False):
    from backend.app.models import EventEvidence, RawDocument, Source

    output = _report_out(report, reused=reused)
    ids = [UUID(value) for value in report.source_event_ids]
    rows = list(session.scalars(select(Event).where(Event.id.in_(ids))))
    evidence = list(session.scalars(select(EventEvidence).where(EventEvidence.event_id.in_(ids))))
    legacy = [e for e in evidence if e.display_license_status is None and not e.display_allowed]
    documents = {
        d.id: d
        for d in session.scalars(
            select(RawDocument).where(RawDocument.id.in_([e.raw_document_id for e in legacy]))
        )
    }
    sources = {
        s.id: s
        for s in session.scalars(
            select(Source).where(Source.id.in_([d.source_id for d in documents.values()]))
        )
    }

    def permission_lost(e):
        if e not in legacy:
            withdrawn_fact = any(
                row.id == e.event_id and row.status in {"rejected", "retracted"} for row in rows
            )
            return (
                not e.display_allowed and not withdrawn_fact
            ) or e.display_license_status not in {
                "public",
                "permission_confirmed",
            }
        document = documents.get(e.raw_document_id)
        source = sources.get(document.source_id) if document else None
        return (
            not document
            or not source
            or document.license_status != "public"
            or source.license_status != "public"
        )

    # 历史报告未保存逐片段许可快照；任何已撤销来源都保守停止再次发出全文。
    restricted = (
        len(rows) != len(ids)
        or any(e.visibility_scope != PLATFORM_SHARED_SCOPE for e in rows)
        or any(permission_lost(e) for e in evidence)
    )
    if restricted:
        output.markdown = "报告来源权限或状态已变化，历史正文停止在线提供，请查看公司最新资料。"
        output.history_status = "restricted"
    elif any(e.status in {"rejected", "retracted", "corrected"} for e in rows):
        output.history_status = "stale"
    return output


def create_personal_company_report(
    session: Session,
    user: User,
    policy: PersonalEntitlementPolicy,
    refresh_policy: RefreshPolicy,
    company_id: UUID,
    *,
    idempotency_key: str,
    archive_new_timepoint: bool = False,
) -> PersonalCompanyReportOut:
    company = _shared_company(session, company_id)
    if company is None:
        raise PersonalFeatureNotFoundError("company not found")
    _lock_user(session, user)
    from backend.app.models import PersonalReportRequest

    previous_request = session.scalar(
        select(PersonalReportRequest).where(
            PersonalReportRequest.owner_user_id == user.id,
            PersonalReportRequest.idempotency_key == idempotency_key,
        )
    )
    if previous_request is not None:
        report = session.get(PersonalCompanyReport, previous_request.report_id)
        if report.company_id != company_id:
            raise PersonalRequestConflictError("idempotency key belongs to another report")
        return _safe_report_out(session, user, report, reused=True)
    existing = session.scalar(
        select(PersonalCompanyReport).where(
            PersonalCompanyReport.owner_user_id == user.id,
            PersonalCompanyReport.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.company_id != company_id:
            raise PersonalRequestConflictError("idempotency key belongs to another report")
        return _safe_report_out(session, user, existing, reused=True)

    event_rows = list(
        session.scalars(
            select(Event)
            .where(
                Event.company_id == company_id,
                Event.status == "published",
                Event.visibility_scope == PLATFORM_SHARED_SCOPE,
                Event.owner_user_id.is_(None),
                Event.owner_tenant_id.is_(None),
            )
            .order_by(Event.occurred_at.desc(), Event.created_at.desc(), Event.id.asc())
        )
    )
    from backend.app.services import _company_event_outputs

    events = _company_event_outputs(session, event_rows, user, allow_organization_private=False)
    events = [event for event in events if event.display_kind != "unconfirmed"]
    snapshot = session.scalar(
        select(CompanySnapshot).where(
            CompanySnapshot.company_id == company_id,
            CompanySnapshot.is_current.is_(True),
            CompanySnapshot.visibility_scope == PLATFORM_SHARED_SCOPE,
            CompanySnapshot.owner_user_id.is_(None),
            CompanySnapshot.owner_tenant_id.is_(None),
        )
    )
    from backend.app.evidence_integrity import hash_canonical_object

    as_of = utc_now()
    markdown = _report_markdown(company, snapshot, events, as_of, refresh_policy)
    generated_at = f"- 报告生成时间：{as_of.astimezone(_SHANGHAI).strftime('%Y年%m月%d日 %H:%M')}"
    # 报告复用按实际正文判断；仅生成时点本身不构成内容变化。
    fingerprint = hash_canonical_object(
        {
            "template": _REPORT_VERSION,
            "company": str(company.id),
            "rendered_markdown": markdown.replace(generated_at, "- 报告生成时间：<生成时点>", 1),
        }
    )
    same = session.scalar(
        select(PersonalCompanyReport)
        .where(
            PersonalCompanyReport.owner_user_id == user.id,
            PersonalCompanyReport.company_id == company_id,
            PersonalCompanyReport.input_fingerprint == fingerprint,
        )
        .order_by(PersonalCompanyReport.created_at.desc())
        .limit(1)
    )
    if same is not None and not archive_new_timepoint:
        session.add(
            PersonalReportRequest(
                owner_user_id=user.id, report_id=same.id, idempotency_key=idempotency_key
            )
        )
        output = _safe_report_out(session, user, same, reused=True)
        session.commit()
        return output
    report = PersonalCompanyReport(
        owner_user_id=user.id,
        company_id=company.id,
        company_legal_name=company.legal_name,
        report_version=_REPORT_VERSION,
        idempotency_key=idempotency_key,
        input_fingerprint=fingerprint,
        title=f"{_single_line(company.legal_name)}信息报告",
        as_of=as_of,
        markdown=markdown,
        content_hash=_sha256(markdown),
        source_event_ids=[str(event.id) for event in events],
        created_at=as_of,
    )
    session.add(report)
    session.flush()
    _record_usage(
        session,
        user,
        operation="company_report",
        limit=policy.monthly_report_limit,
        resource_id=report.id,
        idempotency_key=_sha256(f"company-report:{report.id}"),
        now=as_of,
    )
    output = _report_out(report)
    session.commit()
    return output


def list_personal_company_reports(
    session: Session,
    user: User,
) -> list[PersonalCompanyReportSummaryOut]:
    reports = list(
        session.scalars(
            select(PersonalCompanyReport)
            .where(PersonalCompanyReport.owner_user_id == user.id)
            .order_by(PersonalCompanyReport.created_at.desc())
        )
    )
    return [_report_summary_out(report) for report in reports]


def get_personal_company_report(
    session: Session,
    user: User,
    report_id: UUID,
) -> PersonalCompanyReportOut:
    report = session.scalar(
        select(PersonalCompanyReport).where(
            PersonalCompanyReport.id == report_id,
            PersonalCompanyReport.owner_user_id == user.id,
        )
    )
    if report is None:
        raise PersonalFeatureNotFoundError("report not found")
    return _safe_report_out(session, user, report)
