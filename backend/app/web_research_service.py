from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app.config import SourceMonitoringPolicy, WebResearchPolicy
from backend.app.database import set_request_context
from backend.app.models import (
    PLATFORM_SHARED_SCOPE,
    SYSTEM_RESTRICTED_SCOPE,
    Company,
    CompanyResearchJob,
    EntityMention,
    Event,
    EventEvidence,
    PersonalCompanyRequest,
    RawDocument,
    Source,
    UsageLedger,
    User,
    WebSearchCacheEntry,
    utc_now,
)
from backend.app.services import user_has_role
from backend.app.source_fetcher import (
    SourceFetchError,
    TrustedSourceFetcher,
    UrlSafetyError,
    canonicalize_source_url,
)
from backend.app.web_search import (
    SearchProvider,
    SearchProviderError,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

WEB_RESEARCH_SOURCE_CODE = "bounded_public_web"
ACTIVE_JOB_STATUSES = ("queued", "running", "partial", "budget_deferred")
ACTIVE_REQUEST_STATUSES = ("research_queued", "researching", "partial", "budget_deferred")
TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_NAMES = {"from", "spm"}
BLOCKED_DISCOVERY_DOMAINS = {
    "tianyancha.com",
    "www.tianyancha.com",
    "qianfan.baidubce.com",
    "api.bochaai.com",
}

RESEARCH_MODULES = (
    "financial_operation",
    "financing_cap_table",
    "contract_commercial",
    "product_technology",
    "governance_people",
    "legal_compliance",
    "capacity_assets",
    "exit_liquidity",
    "information_quality",
)

SEARCH_GROUPS = (
    (
        "business_capital",
        "财务 融资 股权 合同 中标 订单",
    ),
    (
        "technology_risk_exit",
        "产品 专利 高管 诉讼 处罚 产能 上市 并购 回购",
    ),
)

SEARCH_GROUP_MODULES = {
    "business_capital": (
        "financial_operation",
        "financing_cap_table",
        "contract_commercial",
    ),
    "technology_risk_exit": (
        "product_technology",
        "governance_people",
        "legal_compliance",
        "capacity_assets",
        "exit_liquidity",
    ),
}

EVENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("exit_liquidity", ("ipo", "上市", "辅导", "挂牌", "并购", "回购", "股权转让")),
    (
        "legal_compliance",
        ("诉讼", "仲裁", "执行", "处罚", "失信", "合规", "吊销", "破产", "清算"),
    ),
    (
        "financing_cap_table",
        ("融资", "增资", "股权", "股东", "注册资本", "质押", "冻结", "估值"),
    ),
    (
        "contract_commercial",
        ("中标", "合同", "订单", "客户", "合作", "供应商", "商业化", "首单"),
    ),
    (
        "product_technology",
        ("产品", "技术", "专利", "研发", "认证", "注册证", "临床", "许可"),
    ),
    (
        "governance_people",
        ("高管", "董事", "监事", "法定代表人", "创始人", "离职", "任命", "裁员"),
    ),
    (
        "capacity_assets",
        ("工厂", "产能", "生产线", "投产", "开工", "环评", "土地", "资产", "查封"),
    ),
    (
        "financial_operation",
        ("营收", "收入", "利润", "毛利", "销量", "出货", "现金", "经营", "停产"),
    ),
)

SEVERE_TERMS = (
    "破产",
    "失信",
    "诈骗",
    "刑事",
    "停产",
    "清算",
    "吊销",
    "立案调查",
    "限制高消费",
)
NEGATIVE_TERMS = SEVERE_TERMS + ("处罚", "诉讼", "仲裁", "执行", "冻结", "查封", "违约")
POSITIVE_TERMS = ("融资", "中标", "首单", "投产", "获批", "认证", "合作", "上市辅导")


class WebResearchAccessError(RuntimeError):
    pass


class WebResearchBudgetDeferred(RuntimeError):
    pass


@dataclass(frozen=True)
class WebResearchWorkerResult:
    status: str
    job_id: UUID | None = None
    company_id: UUID | None = None
    stage: str | None = None
    external_calls: int = 0
    cache_hits: int = 0
    documents_created: int = 0
    events_created: int = 0
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "job_id": str(self.job_id) if self.job_id else None,
            "company_id": str(self.company_id) if self.company_id else None,
            "stage": self.stage,
            "external_calls": self.external_calls,
            "cache_hits": self.cache_hits,
            "documents_created": self.documents_created,
            "events_created": self.events_created,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost": "0",
            "error_code": self.error_code,
        }


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _normalized_identity(value: str) -> str:
    return "".join(value.split()).casefold()


def _public_company(company: Company | None) -> bool:
    return bool(
        company
        and company.tenant_id is None
        and company.visibility_scope == "public"
        and company.identity_status == "verified"
        and company.credit_code
    )


def _identity_fingerprint(company: Company) -> str:
    return _sha256(
        "|".join(
            [
                str(company.id),
                _normalized_identity(company.legal_name),
                company.credit_code or "",
                _normalized_identity(company.registered_region or ""),
            ]
        )
    )


def _initial_coverage(policy: WebResearchPolicy) -> dict[str, object]:
    return {
        "policy_version": policy.version,
        "modules": {module: "pending" for module in RESEARCH_MODULES},
        "search_groups": {
            code: {"status": "pending", "providers": {}} for code, _ in SEARCH_GROUPS
        },
        "candidates": [],
        "candidate_index": 0,
        "documents": [],
        "stats": {
            "search_cache_hits": 0,
            "document_cache_hits": 0,
            "search_calls": 0,
            "fetch_calls": 0,
            "downloaded_bytes": 0,
            "events_created": 0,
        },
    }


