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
    Event,
    PersonalCompanyReport,
    PersonalCompanyRequest,
    PersonalCompanyViewState,
    PersonalEventViewReceipt,
    PersonalUsageRecord,
    PersonalWatchlistItem,
    User,
    utc_now,
)
from backend.app.schemas import (
    EventOut,
    PersonalCompanyReportOut,
    PersonalCompanyReportSummaryOut,
    PersonalCompanyRequestOut,
    PersonalCompanyViewOut,
    PersonalQuotaOut,
    PersonalUsageSummaryOut,
    PersonalWatchlistItemOut,
)
from backend.app.services import platform_shared_event_out, user_has_role

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_REPORT_VERSION = "personal-company-v1"


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


def _unseen_shared_events(
    session: Session,
    user: User,
    company_id: UUID,
) -> list[Event]:
    seen_event_ids = select(PersonalEventViewReceipt.event_id).where(
        PersonalEventViewReceipt.owner_user_id == user.id
    )
    return list(
        session.scalars(
            select(Event)
            .where(
                Event.company_id == company_id,
                Event.status == "published",
                Event.visibility_scope == PLATFORM_SHARED_SCOPE,
                Event.owner_user_id.is_(None),
                Event.owner_tenant_id.is_(None),
                ~Event.id.in_(seen_event_ids),
            )
            .order_by(Event.created_at.asc(), Event.id.asc())
        )
    )


def record_personal_company_view(
    session: Session,
    user: User,
    company_id: UUID,
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
    unseen_events = _unseen_shared_events(session, user, company_id)
    event_outputs = (
        []
        if first_view
        else [platform_shared_event_out(session, event, user) for event in unseen_events]
    )
    viewed_at = utc_now()
    session.add_all(
        [
            PersonalEventViewReceipt(
                owner_user_id=user.id,
                event_id=event.id,
                first_seen_at=viewed_at,
            )
            for event in unseen_events
        ]
    )
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
        viewed_at=viewed_at,
        new_events=event_outputs,
    )
    session.commit()
    return output


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _event_date_label(event: EventOut) -> str:
    if event.occurred_at is not None:
        return event.occurred_at.date().isoformat()
    if event.published_on is not None:
        return event.published_on.isoformat()
    if event.published_at is not None:
        return event.published_at.date().isoformat()
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
        "> 本报告由已审核的平台共享事实按固定模板生成，不含投资建议、机构私有数据或未确认线索。",
        "",
        "## 工商主体身份",
        "",
        f"- 工商全称：{_single_line(company.legal_name)}",
        f"- 统一社会信用代码：{company.credit_code or '暂无可靠公开数据'}",
        f"- 注册地区：{company.registered_region or '暂无可靠公开数据'}",
        f"- 工商主体身份：{'已核验' if company.identity_status == 'verified' else '待核验'}",
        f"- 报告生成时间：{as_of.astimezone(_SHANGHAI).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "",
        "## 已审核平台共享事件",
        "",
    ]
    if not events:
        lines.append("暂无已审核的平台共享事件。")
    for event in events:
        lines.extend(
            [
                f"### {_event_date_label(event)}｜{_single_line(event.title)}",
                "",
                f"- 分类：{event.event_type}",
                f"- 方向：{event.direction}",
                f"- 风险级别：{event.risk_severity}",
                f"- 重要性：{event.materiality_score}/100",
                f"- 可信度：{event.confidence_score}",
                "",
                event.summary.strip(),
                "",
                "证据引用：",
            ]
        )
        if not event.evidence:
            lines.append("- 暂无允许展示的证据引用。")
        for evidence in event.evidence:
            source_name = _single_line(evidence.source_name)
            if evidence.link_display_allowed:
                url = evidence.final_url or evidence.canonical_url
                lines.append(f"- {source_name}：<{url}>（链接状态：{evidence.url_health_status}）")
            else:
                lines.append(f"- {source_name}（链接不开放；状态：{evidence.url_health_status}）")
        lines.append("")
    lines.extend(["## 数据状态与信息缺口", ""])
    if snapshot is None:
        lines.append("- 尚无平台共享公司快照。")
    else:
        lines.append(f"- 数据新鲜度：{_freshness_status(snapshot, refresh_policy, as_of)}")
        lines.append(f"- 最后检查时间：{_aware_utc(snapshot.last_checked_at).isoformat()}")
        for gap in snapshot.information_gaps:
            lines.append(f"- 信息缺口：{_single_line(gap)}")
    lines.extend(
        [
            "",
            "---",
            "本报告是生成时点的只读快照；后续新增、纠正或撤回请以公司最新详情为准。",
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


def create_personal_company_report(
    session: Session,
    user: User,
    policy: PersonalEntitlementPolicy,
    refresh_policy: RefreshPolicy,
    company_id: UUID,
    *,
    idempotency_key: str,
) -> PersonalCompanyReportOut:
    company = _shared_company(session, company_id)
    if company is None:
        raise PersonalFeatureNotFoundError("company not found")
    _lock_user(session, user)
    existing = session.scalar(
        select(PersonalCompanyReport).where(
            PersonalCompanyReport.owner_user_id == user.id,
            PersonalCompanyReport.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.company_id != company_id:
            raise PersonalRequestConflictError("idempotency key belongs to another report")
        return _report_out(existing, reused=True)

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
    events = [platform_shared_event_out(session, event, user) for event in event_rows]
    snapshot = session.scalar(
        select(CompanySnapshot).where(
            CompanySnapshot.company_id == company_id,
            CompanySnapshot.is_current.is_(True),
            CompanySnapshot.visibility_scope == PLATFORM_SHARED_SCOPE,
            CompanySnapshot.owner_user_id.is_(None),
            CompanySnapshot.owner_tenant_id.is_(None),
        )
    )
    as_of = utc_now()
    markdown = _report_markdown(company, snapshot, events, as_of, refresh_policy)
    report = PersonalCompanyReport(
        owner_user_id=user.id,
        company_id=company.id,
        company_legal_name=company.legal_name,
        report_version=_REPORT_VERSION,
        idempotency_key=idempotency_key,
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
    return _report_out(report)