def _active_job(session: Session, company_id: UUID) -> CompanyResearchJob | None:
    return session.scalar(
        select(CompanyResearchJob)
        .where(
            CompanyResearchJob.company_id == company_id,
            CompanyResearchJob.status.in_(ACTIVE_JOB_STATUSES),
        )
        .order_by(CompanyResearchJob.created_at)
        .limit(1)
    )


def prepare_pending_research_requests(
    session: Session,
    user: User,
    policy: WebResearchPolicy,
) -> int:
    if not user_has_role(session, user.id, "platform_admin"):
        raise WebResearchAccessError("platform_admin role required")
    prepared = 0
    requests = list(
        session.scalars(
            select(PersonalCompanyRequest)
            .where(
                PersonalCompanyRequest.company_id.is_not(None),
                or_(
                    PersonalCompanyRequest.status == "research_queued",
                    (
                        (PersonalCompanyRequest.request_type == "refresh")
                        & (PersonalCompanyRequest.status == "pending")
                    ),
                ),
            )
            .order_by(PersonalCompanyRequest.created_at)
        )
    )
    for request in requests:
        company = session.scalar(
            select(Company).where(Company.id == request.company_id).with_for_update()
        )
        if not _public_company(company):
            request.status = "failed"
            request.last_error_code = "company_not_verified_for_shared_research"
            continue
        job = _active_job(session, company.id)
        if job is None:
            job = CompanyResearchJob(
                company_id=company.id,
                created_by_user_id=request.owner_user_id,
                status="queued",
                current_stage=f"search:{SEARCH_GROUPS[0][0]}",
                policy_version=policy.version,
                coverage=_initial_coverage(policy),
            )
            session.add(job)
            session.flush()
        request.research_job_id = job.id
        request.status = "research_queued"
        request.last_error_code = None
        prepared += 1
    session.commit()
    return prepared


def _linked_requests(session: Session, job_id: UUID) -> list[PersonalCompanyRequest]:
    return list(
        session.scalars(
            select(PersonalCompanyRequest).where(PersonalCompanyRequest.research_job_id == job_id)
        )
    )


def _active_linked_requests(session: Session, job_id: UUID) -> list[PersonalCompanyRequest]:
    return [
        request
        for request in _linked_requests(session, job_id)
        if request.status in ACTIVE_REQUEST_STATUSES
    ]


def _cancel_job(session: Session, job: CompanyResearchJob) -> WebResearchWorkerResult:
    now = utc_now()
    job.status = "cancelled"
    job.current_stage = "cancelled"
    job.cancelled_at = now
    job.leased_until = None
    job.heartbeat_at = None
    for request in _linked_requests(session, job.id):
        if request.status in {*ACTIVE_REQUEST_STATUSES, "cancel_requested"}:
            request.status = "cancelled"
            request.cancelled_at = request.cancelled_at or now
            request.leased_until = None
            request.heartbeat_at = None
    session.commit()
    return WebResearchWorkerResult(
        status="cancelled",
        job_id=job.id,
        company_id=job.company_id,
        stage="cancelled",
        external_calls=job.external_calls,
    )


def _lease_job(
    session: Session,
    user: User,
    policy: WebResearchPolicy,
) -> CompanyResearchJob | None:
    now = utc_now()
    statement = (
        select(CompanyResearchJob)
        .where(
            CompanyResearchJob.policy_version == policy.version,
            or_(
                CompanyResearchJob.status.in_(("queued", "partial", "budget_deferred")),
                (
                    (CompanyResearchJob.status == "running")
                    & (
                        (CompanyResearchJob.leased_until.is_(None))
                        | (CompanyResearchJob.leased_until < now)
                    )
                ),
            ),
        )
        .order_by(CompanyResearchJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = session.scalar(statement)
    if job is None:
        session.commit()
        return None
    if job.cancel_requested_at is not None or not _active_linked_requests(session, job.id):
        _cancel_job(session, job)
        return None
    job.status = "running"
    coverage = dict(job.coverage or {})
    coverage.setdefault("started_at", now.isoformat())
    coverage["step_started_at"] = now.isoformat()
    job.coverage = coverage
    job.leased_until = now + timedelta(seconds=policy.worker_lease_seconds)
    job.heartbeat_at = now
    for request in _active_linked_requests(session, job.id):
        request.status = "researching"
        request.leased_until = job.leased_until
        request.heartbeat_at = now
    session.commit()
    return job


def _query_for(company: Company, terms: str) -> str:
    return f'"{company.legal_name}" {terms}'


def _official_host(company: Company) -> str | None:
    if not company.official_website:
        return None
    try:
        host = (urlsplit(company.official_website).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    return host.removeprefix("www.") or None


def _subject_match(company: Company, result: SearchResult) -> bool:
    haystack = _normalized_identity(f"{result.title} {result.snippet}")
    if _normalized_identity(company.legal_name) in haystack:
        return True
    if company.credit_code and company.credit_code.casefold() in haystack:
        return True
    official_host = _official_host(company)
    result_host = (urlsplit(result.url).hostname or "").lower().removeprefix("www.")
    return bool(official_host and result_host == official_host)


def _canonical_candidate_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host in BLOCKED_DISCOVERY_DOMAINS or host.endswith(".tianyancha.com"):
        return None
    root_domain = host.removeprefix("www.")
    try:
        canonical = canonicalize_source_url(value, root_domain)
    except UrlSafetyError:
        return None
    parsed = urlsplit(canonical)
    filtered_query = [
        (name, value)
        for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        if name not in TRACKING_QUERY_NAMES
        and not any(name.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES)
    ]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path or "/", urlencode(filtered_query), "")
    )


def _result_score(company: Company, result: SearchResult) -> tuple[int, str]:
    host = (urlsplit(result.url).hostname or "").lower().removeprefix("www.")
    score = 0
    if host.endswith(".gov.cn") or host == "gov.cn":
        score += 50
    if _official_host(company) == host:
        score += 45
    if _normalized_identity(company.legal_name) in _normalized_identity(result.title):
        score += 20
    result_text = f"{result.title}{result.snippet}".lower()
    if any(keyword in result_text for _, terms in EVENT_KEYWORDS for keyword in terms):
        score += 10
    return score, result.published_at or ""


def _cached_response(
    session: Session,
    company: Company,
    provider_code: str,
    query: str,
    now: datetime,
) -> SearchResponse | None:
    entry = session.scalar(
        select(WebSearchCacheEntry).where(
            WebSearchCacheEntry.company_id == company.id,
            WebSearchCacheEntry.provider_code == provider_code,
            WebSearchCacheEntry.query_hash == _sha256(query),
            WebSearchCacheEntry.identity_fingerprint == _identity_fingerprint(company),
            WebSearchCacheEntry.expires_at > now,
        )
    )
    if entry is None:
        return None
    try:
        results = [SearchResult.from_dict(item) for item in entry.results]
    except ValueError:
        return None
    return SearchResponse(
        provider_code=provider_code,
        request_id=None,
        results=results,
        response_hash=entry.response_hash,
        external_calls=0,
    )


def _search_call_count(session: Session, *, since: datetime) -> int:
    return int(
        session.scalar(
            select(func.coalesce(func.sum(UsageLedger.external_calls), 0)).where(
                UsageLedger.provider.in_(("web_search_baidu", "web_search_bocha")),
                UsageLedger.operation == "company_discovery",
                UsageLedger.created_at >= since,
            )
        )
        or 0
    )


def _check_search_budget(
    session: Session,
    job: CompanyResearchJob,
    policy: WebResearchPolicy,
) -> None:
    now = utc_now()
    if job.external_calls >= policy.max_search_calls_per_job:
        raise WebResearchBudgetDeferred("job search call limit reached")
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    if _search_call_count(session, since=day_start) >= policy.daily_search_call_limit:
        raise WebResearchBudgetDeferred("daily search call limit reached")
    if _search_call_count(session, since=month_start) >= policy.monthly_search_call_limit:
        raise WebResearchBudgetDeferred("monthly search call limit reached")


def _record_usage(
    session: Session,
    user: User,
    job: CompanyResearchJob,
    *,
    provider: str,
    operation: str,
    external_calls: int,
    metrics: dict[str, object],
    idempotency_suffix: str,
) -> None:
    idempotency_key = _sha256(f"web-research:{job.id}:{idempotency_suffix}")
    if session.scalar(select(UsageLedger.id).where(UsageLedger.idempotency_key == idempotency_key)):
        return
    session.add(
        UsageLedger(
            tenant_id=user.tenant_id,
            company_id=job.company_id,
            provider=provider,
            operation=operation,
            external_calls=external_calls,
            input_tokens=0,
            output_tokens=0,
            estimated_cost=Decimal("0"),
            metrics=metrics,
            idempotency_key=idempotency_key,
        )
    )


def _store_search_response(
    session: Session,
    company: Company,
    response: SearchResponse,
    query_kind: str,
    query: str,
    policy: WebResearchPolicy,
) -> None:
    now = utc_now()
    entry = session.scalar(
        select(WebSearchCacheEntry).where(
            WebSearchCacheEntry.company_id == company.id,
            WebSearchCacheEntry.provider_code == response.provider_code,
            WebSearchCacheEntry.query_hash == _sha256(query),
            WebSearchCacheEntry.identity_fingerprint == _identity_fingerprint(company),
        )
    )
    values = [result.to_dict() for result in response.results]
    if entry is None:
        entry = WebSearchCacheEntry(
            company_id=company.id,
            provider_code=response.provider_code,
            query_kind=query_kind,
            query_text=query,
            query_hash=_sha256(query),
            identity_fingerprint=_identity_fingerprint(company),
            response_hash=response.response_hash,
            results=values,
            fetched_at=now,
            expires_at=now + timedelta(days=policy.search_cache_ttl_days),
        )
        session.add(entry)
    else:
        entry.query_kind = query_kind
        entry.query_text = query
        entry.response_hash = response.response_hash
        entry.results = values
        entry.fetched_at = now
        entry.expires_at = now + timedelta(days=policy.search_cache_ttl_days)


def _provider_response(
    session: Session,
    user: User,
    job: CompanyResearchJob,
    company: Company,
    provider: SearchProvider,
    *,
    query_kind: str,
    query: str,
    policy: WebResearchPolicy,
) -> tuple[SearchResponse, bool]:
    cached = _cached_response(session, company, provider.code, query, utc_now())
    if cached is not None:
        coverage = dict(job.coverage)
        stats = dict(coverage.get("stats", {}))
        stats["search_cache_hits"] = int(stats.get("search_cache_hits", 0)) + 1
        coverage["stats"] = stats
        job.coverage = coverage
        for request in _active_linked_requests(session, job.id):
            request.cache_hits += 1
        session.commit()
        return cached, True
    _check_search_budget(session, job, policy)
    try:
        response = provider.search(
            SearchRequest(query=query, max_results=policy.max_results_per_search)
        )
    except SearchProviderError as error:
        job.external_calls += error.external_calls
        _record_usage(
            session,
            user,
            job,
            provider=f"web_search_{provider.code}",
            operation="company_discovery",
            external_calls=error.external_calls,
            metrics={"query_kind": query_kind, "status": "failed", "error_code": error.code},
            idempotency_suffix=f"search:{query_kind}:{provider.code}",
        )
        session.commit()
        raise
    job.external_calls += response.external_calls
    coverage = dict(job.coverage)
    stats = dict(coverage.get("stats", {}))
    stats["search_calls"] = int(stats.get("search_calls", 0)) + response.external_calls
    coverage["stats"] = stats
    job.coverage = coverage
    _store_search_response(session, company, response, query_kind, query, policy)
    _record_usage(
        session,
        user,
        job,
        provider=f"web_search_{provider.code}",
        operation="company_discovery",
        external_calls=response.external_calls,
        metrics={
            "query_kind": query_kind,
            "status": "completed",
            "request_id": response.request_id,
            "results": len(response.results),
        },
        idempotency_suffix=f"search:{query_kind}:{provider.code}",
    )
    session.commit()
    return response, False


def _refresh_job(session: Session, job_id: UUID) -> CompanyResearchJob:
    session.expire_all()
    job = session.get(CompanyResearchJob, job_id)
    if job is None:
        raise RuntimeError("research job disappeared")
    return job


def _job_should_stop(session: Session, job: CompanyResearchJob) -> bool:
    return job.cancel_requested_at is not None or not _active_linked_requests(session, job.id)


def _restore_worker_context(session: Session, user: User) -> None:
    set_request_context(session, user.id, user.tenant_id)


def _process_search_group(
    session: Session,
    user: User,
    job: CompanyResearchJob,
    company: Company,
    policy: WebResearchPolicy,
    providers: dict[str, SearchProvider],
    group_code: str,
    terms: str,
) -> WebResearchWorkerResult:
    query = _query_for(company, terms)
    primary = providers[policy.primary_provider]
    fallback = providers[policy.fallback_provider]
    responses: list[SearchResponse] = []
    provider_state: dict[str, object] = {}
    cache_hits = 0
    try:
        response, cached = _provider_response(
            session,
            user,
            job,
            company,
            primary,
            query_kind=group_code,
            query=query,
            policy=policy,
        )
        responses.append(response)
        cache_hits += int(cached)
        provider_state[primary.code] = {
            "status": "cache_hit" if cached else "completed",
            "results": len(response.results),
        }
    except SearchProviderError as error:
        provider_state[primary.code] = {"status": "failed", "error_code": error.code}
    _restore_worker_context(session, user)
    job = _refresh_job(session, job.id)
    if _job_should_stop(session, job):
        return _cancel_job(session, job)
    primary_subject_results = sum(
        1
        for response in responses
        for result in response.results
        if _subject_match(company, result)
    )
    if primary_subject_results < policy.fallback_min_subject_results:
        try:
            response, cached = _provider_response(
                session,
                user,
                job,
                company,
                fallback,
                query_kind=group_code,
                query=query,
                policy=policy,
            )
            responses.append(response)
            cache_hits += int(cached)
            provider_state[fallback.code] = {
                "status": "cache_hit" if cached else "completed",
                "results": len(response.results),
                "reason": "primary_failed_or_insufficient",
            }
        except SearchProviderError as error:
            provider_state[fallback.code] = {"status": "failed", "error_code": error.code}
    _restore_worker_context(session, user)
    job = _refresh_job(session, job.id)
    if _job_should_stop(session, job):
        return _cancel_job(session, job)

    merged: dict[str, dict[str, object]] = {}
    for response in responses:
        for result in response.results:
            if not _subject_match(company, result):
                continue
            canonical_url = _canonical_candidate_url(result.url)
            if canonical_url is None:
                continue
            current = merged.get(canonical_url)
            candidate = {
                **result.to_dict(),
                "url": canonical_url,
                "discovered_by": [response.provider_code],
                "query_kind": group_code,
            }
            if current is None:
                merged[canonical_url] = candidate
            else:
                discovered = list(current.get("discovered_by", []))
                if response.provider_code not in discovered:
                    discovered.append(response.provider_code)
                current["discovered_by"] = discovered
    coverage = dict(job.coverage)
    groups = dict(coverage.get("search_groups", {}))
    groups[group_code] = {
        "status": "completed",
        "providers": provider_state,
        "subject_results": len(merged),
    }
    coverage["search_groups"] = groups
    modules = dict(coverage.get("modules", {}))
    for module in SEARCH_GROUP_MODULES[group_code]:
        modules[module] = "search_completed"
    coverage["modules"] = modules
    existing_candidates = {
        str(item.get("url")): item
        for item in coverage.get("candidates", [])
        if isinstance(item, dict) and item.get("url")
    }
    existing_candidates.update(merged)
    candidates = list(existing_candidates.values())
    candidates.sort(
        key=lambda item: _result_score(company, SearchResult.from_dict(item)), reverse=True
    )
    coverage["candidates"] = candidates[: policy.max_candidate_urls]
    job.coverage = coverage
    next_groups = [
        code for code, _ in SEARCH_GROUPS if groups.get(code, {}).get("status") != "completed"
    ]
    job.current_stage = f"search:{next_groups[0]}" if next_groups else "fetch"
    job.status = "partial"
    job.leased_until = None
    job.heartbeat_at = utc_now()
    for request in _active_linked_requests(session, job.id):
        request.status = "partial"
        request.heartbeat_at = job.heartbeat_at
        request.leased_until = None
    session.commit()
    return WebResearchWorkerResult(
        status="partial",
        job_id=job.id,
        company_id=job.company_id,
        stage=job.current_stage,
        external_calls=job.external_calls,
        cache_hits=cache_hits,
    )


def _source(session: Session) -> Source:
    source = session.scalar(select(Source).where(Source.code == WEB_RESEARCH_SOURCE_CODE))
    if source is None:
        source = Source(
            code=WEB_RESEARCH_SOURCE_CODE,
            name="受限公开网络研究",
            source_quality="B",
            license_status="public",
            base_url=None,
        )
        session.add(source)
        session.flush()
    return source


def _document_cache(
    session: Session,
    source_id: UUID,
    url: str,
    company: Company,
    policy: WebResearchPolicy,
) -> RawDocument | None:
    threshold = utc_now() - timedelta(days=policy.document_cache_ttl_days)
    documents = session.scalars(
        select(RawDocument)
        .join(EntityMention, EntityMention.raw_document_id == RawDocument.id)
        .where(
            RawDocument.source_id == source_id,
            RawDocument.canonical_url == url,
            RawDocument.visibility_scope == SYSTEM_RESTRICTED_SCOPE,
            RawDocument.observed_at >= threshold,
            EntityMention.candidate_company_id == company.id,
            EntityMention.visibility_scope == SYSTEM_RESTRICTED_SCOPE,
            EntityMention.resolution_status == "verified",
        )
        .order_by(RawDocument.observed_at.desc())
        .limit(5)
    )
    for document in documents:
        excerpt = str(document.payload.get("excerpt") or "")
        if _document_matches_company(company, document.title, excerpt, document.canonical_url):
            return document
    return None


def _document_matches_company(
    company: Company,
    title: str,
    excerpt: str,
    canonical_url: str,
) -> bool:
    content = f"{title} {excerpt}"
    current_identity_match = _normalized_identity(company.legal_name) in _normalized_identity(
        content
    ) or bool(company.credit_code and company.credit_code.casefold() in content.casefold())
    host = (urlsplit(canonical_url).hostname or "").lower().removeprefix("www.")
    return current_identity_match or bool(_official_host(company) == host)


def _ensure_entity_mention(
    session: Session,
    document: RawDocument,
    company: Company,
) -> None:
    existing = session.scalar(
        select(EntityMention.id).where(
            EntityMention.raw_document_id == document.id,
            EntityMention.candidate_company_id == company.id,
            EntityMention.visibility_scope == SYSTEM_RESTRICTED_SCOPE,
            EntityMention.resolution_status == "verified",
        )
    )
    if existing is not None:
        return
    session.add(
        EntityMention(
            raw_document_id=document.id,
            owner_user_id=None,
            owner_tenant_id=None,
            visibility_scope=SYSTEM_RESTRICTED_SCOPE,
            candidate_company_id=company.id,
            mention_text=company.legal_name,
            match_rule="verified_identity_exact_or_official_domain",
            match_confidence=Decimal("0.950"),
            resolution_status="verified",
        )
    )


def _event_classification(title: str, excerpt: str) -> str | None:
    content = f"{title} {excerpt}".casefold()
    for event_type, keywords in EVENT_KEYWORDS:
        if any(keyword.casefold() in content for keyword in keywords):
            return event_type
    return None


def _event_direction(content: str) -> str:
    lowered = content.casefold()
    if any(term.casefold() in lowered for term in NEGATIVE_TERMS):
        return "negative"
    if any(term.casefold() in lowered for term in POSITIVE_TERMS):
        return "positive"
    return "neutral"


def _source_quality(company: Company, url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    if host == "gov.cn" or host.endswith(".gov.cn") or host == _official_host(company):
        return "A"
    return "B"


def _materiality(event_type: str, content: str) -> int:
    if any(term in content for term in SEVERE_TERMS):
        return 85
    if event_type in {"financing_cap_table", "contract_commercial", "exit_liquidity"}:
        return 70
    if event_type in {"legal_compliance", "capacity_assets", "product_technology"}:
        return 65
    return 55


def _raw_document(
    session: Session,
    source: Source,
    company: Company,
    candidate: dict[str, object],
    discovered: object,
    job: CompanyResearchJob,
) -> tuple[RawDocument, bool]:
    canonical_url = str(discovered.canonical_url)
    existing = session.scalar(
        select(RawDocument).where(
            RawDocument.source_id == source.id,
            RawDocument.visibility_scope == SYSTEM_RESTRICTED_SCOPE,
            RawDocument.document_dedupe_key
            == _sha256(f"{source.id}|{canonical_url}|{discovered.content_hash}"),
        )
    )
    if existing is not None:
        _ensure_entity_mention(session, existing, company)
        return existing, False
    excerpt = (discovered.excerpt or "").strip()[:1000]
    document = RawDocument(
        source_id=source.id,
        research_import_id=None,
        candidate_document_id=None,
        owner_user_id=None,
        owner_tenant_id=None,
        visibility_scope=SYSTEM_RESTRICTED_SCOPE,
        external_record_id=_sha256(f"{canonical_url}|{discovered.content_hash}"),
        canonical_url=canonical_url,
        title=discovered.title[:500],
        published_at=discovered.published_at,
        published_on=discovered.published_at.date() if discovered.published_at else None,
        observed_at=utc_now(),
        content_hash=discovered.content_hash,
        document_dedupe_key=_sha256(f"{source.id}|{canonical_url}|{discovered.content_hash}"),
        license_status="public",
        payload={
            "retention": "minimal_excerpt",
            "excerpt": excerpt,
            "discovered_by": candidate.get("discovered_by", []),
            "research_job_id": str(job.id),
            "_source_verification": {
                "status": discovered.link_health_status,
                "checked_at": utc_now().isoformat(),
                "http_status": discovered.http_status,
                "final_url": canonical_url,
                "reason": "bounded_fetch_completed",
                "external_calls": 0,
            },
        },
    )
    session.add(document)
    session.flush()
    _ensure_entity_mention(session, document, company)
    return document, True


def _candidate_event(
    session: Session,
    company: Company,
    document: RawDocument,
    source: Source,
) -> tuple[Event | None, bool]:
    excerpt = str(document.payload.get("excerpt") or "").strip()
    event_type = _event_classification(document.title, excerpt)
    if event_type is None or not excerpt:
        return None, False
    fingerprint = _sha256(
        f"{company.id}|{document.canonical_url}|{document.content_hash}|{event_type}"
    )
    existing = session.scalar(
        select(Event).where(
            Event.company_id == company.id,
            Event.visibility_scope == PLATFORM_SHARED_SCOPE,
            Event.fingerprint_version == "web-v1",
            Event.event_fingerprint == fingerprint,
        )
    )
    if existing is not None:
        return existing, False
    content = f"{document.title} {excerpt}"
    severe = any(term in content for term in SEVERE_TERMS)
    source_quality = _source_quality(company, document.canonical_url)
    event = Event(
        company_id=company.id,
        owner_user_id=None,
        owner_tenant_id=None,
        visibility_scope=PLATFORM_SHARED_SCOPE,
        event_type=event_type,
        event_subtype="bounded_public_web_page",
        status="candidate",
        direction=_event_direction(content),
        materiality_score=_materiality(event_type, content),
        risk_severity=(
            "high" if severe else ("moderate" if event_type == "legal_compliance" else "low")
        ),
        confidence_score=Decimal("0.800") if source_quality == "A" else Decimal("0.700"),
        source_quality=source_quality,
        title=document.title[:200],
        summary=f"公开来源出现与该公司相关的变化线索：{excerpt[:700]}",
        facts=[{"name": "公开页面标题", "value": document.title, "unit": None}],
        uncertainties=["该内容由程序发现并完成主体核对，尚未经过人工事实复核。"],
        occurred_at=document.published_at,
        published_at=document.published_at,
        published_on=document.published_on,
        observed_at=document.observed_at,
        fingerprint_version="web-v1",
        event_fingerprint=fingerprint,
        publication_route="unconfirmed_lead",
        publication_policy_version="bounded-web-v1",
        publication_reasons=[
            "auto_publish_disabled",
            "bounded_public_web_discovery",
            "human_fact_review_not_completed",
            *(["serious_negative_requires_review"] if severe else []),
        ],
    )
    session.add(event)
    session.flush()
    verification = document.payload.get("_source_verification", {})
    session.add(
        EventEvidence(
            event_id=event.id,
            raw_document_id=document.id,
            source_event_evidence_id=None,
            owner_user_id=None,
            owner_tenant_id=None,
            visibility_scope=PLATFORM_SHARED_SCOPE,
            evidence_excerpt=excerpt[:1000],
            span_hash=_sha256(excerpt[:1000]),
            support_type="supports",
            display_source_name=urlsplit(document.canonical_url).hostname or source.name,
            display_source_quality=source_quality,
            display_title=document.title,
            display_canonical_url=document.canonical_url,
            display_published_at=document.published_at,
            display_published_on=document.published_on,
            display_observed_at=document.observed_at,
            display_url_health_status=str(verification.get("status") or "healthy"),
            display_url_http_status=verification.get("http_status"),
            display_url_checked_at=utc_now(),
            display_final_url=document.canonical_url,
            display_license_status="public",
            display_allowed=True,
        )
    )
    return event, True


def _fetch_policy(
    policy: WebResearchPolicy,
    *,
    remaining_requests: int,
    remaining_bytes: int,
) -> SourceMonitoringPolicy:
    return SourceMonitoringPolicy(
        version=policy.version,
        max_requests_per_run=remaining_requests,
        max_download_bytes_per_run=remaining_bytes,
        max_response_bytes=policy.max_response_bytes,
        timeout_seconds=policy.timeout_seconds,
        retry_limit=0,
        max_redirects=3,
        min_request_interval_ms=1_000,
        worker_lease_seconds=policy.worker_lease_seconds,
        scheduler_max_sources_per_run=1,
        failure_backoff_max_multiplier=2,
        user_agent=policy.user_agent,
    )


def _fetch_candidate(
    session: Session,
    user: User,
    job: CompanyResearchJob,
    company: Company,
    policy: WebResearchPolicy,
    fetcher_factory: Callable[[SourceMonitoringPolicy], TrustedSourceFetcher],
) -> WebResearchWorkerResult:
    coverage = dict(job.coverage)
    candidates = [item for item in coverage.get("candidates", []) if isinstance(item, dict)]
    index = int(coverage.get("candidate_index", 0))
    documents = [item for item in coverage.get("documents", []) if isinstance(item, dict)]
    stats = dict(coverage.get("stats", {}))
    previous_fetch_calls = int(stats.get("fetch_calls", 0))
    previous_downloaded_bytes = int(stats.get("downloaded_bytes", 0))
    successful = sum(1 for item in documents if item.get("status") in {"created", "reused"})
    limits_reached = (
        previous_fetch_calls >= policy.max_fetch_requests_per_job
        or previous_downloaded_bytes >= policy.max_download_bytes_per_job
    )
    if index >= len(candidates) or successful >= policy.max_documents_per_job or limits_reached:
        job.current_stage = "finalize"
        job.status = "partial"
        job.leased_until = None
        session.commit()
        return WebResearchWorkerResult(
            status="partial",
            job_id=job.id,
            company_id=job.company_id,
            stage="finalize",
            external_calls=job.external_calls,
        )
    candidate = candidates[index]
    url = str(candidate.get("url") or "")
    coverage["candidate_index"] = index + 1
    source = _source(session)
    cached_document = _document_cache(session, source.id, url, company, policy)
    documents_created = 0
    events_created = 0
    fetch_calls = 0
    downloaded_bytes = 0
    error_code: str | None = None
    if cached_document is not None:
        _, event_created = _candidate_event(session, company, cached_document, source)
        events_created += int(event_created)
        documents.append({"url": url, "status": "reused", "document_id": str(cached_document.id)})
        stats = dict(coverage.get("stats", {}))
        stats["document_cache_hits"] = int(stats.get("document_cache_hits", 0)) + 1
        coverage["stats"] = stats
        for request in _active_linked_requests(session, job.id):
            request.cache_hits += 1
    else:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        root_domain = host.removeprefix("www.")
        fetcher = fetcher_factory(
            _fetch_policy(
                policy,
                remaining_requests=policy.max_fetch_requests_per_job - previous_fetch_calls,
                remaining_bytes=policy.max_download_bytes_per_job - previous_downloaded_bytes,
            )
        )
        try:
            result = fetcher.check(
                source_type="single_page",
                root_domain=root_domain,
                start_url=url,
                retention_policy="minimal_excerpt",
                conditional_state={},
            )
            fetch_calls = result.request_count
            downloaded_bytes = result.downloaded_bytes
            if not result.documents:
                error_code = "no_fetchable_document"
            else:
                discovered = result.documents[0]
                excerpt = discovered.excerpt or ""
                if not _document_matches_company(
                    company,
                    discovered.title,
                    excerpt,
                    discovered.canonical_url,
                ):
                    error_code = "fetched_page_identity_mismatch"
                else:
                    document, document_created = _raw_document(
                        session,
                        source,
                        company,
                        candidate,
                        discovered,
                        job,
                    )
                    documents_created += int(document_created)
                    _, event_created = _candidate_event(session, company, document, source)
                    events_created += int(event_created)
                    documents.append(
                        {
                            "url": url,
                            "status": "created" if document_created else "reused",
                            "document_id": str(document.id),
                            "event_created": event_created,
                        }
                    )
        except SourceFetchError as error:
            fetch_calls = fetcher.request_count
            downloaded_bytes = fetcher.downloaded_bytes
            error_code = error.code
        finally:
            fetcher.close()
        job.external_calls += fetch_calls
        _record_usage(
            session,
            user,
            job,
            provider="public_web_fetch",
            operation="evidence_fetch",
            external_calls=fetch_calls,
            metrics={
                "status": "completed" if error_code is None else "failed",
                "error_code": error_code,
                "downloaded_bytes": downloaded_bytes,
            },
            idempotency_suffix=f"fetch:{_sha256(url)}",
        )
        if error_code is not None:
            documents.append({"url": url, "status": "failed", "error_code": error_code})
    stats = dict(coverage.get("stats", {}))
    stats["fetch_calls"] = int(stats.get("fetch_calls", 0)) + fetch_calls
    stats["downloaded_bytes"] = int(stats.get("downloaded_bytes", 0)) + downloaded_bytes
    stats["events_created"] = int(stats.get("events_created", 0)) + events_created
    coverage["documents"] = documents
    coverage["stats"] = stats
    job.coverage = coverage
    job.status = "partial"
    job.current_stage = "fetch"
    job.leased_until = None
    job.heartbeat_at = utc_now()
    for request in _active_linked_requests(session, job.id):
        request.status = "partial"
        request.heartbeat_at = job.heartbeat_at
        request.leased_until = None
    session.commit()
    _restore_worker_context(session, user)
    job = _refresh_job(session, job.id)
    if _job_should_stop(session, job):
        return _cancel_job(session, job)
    return WebResearchWorkerResult(
        status="partial",
        job_id=job.id,
        company_id=job.company_id,
        stage="fetch",
        external_calls=job.external_calls,
        cache_hits=int(cached_document is not None),
        documents_created=documents_created,
        events_created=events_created,
        error_code=error_code,
    )


def _finalize(session: Session, job: CompanyResearchJob) -> WebResearchWorkerResult:
    coverage = dict(job.coverage)
    documents = [item for item in coverage.get("documents", []) if isinstance(item, dict)]
    event_types = set(
        session.scalars(
            select(Event.event_type).where(
                Event.company_id == job.company_id,
                Event.visibility_scope == PLATFORM_SHARED_SCOPE,
                Event.fingerprint_version == "web-v1",
                Event.observed_at >= job.created_at,
            )
        )
    )
    modules = {
        module: (
            "completed"
            if module in event_types
            else ("completed" if module == "information_quality" else "no_data")
        )
        for module in RESEARCH_MODULES
    }
    coverage["modules"] = modules
    coverage["completed_at"] = utc_now().isoformat()
    coverage["failed_documents"] = sum(1 for item in documents if item.get("status") == "failed")
    job.coverage = coverage
    job.status = "completed"
    job.current_stage = "completed"
    job.leased_until = None
    job.heartbeat_at = utc_now()
    job.last_error_code = None
    for request in _linked_requests(session, job.id):
        if request.status in ACTIVE_REQUEST_STATUSES:
            request.status = "completed"
            request.leased_until = None
            request.heartbeat_at = job.heartbeat_at
            request.last_error_code = None
    session.commit()
    stats = coverage.get("stats", {})
    return WebResearchWorkerResult(
        status="completed",
        job_id=job.id,
        company_id=job.company_id,
        stage="completed",
        external_calls=job.external_calls,
        documents_created=sum(1 for item in documents if item.get("status") == "created"),
        events_created=int(stats.get("events_created", 0)) if isinstance(stats, dict) else 0,
    )


def _defer_budget(
    session: Session,
    job: CompanyResearchJob,
    error: WebResearchBudgetDeferred,
) -> WebResearchWorkerResult:
    job.status = "budget_deferred"
    job.leased_until = None
    job.heartbeat_at = utc_now()
    job.last_error_code = "web_research_budget_deferred"
    for request in _active_linked_requests(session, job.id):
        request.status = "budget_deferred"
        request.leased_until = None
        request.heartbeat_at = job.heartbeat_at
        request.last_error_code = job.last_error_code
    session.commit()
    return WebResearchWorkerResult(
        status="budget_deferred",
        job_id=job.id,
        company_id=job.company_id,
        stage=job.current_stage,
        external_calls=job.external_calls,
        error_code=str(error),
    )


def run_web_research_worker_once(
    session: Session,
    user: User,
    providers: dict[str, SearchProvider],
    policy: WebResearchPolicy,
    *,
    fetcher_factory: Callable[[SourceMonitoringPolicy], TrustedSourceFetcher] | None = None,
) -> WebResearchWorkerResult:
    _restore_worker_context(session, user)
    if not user_has_role(session, user.id, "platform_admin"):
        raise WebResearchAccessError("platform_admin role required")
    if policy.primary_provider not in providers or policy.fallback_provider not in providers:
        raise ValueError("configured web research providers are missing")
    prepare_pending_research_requests(session, user, policy)
    _restore_worker_context(session, user)
    job = _lease_job(session, user, policy)
    if job is None:
        return WebResearchWorkerResult(status="idle")
    _restore_worker_context(session, user)
    company = session.get(Company, job.company_id)
    if not _public_company(company):
        job.status = "failed"
        job.last_error_code = "company_not_verified_for_shared_research"
        session.commit()
        return WebResearchWorkerResult(
            status="failed",
            job_id=job.id,
            company_id=job.company_id,
            stage=job.current_stage,
            error_code=job.last_error_code,
        )
    coverage = dict(job.coverage or {})
    started_at_value = coverage.get("step_started_at")
    try:
        started_at = datetime.fromisoformat(str(started_at_value))
    except (TypeError, ValueError):
        started_at = job.created_at
    elapsed_seconds = (utc_now() - _aware(started_at)).total_seconds()
    if elapsed_seconds >= policy.max_elapsed_seconds and job.current_stage != "finalize":
        coverage["stop_reason"] = "max_elapsed_seconds_reached"
        job.coverage = coverage
        job.current_stage = "finalize"
        session.commit()
        _restore_worker_context(session, user)
        job = _refresh_job(session, job.id)
        return _finalize(session, job)
    try:
        if job.current_stage.startswith("search:"):
            group_code = job.current_stage.split(":", 1)[1]
            group = next((item for item in SEARCH_GROUPS if item[0] == group_code), None)
            if group is None:
                raise RuntimeError("unknown web research search stage")
            return _process_search_group(
                session,
                user,
                job,
                company,
                policy,
                providers,
                group[0],
                group[1],
            )
        if job.current_stage == "fetch":
            return _fetch_candidate(
                session,
                user,
                job,
                company,
                policy,
                fetcher_factory or TrustedSourceFetcher,
            )
        if job.current_stage == "finalize":
            return _finalize(session, job)
        raise RuntimeError("unknown web research job stage")
    except WebResearchBudgetDeferred as error:
        job = _refresh_job(session, job.id)
        return _defer_budget(session, job, error)


def inspect_web_research_queue(
    session: Session,
    user: User,
    policy: WebResearchPolicy,
) -> dict[str, object]:
    if not user_has_role(session, user.id, "platform_admin"):
        raise WebResearchAccessError("platform_admin role required")
    pending_requests = int(
        session.scalar(
            select(func.count())
            .select_from(PersonalCompanyRequest)
            .where(
                PersonalCompanyRequest.company_id.is_not(None),
                PersonalCompanyRequest.status.in_(("pending", "research_queued")),
            )
        )
        or 0
    )
    queued_jobs = int(
        session.scalar(
            select(func.count())
            .select_from(CompanyResearchJob)
            .where(
                CompanyResearchJob.policy_version == policy.version,
                CompanyResearchJob.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
        or 0
    )
    return {
        "status": "dry_run",
        "pending_requests": pending_requests,
        "queued_jobs": queued_jobs,
        "primary_provider": policy.primary_provider,
        "fallback_provider": policy.fallback_provider,
        "max_search_calls_per_job": policy.max_search_calls_per_job,
        "max_documents_per_job": policy.max_documents_per_job,
        "external_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": "0",
    }
