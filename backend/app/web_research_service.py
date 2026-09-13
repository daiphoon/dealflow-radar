from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app import watchlist_monitoring as monitoring
from backend.app import web_research_budget as budget
from backend.app.config import SourceMonitoringPolicy, WebResearchPolicy
from backend.app.database import set_request_context
from backend.app.fact_support import materialize_event_fact_ledger
from backend.app.investor_analysis_schema import RESEARCH_CANDIDATE_POLICY_VERSION
from backend.app.models import (
    PLATFORM_SHARED_SCOPE,
    SYSTEM_RESTRICTED_SCOPE,
    Company,
    CompanyResearchJob,
    CompanyWatchSchedule,
    EntityMention,
    Event,
    EventEvidence,
    IdentityResearchState,
    PersonalCompanyRequest,
    RawDocument,
    Source,
    UsageLedger,
    User,
    WebSearchCacheEntry,
    utc_now,
)
from backend.app.research_coverage import (
    COVERAGE_VERSION,
    RESEARCH_MODULES,
    SEARCH_GROUP_MODULES,
    SEARCH_GROUPS,
    category_coverage,
    source_routes,
)
from backend.app.research_subject import (
    QUERY_STRATEGY_VERSION,
    BusinessExcerptSelector,
    load_subject,
    matched_name,
    query_subject,
)
from backend.app.services import user_has_role
from backend.app.source_fetcher import (
    SourceFetchError,
    TrustedSourceFetcher,
    UrlSafetyError,
    canonicalize_source_url,
    normalized_robots_rule_cache,
    robots_rule_allows,
)
from backend.app.web_research_budget import WebResearchBudgetDeferred
from backend.app.web_search import (
    SearchProvider,
    SearchProviderError,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

WEB_RESEARCH_SOURCE_CODE = "bounded_public_web"
CONTENT_QUALITY_GATE_VERSION = RESEARCH_CANDIDATE_POLICY_VERSION
EVIDENCE_ROUTING_VERSION = "compliant-evidence-routing-v1"
ACTIVE_JOB_STATUSES = ("queued", "running", "partial", "budget_deferred")
ACTIVE_REQUEST_STATUSES = ("research_queued", "researching", "partial", "budget_deferred")
TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_NAMES = {"from", "spm"}
BLOCKED_DISCOVERY_DOMAINS = {
    "aiqicha.com",
    "aiqicha.baidu.com",
    "qcc.com",
    "qixin.com",
    "tianyancha.com",
    "qianfan.baidubce.com",
    "api.bochaai.com",
}
REGULATORY_DISCLOSURE_DOMAINS = {
    "bse.cn",
    "hkex.com.hk",
    "hkexnews.hk",
    "neeq.com.cn",
    "sse.com.cn",
    "szse.cn",
}
TRUSTED_MEDIA_DOMAINS = {
    "21jingji.com",
    "36kr.com",
    "caixin.com",
    "cnstock.com",
    "cs.com.cn",
    "eeo.com.cn",
    "iyiou.com",
    "news.cn",
    "pedaily.cn",
    "people.com.cn",
    "stcn.com",
    "thepaper.cn",
    "xinhuanet.com",
    "yicai.com",
}
PROFILE_AGGREGATOR_DOMAINS = {
    "baike.baidu.com",
    "baike.sogou.com",
    "baike.so.com",
    "itjuzi.com",
    "newseed.cn",
    "pitchhub.36kr.com",
}
PROFILE_OR_LISTING_PATH_PARTS = {
    "brand",
    "companies",
    "company",
    "enterprise",
    "project",
    "search",
    "tag",
}
OFFICIAL_PDF_SOURCE_TIERS = {
    "government",
    "regulatory_disclosure",
    "company_official",
}
SOURCE_ACCESS_API_METADATA_ONLY = "api_metadata_only"
SOURCE_ACCESS_AUTOMATIC_READ = "automatic_read_allowed"
SOURCE_ACCESS_ROBOTS_BLOCKED = "robots_blocked"
SOURCE_ACCESS_MANUAL_IMPORT = "authorized_manual_import_required"
SOURCE_ACCESS_OFFICIAL_DOCUMENT = "official_document"

GAP_FOLLOW_UP_TERMS = {
    "financial_operation": "经营 营收 利润 产量 停产 公告",
    "financing_cap_table": "融资 增资 股东 股权变更 公告",
    "contract_commercial": "中标 合同 订单 合作 公告",
    "product_technology": "新产品 技术 专利 认证 注册证 公告",
    "governance_people": "法定代表人 董事 高管 核心人员 变更",
    "legal_compliance": "行政处罚 诉讼 执行 破产 监管公告",
    "capacity_assets": "工厂 产能 生产线 投产 环评 资产",
    "exit_liquidity": "IPO 上市辅导 备案 并购 回购 公告",
}
GAP_FOLLOW_UP_PRIORITY = (
    "exit_liquidity",
    "financing_cap_table",
    "contract_commercial",
    "legal_compliance",
    "product_technology",
    "capacity_assets",
    "governance_people",
    "financial_operation",
)

EVENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "exit_liquidity",
        (
            "ipo",
            "上市",
            "辅导",
            "挂牌",
            "并购",
            "回购",
            "股权转让",
            "递表",
            "上市申请",
            "聆讯",
            "境外上市备案",
            "全流通备案",
            "招股",
            "公开发售",
        ),
    ),
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
EVENT_CHANGE_SIGNALS: dict[str, tuple[str, ...]] = {
    "exit_liquidity": (
        "辅导备案",
        "申报",
        "问询",
        "递表",
        "提交上市申请",
        "递交上市申请",
        "通过聆讯",
        "上市聆讯",
        "境外上市备案",
        "全流通备案",
        "上市获备案",
        "上市备案通过",
        "启动招股",
        "公开招股",
        "开始招股",
        "公开发售",
        "正式上市",
        "挂牌上市",
        "完成上市",
        "挂牌",
        "并购",
        "收购",
        "回购",
        "股权转让",
        "撤回",
        "中止",
        "终止",
    ),
    "legal_compliance": (
        "被处罚",
        "行政处罚",
        "立案",
        "判决",
        "被执行",
        "限制高消费",
        "失信",
        "冻结",
        "查封",
        "吊销",
        "破产",
        "清算",
    ),
    "financing_cap_table": (
        "完成融资",
        "获得融资",
        "获投",
        "增资",
        "减资",
        "新增股东",
        "股东变更",
        "控制权变更",
        "股权转让",
        "股权质押",
        "股权冻结",
    ),
    "contract_commercial": (
        "中标",
        "签署合同",
        "签订合同",
        "获得订单",
        "达成合作",
        "成为供应商",
        "完成首单",
        "实现量产",
    ),
    "product_technology": (
        "发布新产品",
        "推出新产品",
        "获批",
        "取得注册证",
        "通过认证",
        "专利授权",
        "进入临床",
        "启动临床",
        "产品召回",
        "研发暂停",
        "研发终止",
    ),
    "governance_people": (
        "法定代表人变更",
        "董事变更",
        "监事变更",
        "高管变更",
        "任命",
        "辞任",
        "离职",
        "裁员",
        "组织重组",
    ),
    "capacity_assets": (
        "开工",
        "竣工",
        "投产",
        "扩产",
        "新建工厂",
        "新建产线",
        "资产抵押",
        "资产查封",
        "场所关闭",
        "获得环评",
    ),
    "financial_operation": (
        "营收增长",
        "营收下降",
        "收入增长",
        "收入下降",
        "利润增长",
        "利润下降",
        "实现盈利",
        "出现亏损",
        "扭亏",
        "欠薪",
        "停工",
        "停产",
        "销量增长",
        "销量下降",
        "出货增长",
        "出货下降",
    ),
}
EVENT_CHANGE_PATTERNS: dict[str, tuple[str, ...]] = {
    "exit_liquidity": (
        r"(?:递交|提交).{0,12}(?:上市申请|招股书)",
        r"通过.{0,8}(?:上市)?聆讯",
        r"(?:境外上市|全流通).{0,20}备案",
    ),
    "financing_cap_table": (
        r"完成.{0,12}融资",
        r"获得.{0,12}融资",
        r"获(?:得)?.{0,12}投资",
    ),
    "contract_commercial": (
        r"签(?:署|订).{0,12}合同",
        r"获得.{0,12}订单",
        r"达成.{0,12}合作",
    ),
    "product_technology": (
        r"发布.{0,12}产品",
        r"推出.{0,12}产品",
        r"通过.{0,12}认证",
    ),
}


class WebResearchAccessError(RuntimeError):
    pass


@dataclass(frozen=True)
class _ContentQualityDecision:
    eligible: bool
    event_type: str | None
    supporting_excerpt: str | None
    reasons: tuple[str, ...]
    source_published_at: datetime | None
    observed_at: datetime
    recency_cutoff_at: datetime
    occurred_at: datetime | None = None
    event_date_status: str = "unknown"

    def to_dict(self) -> dict[str, object]:
        return {
            "version": CONTENT_QUALITY_GATE_VERSION,
            "change_recognition_version": "explicit-change-v2",
            "status": "eligible" if self.eligible else "internal_only",
            "event_type": self.event_type,
            "supporting_excerpt_hash": (
                _sha256(self.supporting_excerpt) if self.supporting_excerpt else None
            ),
            "reasons": list(self.reasons),
            "date_basis": "source_published_at",
            "source_published_at": (
                self.source_published_at.isoformat() if self.source_published_at else None
            ),
            "observed_at": self.observed_at.isoformat(),
            "recency_cutoff_at": self.recency_cutoff_at.isoformat(),
            "event_date_status": self.event_date_status,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
        }


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
    cost_summary: dict | None = None

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
            "estimated_cost": (
                self.cost_summary["amount"]
                if self.cost_summary
                else None
                if self.external_calls or self.error_code
                else "0"
            ),
            "cost_status": (
                self.cost_summary["status"]
                if self.cost_summary
                else "unknown"
                if self.external_calls or self.error_code
                else "confirmed_free"
            ),
            "cost_summary": self.cost_summary,
            "error_code": self.error_code,
        }


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _normalized_identity(value: str) -> str:
    return "".join(value.split()).casefold()


def _normalized_identity_for_match(value: str) -> str:
    """Normalize equivalent Unicode forms without changing persisted cache identities."""
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


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
        "evidence_routing_version": EVIDENCE_ROUTING_VERSION,
        "category_coverage_version": COVERAGE_VERSION,
        "source_routes": source_routes(policy.primary_provider, policy.fallback_provider),
        "modules": {module: "pending" for module in RESEARCH_MODULES},
        "search_groups": {
            code: {"status": "pending", "providers": {}} for code, _ in SEARCH_GROUPS
        },
        "candidates": [],
        "candidate_index": 0,
        "documents": [],
        "source_recovery": {"status": "not_requested", "attempts": 0},
        "gap_follow_up": {"status": "not_requested", "attempts": 0},
        "stats": {
            "search_cache_hits": 0,
            "document_cache_hits": 0,
            "search_calls": 0,
            "fetch_calls": 0,
            "downloaded_bytes": 0,
            "events_created": 0,
            "quality_gate_passed": 0,
            "internal_candidates": 0,
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
                        (PersonalCompanyRequest.status == "in_review")
                        & (PersonalCompanyRequest.last_error_code == "curator_identity_confirmed")
                        & (PersonalCompanyRequest.cancel_requested_at.is_(None))
                    )
                    if policy.incremental_research_enabled
                    else False,
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
        if job is not None and job.trigger_type == "watchlist":
            # Finish the bounded check first; the full manual request then reuses its cache.
            continue
        if job is None:
            coverage = _initial_coverage(policy)
            identity = session.get(IdentityResearchState, request.id)
            if identity:
                # Identity and business research have separate task caps. Preserve
                # the former for audit, but do not exhaust business discovery before it starts.
                coverage["identity_usage"] = {
                    metric: int(identity.progress.get(metric, 0))
                    for metric in ("search_calls", "fetch_calls", "downloaded_bytes")
                }
            job = CompanyResearchJob(
                company_id=company.id,
                created_by_user_id=request.owner_user_id,
                status="queued",
                current_stage=f"search:{SEARCH_GROUPS[0][0]}",
                policy_version=policy.version,
                coverage=coverage,
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


def _job_cost_summary(session, job):
    baseline = job.coverage.get("budget_baseline", job.coverage.get("stats", {}))
    legacy_calls = int(baseline.get("search_calls", 0)) + int(baseline.get("fetch_calls", 0))
    return budget.summary(session, f"web-research:{job.id}", legacy_calls=legacy_calls)


def _record_watch_outcome(session, job):
    policy = session.info.get("watchlist_policy")
    user_id = session.info.get("watchlist_user_id")
    if policy is not None and user_id is not None:
        user = session.get(User, user_id)
        monitoring.record_outcome(session, user, job, policy, commit=False)


def _cancel_job(session: Session, job: CompanyResearchJob) -> WebResearchWorkerResult:
    now = utc_now()
    coverage = dict(job.coverage)
    coverage.pop("_robots_rule_cache", None)
    budget.recover(session, task_key=f"web-research:{job.id}", cancelled=True)
    coverage["cost_summary"] = _job_cost_summary(session, job)
    job.coverage = coverage
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
    _record_watch_outcome(session, job)
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
                CompanyResearchJob.trigger_type != "watchlist",
                not policy.watchlist.enabled,
                ~select(CompanyWatchSchedule.company_id)
                .where(
                    CompanyWatchSchedule.company_id == CompanyResearchJob.company_id,
                    CompanyWatchSchedule.cooldown_until > now,
                )
                .exists(),
            ),
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
    if _job_should_stop(session, job):
        _cancel_job(session, job)
        _restore_worker_context(session, user)
        monitoring.record_outcome(session, user, job, policy.watchlist)
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
    return f"{query_subject(company)} {terms}"


def _official_host(company: Company) -> str | None:
    if not company.official_website:
        return None
    try:
        host = (urlsplit(company.official_website).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    return host.removeprefix("www.") or None


def _host_matches_domain(host: str, domain: str) -> bool:
    normalized_host = host.lower().rstrip(".").removeprefix("www.")
    normalized_domain = domain.lower().rstrip(".").removeprefix("www.")
    return normalized_host == normalized_domain or normalized_host.endswith(f".{normalized_domain}")


def _host_matches_any(host: str, domains: set[str]) -> bool:
    return any(_host_matches_domain(host, domain) for domain in domains)


def _subject_match(company: Company, result: SearchResult) -> bool:
    haystack = _normalized_identity_for_match(f"{result.title}。{result.snippet}")
    if matched_name(company, haystack):
        return True
    if company.credit_code and _normalized_identity_for_match(company.credit_code) in haystack:
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
    if not host or _host_matches_any(host, BLOCKED_DISCOVERY_DOMAINS):
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


@dataclass(frozen=True)
class _SourceRank:
    tier: str
    score: int
    reasons: tuple[str, ...]


def _profile_or_listing_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if _host_matches_any(host, PROFILE_AGGREGATOR_DOMAINS):
        return True
    if _host_matches_domain(host, "kanzhun.com") and parsed.path.casefold().startswith(
        "/firm/info/"
    ):
        return True
    path_parts = {part.casefold() for part in parsed.path.split("/") if part}
    if not path_parts:
        return True
    return bool(path_parts & PROFILE_OR_LISTING_PATH_PARTS)


def _source_rank(company: Company, result: SearchResult) -> _SourceRank:
    host = (urlsplit(result.url).hostname or "").lower().rstrip(".")
    official_host = _official_host(company)
    if host == "gov.cn" or host.endswith(".gov.cn"):
        return _SourceRank("government", 500, ("government_domain",))
    if _host_matches_any(host, REGULATORY_DISCLOSURE_DOMAINS):
        return _SourceRank("regulatory_disclosure", 480, ("regulatory_domain",))
    if official_host and _host_matches_domain(host, official_host):
        return _SourceRank("company_official", 450, ("verified_company_domain",))
    if _profile_or_listing_url(result.url):
        return _SourceRank("profile_or_listing", 100, ("profile_or_listing_page",))
    if _host_matches_any(host, TRUSTED_MEDIA_DOMAINS):
        return _SourceRank("trusted_media_article", 350, ("reviewed_media_domain",))
    return _SourceRank("locatable_source_page", 250, ("specific_https_page",))


def _search_result_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = value.strip()
    matched = re.search(r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})", candidate)
    if matched:
        candidate = "-".join(part.zfill(2) for part in matched.groups())
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


def _evidence_checked_at(value: object) -> datetime | None:
    # Check timestamps need an explicit time and timezone; do not use the
    # search-date parser, which intentionally reduces input to a calendar date.
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _search_date_status(
    result: SearchResult,
    policy: WebResearchPolicy,
    *,
    observed_at: datetime | None = None,
) -> str:
    published_at = _search_result_datetime(result.published_at)
    if published_at is None:
        return "unknown"
    observed = _aware(observed_at or utc_now())
    if published_at > observed + timedelta(days=1):
        return "future"
    if published_at < observed - timedelta(days=policy.recent_change_window_days):
        return "old"
    return "recent"


def _qualified_subject_result(
    company: Company,
    result: SearchResult,
    policy: WebResearchPolicy,
) -> bool:
    return _qualification_reason(company, result, policy) == "qualified"


def _qualification_reason(
    company: Company,
    result: SearchResult,
    policy: WebResearchPolicy,
) -> str:
    if not _subject_match(company, result):
        return "subject_mismatch"
    if _canonical_candidate_url(result.url) is None:
        return "blocked_or_unsafe_url"
    if _source_rank(company, result).tier == "profile_or_listing":
        return "profile_or_listing"
    date_status = _search_date_status(result, policy)
    if date_status == "old":
        return "published_at_old"
    if date_status == "future":
        return "published_at_future"
    return "qualified"


def _qualification_reason_counts(
    company: Company,
    results: list[SearchResult],
    policy: WebResearchPolicy,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        reason = _qualification_reason(company, result, policy)
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _verified_official_site_response(
    company: Company,
    policy: WebResearchPolicy,
) -> SearchResponse | None:
    if not company.official_website:
        return None
    canonical_url = _canonical_candidate_url(company.official_website)
    if canonical_url is None:
        return None
    result = SearchResult(
        provider_record_id=f"verified-company-website:{company.id}",
        title=f"{company.legal_name}已核验官方网站",
        url=canonical_url,
        snippet=f"{company.legal_name}已核验官方网站。",
        source_name="公司已核验官网",
        published_at=None,
    )
    if not _qualified_subject_result(company, result, policy):
        return None
    return SearchResponse(
        provider_code="verified_company_identity",
        request_id=None,
        results=[result],
        response_hash=_sha256(f"verified-company-website:{company.id}:{canonical_url}"),
        external_calls=0,
    )


def _result_score(
    company: Company,
    result: SearchResult,
    policy: WebResearchPolicy,
) -> tuple[int, float, str]:
    rank = _source_rank(company, result)
    score = rank.score
    if _normalized_identity_for_match(company.legal_name) in _normalized_identity_for_match(
        result.title
    ):
        score += 20
    result_text = f"{result.title}{result.snippet}".lower()
    if any(keyword in result_text for _, terms in EVENT_KEYWORDS for keyword in terms):
        score += 10
    published_at = _search_result_datetime(result.published_at)
    date_status = _search_date_status(result, policy)
    if date_status == "recent":
        score += 25
    elif date_status in {"old", "future"}:
        score -= 1_000
    return score, published_at.timestamp() if published_at else 0.0, result.url


def _candidate_payload(
    company: Company,
    result: SearchResult,
    *,
    provider_code: str,
    query_kind: str,
    policy: WebResearchPolicy,
) -> dict[str, object] | None:
    if not _qualified_subject_result(company, result, policy):
        return None
    canonical_url = _canonical_candidate_url(result.url)
    if canonical_url is None:
        return None
    source_rank = _source_rank(company, result)
    return {
        **result.to_dict(),
        "url": canonical_url,
        "discovered_by": [provider_code],
        "query_kind": query_kind,
        "query_kinds": [query_kind],
        "coverage_category": _event_classification(result.title, result.snippet),
        "source_tier": source_rank.tier,
        "source_rank_reasons": list(source_rank.reasons),
        "search_date_status": _search_date_status(result, policy),
        "source_access_status": SOURCE_ACCESS_API_METADATA_ONLY,
        "source_access_reason": "search_discovery_metadata_only",
    }


def _cached_response(
    session: Session,
    company: Company,
    provider_code: str,
    query: str,
    now: datetime,
    *,
    max_age_days: int | None = None,
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
    if entry is None or (
        max_age_days is not None and _aware(entry.fetched_at) <= now - timedelta(days=max_age_days)
    ):
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


def _incremental_time_exhausted(job: CompanyResearchJob, policy: WebResearchPolicy) -> bool:
    started = _evidence_checked_at(job.coverage.get("started_at"))
    return bool(
        policy.incremental_research_enabled
        and job.trigger_type != "watchlist"
        and started is not None
        and (utc_now() - started).total_seconds() >= policy.max_elapsed_seconds
    )


def _reserve_job_call(
    session, user, job, company, policy, *, provider, operation, token, calls, unit_price, metrics
):
    if _incremental_time_exhausted(job, policy):
        raise WebResearchBudgetDeferred("execution time limit reached")
    coverage = dict(job.coverage)
    baseline = coverage.setdefault("budget_baseline", dict(coverage.get("stats", {})))
    job.coverage = coverage
    is_search = operation == "company_discovery"
    usage = budget.reserve(
        session,
        user,
        policy,
        task_key=f"web-research:{job.id}",
        subject=budget.subject_key(company.credit_code, company.id),
        company_id=company.id,
        provider=provider,
        operation=operation,
        token=token,
        calls=calls,
        unit_price=unit_price,
        task_limit=policy.max_search_calls_per_job
        if is_search
        else policy.max_fetch_requests_per_job,
        task_baseline=int(baseline.get("search_calls" if is_search else "fetch_calls", 0)),
        metrics=metrics,
    )

    def cancelled():
        session.refresh(job)
        return _job_should_stop(session, job)

    budget.dispatch(session, user, usage, policy, cancelled=cancelled)
    return usage


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
    cached = _cached_response(
        session, company, provider.code, query, utc_now(), max_age_days=policy.search_cache_ttl_days
    )
    if cached is not None:
        _record_search_cache_hit(session, job)
        return cached, True
    token = f"search:{query_kind}:{provider.code}"
    previous = session.scalar(
        select(UsageLedger).where(
            UsageLedger.idempotency_key == _sha256(f"web-research:{job.id}:{token}")
        )
    )
    if (
        previous is not None
        and previous.usage_state == "settled"
        and previous.metrics.get("status") == "failed"
    ):
        # A known failed attempt may be replayed as a result, never as another paid call.
        raise SearchProviderError(
            previous.metrics.get("error_code", "request_failed"),
            "previous attempt already accounted",
            external_calls=0,
            http_status=previous.metrics.get("http_status"),
        )
    usage = _reserve_job_call(
        session,
        user,
        job,
        company,
        policy,
        provider=f"web_search_{provider.code}",
        operation="company_discovery",
        token=f"search:{query_kind}:{provider.code}",
        calls=1,
        unit_price=budget.search_price(provider, policy),
        metrics={"query_kind": query_kind},
    )
    try:
        response = provider.search(
            SearchRequest(query=query, max_results=policy.max_results_per_search)
        )
    except SearchProviderError as error:
        job.external_calls += error.external_calls
        coverage = dict(job.coverage)
        stats = dict(coverage.get("stats", {}))
        stats["search_calls"] = int(stats.get("search_calls", 0)) + error.external_calls
        coverage["stats"] = stats
        job.coverage = coverage
        safe_diagnostic = {
            "query_kind": query_kind,
            "status": "failed",
            "error_code": error.code,
            "http_status": error.http_status,
        }
        budget.settle(session, usage, calls=error.external_calls, metrics=safe_diagnostic)
        session.commit()
        raise
    job.external_calls += response.external_calls
    coverage = dict(job.coverage)
    stats = dict(coverage.get("stats", {}))
    stats["search_calls"] = int(stats.get("search_calls", 0)) + response.external_calls
    coverage["stats"] = stats
    job.coverage = coverage
    _store_search_response(session, company, response, query_kind, query, policy)
    budget.settle(
        session,
        usage,
        calls=response.external_calls,
        metrics={
            "query_kind": query_kind,
            "status": "completed",
            "request_id": response.request_id,
            "results": len(response.results),
        },
    )
    session.commit()
    return response, False


def _record_search_cache_hit(session: Session, job: CompanyResearchJob) -> None:
    coverage = dict(job.coverage)
    stats = dict(coverage.get("stats", {}))
    stats["search_cache_hits"] = int(stats.get("search_cache_hits", 0)) + 1
    coverage["stats"] = stats
    job.coverage = coverage
    for request in _active_linked_requests(session, job.id):
        request.cache_hits += 1
    session.commit()


def _refresh_job(session: Session, job_id: UUID) -> CompanyResearchJob:
    session.expire_all()
    job = session.get(CompanyResearchJob, job_id)
    if job is None:
        raise RuntimeError("research job disappeared")
    return job


def _job_should_stop(session: Session, job: CompanyResearchJob) -> bool:
    if job.trigger_type == "watchlist":
        user = session.get(User, session.info["watchlist_user_id"])
        return not monitoring.allowed(session, user, job)
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
    primary_failure_code: str | None = None
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
        primary_failure_code = error.code
        provider_state[primary.code] = {
            "status": "failed",
            "error_code": error.code,
            "http_status": error.http_status,
        }
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
    primary_qualified_results = sum(
        1
        for response in responses
        for result in response.results
        if _qualified_subject_result(company, result, policy)
    )
    if primary.code in provider_state:
        state = dict(provider_state[primary.code])
        state["subject_results"] = primary_subject_results
        state["qualified_subject_results"] = primary_qualified_results
        if primary_failure_code is None:
            primary_results = [result for response in responses for result in response.results]
            state["filter_reasons"] = _qualification_reason_counts(company, primary_results, policy)
        provider_state[primary.code] = state
    fallback_reason = (
        "primary_failed"
        if primary_failure_code is not None
        else "insufficient_qualified_subject_results"
    )
    if primary_qualified_results < policy.fallback_min_subject_results:
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
                "subject_results": sum(
                    1 for result in response.results if _subject_match(company, result)
                ),
                "qualified_subject_results": sum(
                    1
                    for result in response.results
                    if _qualified_subject_result(company, result, policy)
                ),
                "filter_reasons": _qualification_reason_counts(company, response.results, policy),
                "reason": fallback_reason,
            }
        except SearchProviderError as error:
            provider_state[fallback.code] = {
                "status": "failed",
                "error_code": error.code,
                "http_status": error.http_status,
                "reason": fallback_reason,
            }
    else:
        cached_fallback = _cached_response(
            session,
            company,
            fallback.code,
            query,
            utc_now(),
            max_age_days=policy.search_cache_ttl_days,
        )
        if cached_fallback is None:
            provider_state[fallback.code] = {
                "status": "not_called",
                "reason": "primary_has_qualified_subject_results",
            }
        else:
            responses.append(cached_fallback)
            cache_hits += 1
            _record_search_cache_hit(session, job)
            provider_state[fallback.code] = {
                "status": "cache_fused",
                "results": len(cached_fallback.results),
                "subject_results": sum(
                    1 for result in cached_fallback.results if _subject_match(company, result)
                ),
                "qualified_subject_results": sum(
                    1
                    for result in cached_fallback.results
                    if _qualified_subject_result(company, result, policy)
                ),
                "filter_reasons": _qualification_reason_counts(
                    company,
                    cached_fallback.results,
                    policy,
                ),
                "reason": "valid_existing_cache_fused_without_provider_call",
            }
    _restore_worker_context(session, user)
    job = _refresh_job(session, job.id)
    if _job_should_stop(session, job):
        return _cancel_job(session, job)

    candidate_responses = list(responses)
    if group_code == SEARCH_GROUPS[-1][0]:
        official_site_response = _verified_official_site_response(company, policy)
        if official_site_response is not None:
            candidate_responses.append(official_site_response)

    merged: dict[str, dict[str, object]] = {}
    for response in candidate_responses:
        for result in response.results:
            candidate = _candidate_payload(
                company,
                result,
                provider_code=response.provider_code,
                query_kind=group_code,
                policy=policy,
            )
            if candidate is None or (
                job.trigger_type == "watchlist"
                and candidate.get("coverage_category") not in monitoring.CATEGORIES
            ):
                continue
            canonical_url = str(candidate["url"])
            current = merged.get(canonical_url)
            if current is None:
                merged[canonical_url] = candidate
            else:
                discovered = list(current.get("discovered_by", []))
                if response.provider_code not in discovered:
                    discovered.append(response.provider_code)
                current["discovered_by"] = discovered
                if _result_score(company, result, policy) > _result_score(
                    company, SearchResult.from_dict(current), policy
                ):
                    candidate["discovered_by"] = discovered
                    merged[canonical_url] = candidate
    coverage = dict(job.coverage)
    groups = dict(coverage.get("search_groups", {}))
    successful_providers = [
        code
        for code, state in provider_state.items()
        if state.get("status") in {"completed", "cache_hit", "cache_fused"}
    ]
    checked_at = (
        session.scalar(
            select(func.max(WebSearchCacheEntry.fetched_at)).where(
                WebSearchCacheEntry.company_id == company.id,
                WebSearchCacheEntry.provider_code.in_(successful_providers),
                WebSearchCacheEntry.query_hash == _sha256(query),
                WebSearchCacheEntry.identity_fingerprint == _identity_fingerprint(company),
            )
        )
        if successful_providers
        else None
    )
    groups[group_code] = {
        "status": "completed",
        "query_text": query,
        "providers": provider_state,
        "attempted_at": utc_now().isoformat(),
        "checked_at": _aware(checked_at).isoformat() if checked_at is not None else None,
        "cache_reused": cache_hits > 0,
        "subject_results": len(merged),
        "qualified_subject_results": sum(
            1
            for item in merged.values()
            if _qualified_subject_result(company, SearchResult.from_dict(item), policy)
        ),
    }
    coverage["search_groups"] = groups
    modules = dict(coverage.get("modules", {}))
    for module in SEARCH_GROUP_MODULES[group_code]:
        modules[module] = "search_completed"
    coverage["modules"] = modules
    existing_candidates = {
        str(item.get("url")): dict(item)
        for item in coverage.get("candidates", [])
        if isinstance(item, dict) and item.get("url")
    }
    for canonical_url, candidate in merged.items():
        current = existing_candidates.get(canonical_url)
        if current is None:
            existing_candidates[canonical_url] = candidate
            continue
        discovered = list(current.get("discovered_by", []))
        for provider_code in candidate.get("discovered_by", []):
            if provider_code not in discovered:
                discovered.append(provider_code)
        query_kinds = list(current.get("query_kinds", [current.get("query_kind")]))
        if group_code not in query_kinds:
            query_kinds.append(group_code)
        if _result_score(company, SearchResult.from_dict(candidate), policy) > _result_score(
            company, SearchResult.from_dict(current), policy
        ):
            candidate["discovered_by"] = discovered
            candidate["query_kinds"] = query_kinds
            existing_candidates[canonical_url] = candidate
        else:
            current["discovered_by"] = discovered
            current["query_kinds"] = query_kinds
    candidates = list(existing_candidates.values())
    candidates.sort(
        key=lambda item: _result_score(company, SearchResult.from_dict(item), policy),
        reverse=True,
    )
    coverage["candidates"] = candidates[: policy.max_candidate_urls]
    job.coverage = coverage
    group_codes = (
        [monitoring.GROUP]
        if job.trigger_type == "watchlist"
        else [code for code, _ in SEARCH_GROUPS]
    )
    next_groups = [
        code for code in group_codes if groups.get(code, {}).get("status") != "completed"
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


def _readability_fallback_group(
    coverage: dict[str, object], policy: WebResearchPolicy
) -> str | None:
    candidates = coverage.get("candidates", [])
    stats = coverage.get("stats", {})
    if (
        int(coverage.get("candidate_index", 0)) < len(candidates)
        or len(candidates) >= policy.max_candidate_urls
        or int(stats.get("fetch_calls", 0)) >= policy.max_fetch_requests_per_job
        or int(stats.get("downloaded_bytes", 0)) >= policy.max_download_bytes_per_job
        or sum(
            item.get("status") in {"created", "reused"} for item in coverage.get("documents", [])
        )
        >= policy.max_documents_per_job
    ):
        return None
    documents = {item["url"]: item for item in coverage.get("documents", [])}
    for code, _ in SEARCH_GROUPS:
        group = coverage.get("search_groups", {}).get(code, {})
        providers = group.get("providers", {})
        if providers.get(policy.fallback_provider, {}).get("status") != "not_called":
            continue
        if (
            providers.get(policy.primary_provider, {}).get("qualified_subject_results", 0)
            < policy.fallback_min_subject_results
        ):
            continue
        urls = {
            item["url"]
            for item in candidates
            if code in item.get("query_kinds", [item.get("query_kind")])
        }
        if urls and all(documents.get(url, {}).get("status") == "failed" for url in urls):
            return code
    return None


def _process_readability_fallback(session, user, job, company, policy, providers, group_code):
    # Replay uses the original query/provider token, so a committed response or failure
    # is reused even if the worker stopped before saving the next stage.
    if (
        not policy.incremental_research_enabled
        or job.trigger_type == "watchlist"
        or _readability_fallback_group(job.coverage, policy) != group_code
    ):
        return _finalize(session, job)
    provider = providers[policy.fallback_provider]
    query = job.coverage["search_groups"][group_code].get("query_text") or _query_for(
        company, dict(SEARCH_GROUPS)[group_code]
    )
    response = None
    cached = False
    state = {"reason": "primary_documents_unreadable", "attempted_at": utc_now().isoformat()}
    try:
        response, cached = _provider_response(
            session,
            user,
            job,
            company,
            provider,
            query_kind=group_code,
            query=query,
            policy=policy,
        )
        state.update(
            status="cache_hit" if cached else "completed",
            results=len(response.results),
            subject_results=sum(_subject_match(company, item) for item in response.results),
            qualified_subject_results=sum(
                _qualified_subject_result(company, item, policy) for item in response.results
            ),
            filter_reasons=_qualification_reason_counts(company, response.results, policy),
        )
    except SearchProviderError as error:
        state.update(status="failed", error_code=error.code, http_status=error.http_status)
    _restore_worker_context(session, user)
    job = _refresh_job(session, job.id)
    if _job_should_stop(session, job):
        return _cancel_job(session, job)
    coverage = dict(job.coverage)
    candidates = list(coverage.get("candidates", []))
    seen = {item["url"] for item in candidates}
    robots = normalized_robots_rule_cache(coverage.get("_robots_rule_cache"), policy.user_agent)
    added = []
    excluded = {"duplicate_url": 0, "robots_disallowed": 0}
    for result in response.results if response else []:
        candidate = _candidate_payload(
            company, result, provider_code=provider.code, query_kind=group_code, policy=policy
        )
        if candidate is None:
            continue
        url = candidate["url"]
        if url in seen:
            excluded["duplicate_url"] += 1
            continue
        seen.add(url)
        parsed = urlsplit(url)
        rule = robots.get(f"{parsed.scheme}://{parsed.netloc}")
        if rule is not None and not robots_rule_allows(rule, url, policy.user_agent):
            excluded["robots_disallowed"] += 1
            continue
        added.append(candidate)
    added.sort(
        key=lambda item: _result_score(company, SearchResult.from_dict(item), policy), reverse=True
    )
    added = added[: max(0, policy.max_candidate_urls - len(candidates))]
    # All existing candidates were processed; never reorder that prefix or replace evidence.
    coverage["candidates"] = [*candidates, *added]
    state.update(candidate_count_added=len(added), excluded_candidates=excluded)
    groups = dict(coverage["search_groups"])
    group = dict(groups[group_code])
    group["providers"] = {**group["providers"], provider.code: state}
    group["cache_reused"] = bool(group.get("cache_reused") or cached)
    if response is not None:
        checked = session.scalar(
            select(WebSearchCacheEntry.fetched_at).where(
                WebSearchCacheEntry.company_id == company.id,
                WebSearchCacheEntry.provider_code == provider.code,
                WebSearchCacheEntry.query_hash == _sha256(query),
                WebSearchCacheEntry.identity_fingerprint == _identity_fingerprint(company),
            )
        )
        if checked is not None:
            previous = _evidence_checked_at(group.get("checked_at"))
            group["checked_at"] = max(_aware(checked), previous or _aware(checked)).isoformat()
    group["subject_results"] = int(group.get("subject_results", 0)) + len(added)
    group["qualified_subject_results"] = int(group.get("qualified_subject_results", 0)) + len(added)
    groups[group_code] = group
    coverage["search_groups"] = groups
    job.coverage = coverage
    job.current_stage = "fetch"
    job.status = "partial"
    job.leased_until = None
    job.heartbeat_at = utc_now()
    for request in _active_linked_requests(session, job.id):
        request.status = "partial"
        request.leased_until = None
        request.heartbeat_at = job.heartbeat_at
    session.commit()
    return WebResearchWorkerResult(
        status="partial",
        job_id=job.id,
        company_id=job.company_id,
        stage="fetch",
        external_calls=job.external_calls,
        cache_hits=int(cached),
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
    content = f"{title}。{excerpt}"
    normalized_content = _normalized_identity_for_match(content)
    current_identity_match = bool(matched_name(company, normalized_content))
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
        has_category = any(keyword.casefold() in content for keyword in keywords)
        has_change = any(
            signal.casefold() in content for signal in EVENT_CHANGE_SIGNALS[event_type]
        ) or any(
            re.search(pattern, content, flags=re.IGNORECASE)
            for pattern in EVENT_CHANGE_PATTERNS.get(event_type, ())
        )
        if event_type == "exit_liquidity" and not has_change:
            # Only completed-listing wording; plans and failed attempts are not completion.
            has_change = bool(
                re.search(r"成功(?:赴港|(?:在|于)\s*[\u4e00-\u9fff ]{0,16})?\s*上市", content)
                and not re.search(
                    r"拟|计划|预计|有望|争取|尚未|未能|没有|未成功|不曾|将|如果|若|目标", content
                )
            )
        if has_category and has_change:
            return event_type
    return None


def _content_passages(excerpt: str) -> list[str]:
    passages: list[str] = []
    for item in re.split(r"\n+|(?<=[。！？!?；;])", excerpt):
        normalized = " ".join(item.split()).strip()
        if not normalized:
            continue
        if len(normalized) <= 500:
            passages.append(normalized)
            continue
        start = 0
        while start < len(normalized):
            passages.append(normalized[start : start + 500])
            start += 400
    return passages


def _body_identity_match(company: Company, value: str) -> bool:
    return bool(matched_name(company, value))


def _explicit_event_date(company: Company, passage: str) -> tuple[datetime | None, str]:
    # A date must lead the supporting sentence. Do not borrow years from publication
    # metadata or dates describing a company's founding/history later in the passage.
    match = re.match(r"^\s*(\d{4})年(\d{1,2})月(\d{1,2})日[，,\s]", passage)
    if match:
        remainder = passage[match.end() :]
        if not _normalized_identity_for_match(remainder).startswith(
            _normalized_identity_for_match(matched_name(company, remainder) or company.legal_name)
        ) or re.search(r"\d{1,2}月\d{1,2}日", remainder):
            return None, "body_date_ambiguous"
        try:
            return datetime(
                *map(int, match.groups()), tzinfo=ZoneInfo("Asia/Shanghai")
            ), "explicit_body_date"
        except ValueError:
            return None, "invalid_body_date"
    if re.match(r"^\s*\d{1,2}月\d{1,2}日[，,\s]", passage):
        return None, "body_date_year_unknown"
    return None, "unknown"


def _content_quality_decision(
    company: Company,
    *,
    title: str,
    excerpt: str,
    published_at: datetime | None,
    observed_at: datetime,
    policy: WebResearchPolicy,
) -> _ContentQualityDecision:
    reasons: list[str] = []
    observed = _aware(observed_at)
    published = _aware(published_at) if published_at is not None else None
    recency_cutoff = observed - timedelta(days=policy.recent_change_window_days)
    if not excerpt:
        reasons.append("missing_clean_body")
    if published is None:
        reasons.append("missing_reliable_published_at")
    elif published > observed + timedelta(days=1):
        reasons.append("published_at_in_future")
    elif published < recency_cutoff:
        reasons.append("published_before_recent_window")
    passages = _content_passages(excerpt)
    identity_passages = [item for item in passages if _body_identity_match(company, item)]
    if not identity_passages:
        reasons.append("subject_not_in_clean_body")

    matched_type: str | None = None
    supporting_excerpt: str | None = None
    occurred_at: datetime | None = None
    event_date_status = "unknown"
    for passage in identity_passages:
        # The subject and the change must be supported by the same body passage.
        # A headline keyword must not combine with an unrelated company profile
        # paragraph to manufacture a user-visible lead.
        event_type = _event_classification("", passage)
        if event_type is None:
            continue
        matched_type = event_type
        supporting_excerpt = f"{title}。{passage}"[:1000]
        occurred_at, event_date_status = _explicit_event_date(company, passage)
        if occurred_at is not None and occurred_at > observed:
            occurred_at = None
            event_date_status = "future_body_date"
            reasons.append("event_date_in_future")
        break
    if matched_type is None:
        unrelated_change = any(_event_classification("", item) for item in passages)
        reasons.append(
            "subject_not_in_change_passage"
            if unrelated_change and identity_passages
            else "no_material_change_signal"
        )
    if reasons:
        return _ContentQualityDecision(
            eligible=False,
            event_type=matched_type,
            supporting_excerpt=supporting_excerpt,
            reasons=tuple(dict.fromkeys(reasons)),
            source_published_at=published,
            observed_at=observed,
            recency_cutoff_at=recency_cutoff,
            occurred_at=occurred_at,
            event_date_status=event_date_status,
        )
    return _ContentQualityDecision(
        eligible=True,
        event_type=matched_type,
        supporting_excerpt=supporting_excerpt,
        reasons=("content_quality_gate_passed",),
        source_published_at=published,
        observed_at=observed,
        recency_cutoff_at=recency_cutoff,
        occurred_at=occurred_at,
        event_date_status=event_date_status,
    )


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
    quality: _ContentQualityDecision,
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
    excerpt = (discovered.excerpt or "").strip()[:1500]
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
            "content_extraction": dict(discovered.metadata),
            "quality_gate": quality.to_dict(),
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
    policy: WebResearchPolicy,
    *,
    new_document: bool,
    actor: User | None = None,
    allowed_categories: tuple[str, ...] | None = None,
) -> tuple[Event | None, bool, _ContentQualityDecision]:
    excerpt = str(document.payload.get("excerpt") or "").strip()
    quality = _content_quality_decision(
        company,
        title=document.title,
        excerpt=excerpt,
        published_at=document.published_at,
        observed_at=document.observed_at,
        policy=policy,
    )
    if allowed_categories is not None and quality.event_type not in allowed_categories:
        quality = replace(
            quality, eligible=False, reasons=(*quality.reasons, "category_outside_monitoring_scope")
        )
    if not quality.eligible or quality.event_type is None or quality.supporting_excerpt is None:
        return None, False, quality
    event_type = quality.event_type
    if policy.incremental_research_enabled and event_type == "financing_cap_table":
        from backend.app.financing_storage import persist_financing_document

        event, created = persist_financing_document(session, company, document, source, actor)
        if event is None:
            quality = replace(
                quality,
                eligible=False,
                reasons=(*quality.reasons, "financing_recipient_or_fields_unresolved"),
            )
        return event, created, quality
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
        return existing, False, quality
    supporting_excerpt = quality.supporting_excerpt
    content = f"{document.title} {supporting_excerpt}"
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
        summary=f"公开来源出现与该公司相关的变化线索：{supporting_excerpt[:700]}",
        facts=[{"name": "公开页面标题", "value": document.title, "unit": None}],
        uncertainties=[
            "该内容由程序发现并完成主体核对，尚未经过人工事实复核。",
            (
                "事件日期取自支持正文，尚未独立核验；来源发布时间不是事件发生时间。"
                if quality.occurred_at is not None
                else "事件日期尚不完整或未知，不以来源发布时间代替，也不代表近期新变化。"
            ),
        ],
        occurred_at=quality.occurred_at,
        published_at=document.published_at,
        published_on=document.published_on,
        observed_at=document.observed_at,
        fingerprint_version="web-v1",
        event_fingerprint=fingerprint,
        publication_route="unconfirmed_lead",
        publication_policy_version=CONTENT_QUALITY_GATE_VERSION,
        publication_reasons=[
            "auto_publish_disabled",
            "bounded_public_web_discovery",
            "content_quality_gate_passed",
            "source_published_within_recent_window",
            "event_time_not_independently_verified",
            f"event_date:{quality.event_date_status}",
            "change_recognition:explicit-change-v2",
            "human_fact_review_not_completed",
            *(["new_evidence_content"] if new_document else ["reused_evidence_content"]),
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
            evidence_excerpt=supporting_excerpt,
            span_hash=_sha256(supporting_excerpt),
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
            display_url_checked_at=_evidence_checked_at(verification.get("checked_at")),
            display_final_url=document.canonical_url,
            display_license_status="public",
            display_allowed=True,
        )
    )
    session.flush()
    materialize_event_fact_ledger(session, event)
    return event, True, quality


def _candidate_can_trigger_source_recovery(candidate: dict[str, object]) -> bool:
    if candidate.get("source_tier") == "profile_or_listing":
        return False
    title = str(candidate.get("title") or "")
    snippet = str(candidate.get("snippet") or "")
    return _event_classification("", f"{title}。{snippet}") is not None


def _source_recovery_query(company: Company, candidate: dict[str, object]) -> str:
    title = " ".join(str(candidate.get("title") or "").replace('"', "").split())[:100]
    return f'"{company.legal_name}" "{title}" 原文 公告'


def _gap_follow_up_target(
    company: Company,
    coverage: dict[str, object],
    policy: WebResearchPolicy,
) -> dict[str, str] | None:
    candidates = {
        str(item.get("url")): item
        for item in coverage.get("candidates", [])
        if isinstance(item, dict) and item.get("url")
    }
    documents = [item for item in coverage.get("documents", []) if isinstance(item, dict)]
    covered_event_types = {
        str(quality.get("event_type"))
        for document in documents
        if isinstance((quality := document.get("quality_gate")), dict)
        and quality.get("status") == "eligible"
        and quality.get("event_type")
    }
    priorities = {event_type: index for index, event_type in enumerate(GAP_FOLLOW_UP_PRIORITY)}
    signals: list[tuple[int, int, float, str, dict[str, str]]] = []
    for document in documents:
        if document.get("status") not in {"failed", "created", "reused"}:
            continue
        quality = document.get("quality_gate")
        if isinstance(quality, dict) and quality.get("status") == "eligible":
            continue
        if isinstance(quality, dict) and any(
            reason in {"published_before_recent_window", "published_at_in_future"}
            for reason in quality.get("reasons", [])
        ):
            continue
        source_url = str(document.get("url") or "")
        candidate = candidates.get(source_url)
        if candidate is None or candidate.get("source_tier") == "profile_or_listing":
            continue
        title = str(candidate.get("title") or "")
        snippet = str(candidate.get("snippet") or "")
        event_type = _event_classification("", f"{title}。{snippet}")
        if event_type is None or event_type in covered_event_types:
            continue
        content = f"{title} {snippet}"
        if _materiality(event_type, content) < 60:
            continue
        target = {
            "event_type": event_type,
            "source_url": source_url,
            "reason": str(document.get("error_code") or "content_quality_gap"),
        }
        score, published_score, score_url = _result_score(
            company, SearchResult.from_dict(candidate), policy
        )
        signals.append(
            (
                priorities.get(event_type, len(priorities)),
                -score,
                -published_score,
                score_url,
                target,
            )
        )
    if not signals:
        return None
    signals.sort(key=lambda item: item[:4])
    return signals[0][4]


def _prepare_gap_follow_up(
    company: Company,
    coverage: dict[str, object],
    policy: WebResearchPolicy,
) -> dict[str, object]:
    follow_up = dict(coverage.get("gap_follow_up", {}))
    if follow_up.get("status") != "not_requested":
        return follow_up
    recovery = dict(coverage.get("source_recovery", {}))
    if int(recovery.get("attempts", 0)) >= 1:
        return {
            "status": "skipped",
            "attempts": 0,
            "reason": "source_recovery_consumed_follow_up_budget",
        }
    target = _gap_follow_up_target(company, coverage, policy)
    if target is None:
        return {
            "status": "skipped",
            "attempts": 0,
            "reason": "no_explicit_material_evidence_gap",
        }
    return {"status": "pending", "attempts": 0, **target}


def _gap_follow_up_query(company: Company, event_type: str) -> str:
    terms = GAP_FOLLOW_UP_TERMS[event_type]
    return _query_for(company, f"{terms} 原文")


def _process_source_recovery(
    session: Session,
    user: User,
    job: CompanyResearchJob,
    company: Company,
    policy: WebResearchPolicy,
    providers: dict[str, SearchProvider],
) -> WebResearchWorkerResult:
    coverage = dict(job.coverage)
    recovery = dict(coverage.get("source_recovery", {}))
    if recovery.get("status") != "pending" or int(recovery.get("attempts", 0)) >= 1:
        job.current_stage = "fetch"
        job.status = "partial"
        job.leased_until = None
        session.commit()
        return WebResearchWorkerResult(
            status="partial",
            job_id=job.id,
            company_id=job.company_id,
            stage="fetch",
            external_calls=job.external_calls,
        )

    candidates = [item for item in coverage.get("candidates", []) if isinstance(item, dict)]
    blocked_url = str(recovery.get("source_url") or "")
    blocked_candidate = next(
        (item for item in candidates if str(item.get("url") or "") == blocked_url),
        None,
    )
    recovery["attempts"] = 1
    if blocked_candidate is None:
        recovery.update({"status": "failed", "error_code": "source_candidate_missing"})
        coverage["source_recovery"] = recovery
        job.coverage = coverage
        job.current_stage = "fetch"
        job.status = "partial"
        job.leased_until = None
        session.commit()
        return WebResearchWorkerResult(
            status="partial",
            job_id=job.id,
            company_id=job.company_id,
            stage="fetch",
            external_calls=job.external_calls,
            error_code="source_candidate_missing",
        )

    primary = providers[policy.primary_provider]
    query = _source_recovery_query(company, blocked_candidate)
    recovery.update({"status": "running", "attempts": 1, "provider": primary.code})
    coverage["source_recovery"] = recovery
    job.coverage = coverage
    job.heartbeat_at = utc_now()
    session.commit()
    _restore_worker_context(session, user)
    job = _refresh_job(session, job.id)
    if _job_should_stop(session, job):
        return _cancel_job(session, job)
    cache_hit = False
    try:
        response, cache_hit = _provider_response(
            session,
            user,
            job,
            company,
            primary,
            query_kind="source_recovery",
            query=query,
            policy=policy,
        )
    except SearchProviderError as error:
        _restore_worker_context(session, user)
        job = _refresh_job(session, job.id)
        recovery = dict(job.coverage.get("source_recovery", recovery))
        recovery.update(
            {
                "status": "failed",
                "provider": primary.code,
                "error_code": error.code,
                "result_count": 0,
            }
        )
        coverage = dict(job.coverage)
        coverage["source_recovery"] = recovery
        job.coverage = coverage
        job.current_stage = "fetch"
        job.status = "partial"
        job.leased_until = None
        session.commit()
        return WebResearchWorkerResult(
            status="partial",
            job_id=job.id,
            company_id=job.company_id,
            stage="fetch",
            external_calls=job.external_calls,
            error_code=error.code,
        )

    _restore_worker_context(session, user)
    job = _refresh_job(session, job.id)
    if _job_should_stop(session, job):
        return _cancel_job(session, job)
    coverage = dict(job.coverage)
    candidates = [item for item in coverage.get("candidates", []) if isinstance(item, dict)]
    recovered: list[dict[str, object]] = []
    for result in response.results:
        candidate = _candidate_payload(
            company,
            result,
            provider_code=response.provider_code,
            query_kind="source_recovery",
            policy=policy,
        )
        if (
            candidate is None
            or candidate["url"] == blocked_url
            or (
                job.trigger_type == "watchlist"
                and candidate.get("coverage_category") not in monitoring.CATEGORIES
            )
        ):
            continue
        candidate["recovery_for_url"] = blocked_url
        recovered.append(candidate)
    recovered.sort(
        key=lambda item: _result_score(company, SearchResult.from_dict(item), policy),
        reverse=True,
    )

    processed_count = min(int(coverage.get("candidate_index", 0)), len(candidates))
    processed = candidates[:processed_count]
    remaining = candidates[processed_count:]
    seen_urls = {str(item.get("url") or "") for item in processed}
    recovered_tail: list[dict[str, object]] = []
    for item in recovered:
        url = str(item.get("url") or "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        recovered_tail.append(item)
        break
    regular_tail: list[dict[str, object]] = []
    for item in remaining:
        url = str(item.get("url") or "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        regular_tail.append(item)
    coverage["candidates"] = [
        *processed,
        # One dedicated slot keeps a valid recovery result from being discarded
        # when the original bounded discovery queue was already full.
        *recovered_tail,
        *regular_tail[: max(0, policy.max_candidate_urls - len(processed))],
    ]
    recovery = dict(coverage.get("source_recovery", recovery))
    recovery.update(
        {
            "status": "completed",
            "attempts": 1,
            "provider": primary.code,
            "provider_status": "cache_hit" if cache_hit else "completed",
            "result_count": len(recovered),
            "candidate_count_added": len(recovered_tail),
        }
    )
    if not recovered_tail:
        recovery["evidence_gap"] = "no_alternative_original_source_found"
        for item in coverage["candidates"]:
            if str(item.get("url") or "") == blocked_url:
                item["source_access_follow_up"] = SOURCE_ACCESS_MANUAL_IMPORT
                break
    coverage["source_recovery"] = recovery
    job.coverage = coverage
    job.current_stage = "fetch"
    job.status = "partial"
    job.leased_until = None
    job.heartbeat_at = utc_now()
    for request in _active_linked_requests(session, job.id):
        request.status = "partial"
        request.leased_until = None
        request.heartbeat_at = job.heartbeat_at
    session.commit()
    return WebResearchWorkerResult(
        status="partial",
        job_id=job.id,
        company_id=job.company_id,
        stage="fetch",
        external_calls=job.external_calls,
        cache_hits=int(cache_hit),
    )


def _process_gap_follow_up(
    session: Session,
    user: User,
    job: CompanyResearchJob,
    company: Company,
    policy: WebResearchPolicy,
    providers: dict[str, SearchProvider],
) -> WebResearchWorkerResult:
    coverage = dict(job.coverage)
    follow_up = dict(coverage.get("gap_follow_up", {}))
    event_type = str(follow_up.get("event_type") or "")
    if (
        follow_up.get("status") != "pending"
        or int(follow_up.get("attempts", 0)) >= 1
        or event_type not in GAP_FOLLOW_UP_TERMS
        or (job.trigger_type == "watchlist" and event_type not in monitoring.CATEGORIES)
    ):
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

    primary = providers[policy.primary_provider]
    fallback = providers[policy.fallback_provider]
    query = _gap_follow_up_query(company, event_type)
    follow_up.update({"status": "running", "attempts": 1, "provider": primary.code})
    coverage["gap_follow_up"] = follow_up
    job.coverage = coverage
    job.heartbeat_at = utc_now()
    session.commit()
    _restore_worker_context(session, user)
    job = _refresh_job(session, job.id)
    if _job_should_stop(session, job):
        return _cancel_job(session, job)
    responses: list[SearchResponse] = []
    cache_hits = 0
    primary_error_code: str | None = None
    primary_cached = False
    fallback_cache_used = False
    try:
        response, cached = _provider_response(
            session,
            user,
            job,
            company,
            primary,
            query_kind="gap_follow_up",
            query=query,
            policy=policy,
        )
        responses.append(response)
        cache_hits += int(cached)
        primary_cached = cached
    except SearchProviderError as error:
        primary_error_code = error.code

    _restore_worker_context(session, user)
    job = _refresh_job(session, job.id)
    if _job_should_stop(session, job):
        return _cancel_job(session, job)
    cached_fallback = _cached_response(
        session, company, fallback.code, query, utc_now(), max_age_days=policy.search_cache_ttl_days
    )
    if cached_fallback is not None:
        responses.append(cached_fallback)
        cache_hits += 1
        fallback_cache_used = True
        _record_search_cache_hit(session, job)
        _restore_worker_context(session, user)
        job = _refresh_job(session, job.id)

    recovered: list[dict[str, object]] = []
    original_url = str(follow_up.get("source_url") or "")
    for response in responses:
        for result in response.results:
            if _event_classification("", f"{result.title}。{result.snippet}") != event_type:
                continue
            candidate = _candidate_payload(
                company,
                result,
                provider_code=response.provider_code,
                query_kind=f"gap_follow_up:{event_type}",
                policy=policy,
            )
            if (
                candidate is None
                or candidate["url"] == original_url
                or (
                    job.trigger_type == "watchlist"
                    and candidate.get("coverage_category") not in monitoring.CATEGORIES
                )
            ):
                continue
            candidate["gap_follow_up_for"] = event_type
            recovered.append(candidate)
    recovered.sort(
        key=lambda item: _result_score(company, SearchResult.from_dict(item), policy),
        reverse=True,
    )

    coverage = dict(job.coverage)
    candidates = [item for item in coverage.get("candidates", []) if isinstance(item, dict)]
    processed_count = min(int(coverage.get("candidate_index", 0)), len(candidates))
    processed = candidates[:processed_count]
    remaining = candidates[processed_count:]
    seen_urls = {str(item.get("url") or "") for item in candidates}
    added = next(
        (candidate for candidate in recovered if str(candidate.get("url") or "") not in seen_urls),
        None,
    )
    follow_up = dict(coverage.get("gap_follow_up", follow_up))
    follow_up.update(
        {
            "status": "completed" if primary_error_code is None or added else "failed",
            "attempts": 1,
            "provider": primary.code,
            "provider_status": (
                "fallback_cache_after_primary_failure"
                if primary_error_code is not None and fallback_cache_used
                else (
                    "failed"
                    if primary_error_code is not None
                    else ("cache_hit" if primary_cached else "completed")
                )
            ),
            "fallback_cache_used": fallback_cache_used,
            "error_code": primary_error_code,
            "result_count": len(recovered),
            "candidate_count_added": int(added is not None),
        }
    )
    if added is None:
        follow_up["evidence_gap"] = "no_alternative_qualified_source_found"
    coverage["gap_follow_up"] = follow_up
    coverage["candidates"] = [*processed, *([added] if added else []), *remaining]
    job.coverage = coverage
    job.current_stage = "fetch" if added is not None else "finalize"
    job.status = "partial"
    job.leased_until = None
    job.heartbeat_at = utc_now()
    for request in _active_linked_requests(session, job.id):
        request.status = "partial"
        request.leased_until = None
        request.heartbeat_at = job.heartbeat_at
    session.commit()
    return WebResearchWorkerResult(
        status="partial",
        job_id=job.id,
        company_id=job.company_id,
        stage=job.current_stage,
        external_calls=job.external_calls,
        cache_hits=cache_hits,
        error_code=primary_error_code,
    )


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
        max_pdf_pages=policy.max_pdf_pages,
        max_pdf_text_chars=policy.max_pdf_text_chars,
        pdf_parse_timeout_seconds=policy.pdf_parse_timeout_seconds,
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
    robots_cache = normalized_robots_rule_cache(
        coverage.get("_robots_rule_cache"),
        policy.user_agent,
    )
    candidates = [item for item in coverage.get("candidates", []) if isinstance(item, dict)]
    index = int(coverage.get("candidate_index", 0))
    documents = [item for item in coverage.get("documents", []) if isinstance(item, dict)]
    stats = dict(coverage.get("stats", {}))
    previous_fetch_calls = int(stats.get("fetch_calls", 0))
    previous_downloaded_bytes = int(stats.get("downloaded_bytes", 0))
    previous_search_calls = int(stats.get("search_calls", 0))
    successful = sum(1 for item in documents if item.get("status") in {"created", "reused"})
    limits_reached = (
        previous_fetch_calls >= policy.max_fetch_requests_per_job
        or previous_downloaded_bytes >= policy.max_download_bytes_per_job
    )
    if index >= len(candidates) or successful >= policy.max_documents_per_job or limits_reached:
        if policy.incremental_research_enabled and job.trigger_type != "watchlist":
            group_code = (
                _readability_fallback_group(coverage, policy)
                if previous_search_calls < policy.max_search_calls_per_job
                else None
            )
            # Incremental discovery stays within the original two query groups.
            for kind in ("source_recovery", "gap_follow_up"):
                previous = dict(coverage.get(kind, {}))
                if previous.get("status") in {"not_requested", "pending"}:
                    coverage[kind] = {
                        **previous,
                        "status": "skipped",
                        "reason": "same_query_fallback_only",
                    }
            job.coverage = coverage
            job.current_stage = f"readability_fallback:{group_code}" if group_code else "finalize"
            job.status = "partial"
            job.leased_until = None
            session.commit()
            return WebResearchWorkerResult(
                status="partial",
                job_id=job.id,
                company_id=job.company_id,
                stage=job.current_stage,
                external_calls=job.external_calls,
            )
        recovery = dict(coverage.get("source_recovery", {}))
        can_recover = (
            index >= len(candidates)
            and successful < policy.max_documents_per_job
            and not limits_reached
            and previous_search_calls < policy.max_search_calls_per_job
            and recovery.get("status") == "pending"
        )
        job.current_stage = "source_recovery" if can_recover else "finalize"
        if recovery.get("status") == "pending" and not can_recover:
            recovery.update(
                {
                    "status": "skipped",
                    "evidence_gap": (
                        "search_call_limit_reached"
                        if previous_search_calls >= policy.max_search_calls_per_job
                        else "document_or_request_limit_reached"
                    ),
                }
            )
            coverage["source_recovery"] = recovery
        if not can_recover:
            follow_up = _prepare_gap_follow_up(company, coverage, policy)
            can_follow_up = (
                follow_up.get("status") == "pending"
                and index >= len(candidates)
                and successful < policy.max_documents_per_job
                and not limits_reached
                and previous_search_calls < policy.max_search_calls_per_job
            )
            if follow_up.get("status") == "pending" and not can_follow_up:
                follow_up.update(
                    {
                        "status": "skipped",
                        "reason": (
                            "search_call_limit_reached"
                            if previous_search_calls >= policy.max_search_calls_per_job
                            else "document_or_request_limit_reached"
                        ),
                    }
                )
            coverage["gap_follow_up"] = follow_up
            if can_follow_up:
                job.current_stage = "gap_follow_up"
        job.coverage = coverage
        job.status = "partial"
        job.leased_until = None
        session.commit()
        return WebResearchWorkerResult(
            status="partial",
            job_id=job.id,
            company_id=job.company_id,
            stage=job.current_stage,
            external_calls=job.external_calls,
        )
    candidate = candidates[index]
    url = str(candidate.get("url") or "")
    coverage["candidate_index"] = index + 1
    source = _source(session)
    cached_document = _document_cache(session, source.id, url, company, policy)
    documents_created = 0
    events_created = 0
    quality_gate_passed = 0
    internal_candidates = 0
    fetch_calls = 0
    downloaded_bytes = 0
    error_code: str | None = None
    http_status: int | None = None
    usage = None
    if cached_document is not None:
        extraction = cached_document.payload.get("content_extraction", {})
        is_official_pdf = bool(
            isinstance(extraction, dict) and extraction.get("document_format") == "pdf"
        )
        candidate["source_access_status"] = (
            SOURCE_ACCESS_OFFICIAL_DOCUMENT if is_official_pdf else SOURCE_ACCESS_AUTOMATIC_READ
        )
        candidate["source_access_reason"] = "valid_document_cache_reused"
        _, event_created, quality = _candidate_event(
            session,
            company,
            cached_document,
            source,
            policy,
            new_document=False,
            actor=user,
            allowed_categories=monitoring.CATEGORIES if job.trigger_type == "watchlist" else None,
        )
        events_created += int(event_created)
        quality_gate_passed += int(quality.eligible)
        internal_candidates += int(not quality.eligible)
        documents.append(
            {
                "url": url,
                "status": "reused",
                "document_id": str(cached_document.id),
                "event_created": event_created,
                "quality_gate": quality.to_dict(),
                "source_access_status": candidate["source_access_status"],
                "coverage_category": quality.event_type,
                "attempted_at": utc_now().isoformat(),
                "checked_at": cached_document.payload.get("_source_verification", {}).get(
                    "checked_at"
                ),
                "cache_reused": True,
            }
        )
        stats = dict(coverage.get("stats", {}))
        stats["document_cache_hits"] = int(stats.get("document_cache_hits", 0)) + 1
        coverage["stats"] = stats
        for request in _active_linked_requests(session, job.id):
            request.cache_hits += 1
    else:
        is_pdf_url = urlsplit(url).path.casefold().endswith(".pdf")
        source_tier = str(candidate.get("source_tier") or "")
        if is_pdf_url and source_tier not in OFFICIAL_PDF_SOURCE_TIERS:
            error_code = "pdf_source_not_authoritative"
        else:
            host = (urlsplit(url).hostname or "").lower().rstrip(".")
            root_domain = host.removeprefix("www.")
            usage = _reserve_job_call(
                session,
                user,
                job,
                company,
                policy,
                provider="public_web_fetch",
                operation="evidence_fetch",
                token=f"fetch:{_sha256(url)}",
                calls=policy.max_fetch_requests_per_job - previous_fetch_calls,
                unit_price=Decimal("0"),
                metrics={
                    "download_bytes_limit": policy.max_download_bytes_per_job
                    - previous_downloaded_bytes
                },
            )
            coverage["budget_baseline"] = job.coverage["budget_baseline"]
            fetcher = fetcher_factory(
                _fetch_policy(
                    policy,
                    remaining_requests=policy.max_fetch_requests_per_job - previous_fetch_calls,
                    remaining_bytes=policy.max_download_bytes_per_job - previous_downloaded_bytes,
                )
            )
            fetcher.bind_job_robots_cache(robots_cache)
            if job.trigger_type == "watchlist":
                fetcher.bind_request_guard(monitoring.request_guard(session, user, job))
            elif policy.incremental_research_enabled:

                def within_deadline():
                    if _incremental_time_exhausted(job, policy):
                        raise SourceFetchError(
                            "execution_time_limit_reached", "research deadline reached"
                        )
                    return True

                fetcher.bind_request_guard(within_deadline)
            try:
                result = fetcher.check(
                    source_type="single_page",
                    root_domain=root_domain,
                    start_url=url,
                    retention_policy="minimal_excerpt",
                    conditional_state={},
                    **(
                        {"excerpt_selector": BusinessExcerptSelector(company)}
                        if policy.incremental_research_enabled
                        else {}
                    ),
                )
                fetch_calls = result.request_count
                downloaded_bytes = result.downloaded_bytes
                if not result.documents:
                    error_code = "no_fetchable_document"
                else:
                    discovered = result.documents[0]
                    is_pdf_document = discovered.metadata.get("document_format") == "pdf"
                    if is_pdf_document and source_tier not in OFFICIAL_PDF_SOURCE_TIERS:
                        error_code = "pdf_source_not_authoritative"
                    else:
                        excerpt = discovered.excerpt or ""
                        if not _document_matches_company(
                            company,
                            discovered.title,
                            excerpt,
                            discovered.canonical_url,
                        ):
                            error_code = "fetched_page_identity_mismatch"
                        else:
                            candidate["source_access_status"] = (
                                SOURCE_ACCESS_OFFICIAL_DOCUMENT
                                if is_pdf_document
                                else SOURCE_ACCESS_AUTOMATIC_READ
                            )
                            candidate["source_access_reason"] = "bounded_fetch_completed"
                            quality = _content_quality_decision(
                                company,
                                title=discovered.title,
                                excerpt=excerpt,
                                published_at=discovered.published_at,
                                observed_at=utc_now(),
                                policy=policy,
                            )
                            document, document_created = _raw_document(
                                session,
                                source,
                                company,
                                candidate,
                                discovered,
                                job,
                                quality,
                            )
                            documents_created += int(document_created)
                            _, event_created, quality = _candidate_event(
                                session,
                                company,
                                document,
                                source,
                                policy,
                                new_document=document_created,
                                actor=user,
                                allowed_categories=monitoring.CATEGORIES
                                if job.trigger_type == "watchlist"
                                else None,
                            )
                            events_created += int(event_created)
                            quality_gate_passed += int(quality.eligible)
                            internal_candidates += int(not quality.eligible)
                            documents.append(
                                {
                                    "url": url,
                                    "status": "created" if document_created else "reused",
                                    "document_id": str(document.id),
                                    "event_created": event_created,
                                    "quality_gate": quality.to_dict(),
                                    "source_access_status": candidate["source_access_status"],
                                    "coverage_category": quality.event_type,
                                    "attempted_at": utc_now().isoformat(),
                                    "checked_at": (
                                        document.payload["_source_verification"]["checked_at"]
                                        if document_created
                                        else utc_now().isoformat()
                                    ),
                                    "cache_reused": False,
                                }
                            )
            except SourceFetchError as error:
                fetch_calls = fetcher.request_count
                downloaded_bytes = fetcher.downloaded_bytes
                error_code = error.code
                http_status = error.http_status
            finally:
                fetcher.close()
        job.external_calls += fetch_calls
        if usage is not None:
            budget.settle(
                session,
                usage,
                calls=fetch_calls,
                metrics={
                    "status": "completed" if error_code is None else "failed",
                    "error_code": error_code,
                    "downloaded_bytes": downloaded_bytes,
                },
            )
        if error_code is not None:
            if error_code == "robots_disallowed":
                candidate["source_access_status"] = SOURCE_ACCESS_ROBOTS_BLOCKED
                candidate["source_access_reason"] = error_code
                recovery = dict(coverage.get("source_recovery", {}))
                if recovery.get(
                    "status"
                ) == "not_requested" and _candidate_can_trigger_source_recovery(candidate):
                    recovery = {
                        "status": "pending",
                        "attempts": 0,
                        "source_url": url,
                        "source_tier": candidate.get("source_tier"),
                    }
                    coverage["source_recovery"] = recovery
                else:
                    candidate["source_access_follow_up"] = SOURCE_ACCESS_MANUAL_IMPORT
            else:
                candidate["source_access_status"] = SOURCE_ACCESS_MANUAL_IMPORT
                candidate["source_access_reason"] = error_code
            documents.append(
                {
                    "url": url,
                    "status": "failed",
                    "error_code": error_code,
                    "source_access_status": candidate["source_access_status"],
                    "http_status": http_status,
                    "coverage_category": candidate.get("coverage_category"),
                    "attempted_at": utc_now().isoformat(),
                    "checked_at": None,
                    "cache_reused": False,
                }
            )
    stats = dict(coverage.get("stats", {}))
    stats["fetch_calls"] = int(stats.get("fetch_calls", 0)) + fetch_calls
    stats["downloaded_bytes"] = int(stats.get("downloaded_bytes", 0)) + downloaded_bytes
    stats["events_created"] = int(stats.get("events_created", 0)) + events_created
    stats["quality_gate_passed"] = int(stats.get("quality_gate_passed", 0)) + quality_gate_passed
    stats["internal_candidates"] = int(stats.get("internal_candidates", 0)) + internal_candidates
    coverage["documents"] = documents
    coverage["candidates"] = candidates
    coverage["stats"] = stats
    coverage["_robots_rule_cache"] = normalized_robots_rule_cache(
        robots_cache,
        policy.user_agent,
    )
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
        stage=job.current_stage,
        external_calls=job.external_calls,
        cache_hits=int(cached_document is not None),
        documents_created=documents_created,
        events_created=events_created,
        error_code=error_code,
    )


def _finalize(session: Session, job: CompanyResearchJob) -> WebResearchWorkerResult:
    coverage = dict(job.coverage)
    coverage["cost_summary"] = _job_cost_summary(session, job)
    coverage.pop("_robots_rule_cache", None)
    follow_up = dict(coverage.get("gap_follow_up", {}))
    if follow_up.get("status") in {"not_requested", "pending"}:
        follow_up.update(
            {
                "status": "skipped",
                "reason": "job_finalized_before_follow_up",
            }
        )
        coverage["gap_follow_up"] = follow_up
    documents = [item for item in coverage.get("documents", []) if isinstance(item, dict)]
    coverage["modules"] = {item.category: item.status for item in category_coverage(coverage)}
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
    _record_watch_outcome(session, job)
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
    job.coverage = {
        **job.coverage,
        "cost_summary": _job_cost_summary(session, job),
    }
    if job.current_stage in {"gap_follow_up", "source_recovery"}:
        kind = job.current_stage
        attempts = session.scalars(
            select(UsageLedger).where(
                UsageLedger.task_key == f"web-research:{job.id}",
                UsageLedger.metrics["query_kind"].as_string() == kind,
            )
        ).all()
        if not any(item.usage_state in {"in_flight", "uncertain", "settled"} for item in attempts):
            follow_up = dict(job.coverage.get(kind, {}))
            follow_up.update(status="pending", attempts=0, deferred_at=utc_now().isoformat())
            job.coverage = {**job.coverage, kind: follow_up}
    job.last_error_code = "web_research_budget_deferred"
    for request in _active_linked_requests(session, job.id):
        request.status = "budget_deferred"
        request.leased_until = None
        request.heartbeat_at = job.heartbeat_at
        request.last_error_code = job.last_error_code
    _record_watch_outcome(session, job)
    session.commit()
    return WebResearchWorkerResult(
        status="budget_deferred",
        job_id=job.id,
        company_id=job.company_id,
        stage=job.current_stage,
        external_calls=job.external_calls,
        error_code=str(error),
        cost_summary=job.coverage.get("cost_summary"),
    )


def run_web_research_worker_once(
    session, user, providers, policy, *, fetcher_factory=None, watchlist_gate=None
):
    session.info["watchlist_gate"] = watchlist_gate or (lambda: policy.watchlist.enabled)
    session.info["watchlist_user_id"] = user.id
    session.info["watchlist_policy"] = policy.watchlist
    try:
        result = _run_web_research_worker_once(
            session, user, providers, policy, fetcher_factory=fetcher_factory
        )
        if result.job_id:
            _restore_worker_context(session, user)
            job = session.get(CompanyResearchJob, result.job_id)
            if job is not None:
                monitoring.record_outcome(session, user, job, policy.watchlist)
                return replace(result, cost_summary=_job_cost_summary(session, job))
        return result
    finally:
        session.info.pop("watchlist_gate", None)
        session.info.pop("watchlist_user_id", None)
        session.info.pop("watchlist_policy", None)


def _run_web_research_worker_once(
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
    # Leasing commits even when no research job exists, clearing transaction-local RLS context.
    _restore_worker_context(session, user)
    if job is None:
        from backend.app.identity_research import run_identity_step

        identity_result = run_identity_step(session, user, providers, policy, fetcher_factory)
        if identity_result is not None:
            return identity_result
        return WebResearchWorkerResult(status="idle")
    if job.trigger_type == "watchlist":
        policy = replace(
            policy,
            search_cache_ttl_days=min(policy.search_cache_ttl_days, policy.watchlist.interval_days),
            document_cache_ttl_days=min(
                policy.document_cache_ttl_days, policy.watchlist.interval_days
            ),
        )
    company = session.get(Company, job.company_id)
    if not _public_company(company):
        job.status = "failed"
        job.last_error_code = "company_not_verified_for_shared_research"
        _record_watch_outcome(session, job)
        session.commit()
        return WebResearchWorkerResult(
            status="failed",
            job_id=job.id,
            company_id=job.company_id,
            stage=job.current_stage,
            error_code=job.last_error_code,
        )
    if policy.incremental_research_enabled:
        company = load_subject(session, company)
        coverage = dict(job.coverage)
        coverage["query_strategy_version"] = QUERY_STRATEGY_VERSION
        coverage["business_subject"] = {
            "legal_name": company.legal_name,
            "aliases": list(company.aliases),
        }
        job.coverage = coverage
    if budget.recover(session, task_key=f"web-research:{job.id}"):
        return _defer_budget(
            session, job, WebResearchBudgetDeferred("uncertain spend requires reconciliation")
        )
    coverage = dict(job.coverage or {})
    incremental_bounded = policy.incremental_research_enabled and job.trigger_type != "watchlist"
    if incremental_bounded:
        coverage["follow_up_strategy_version"] = "same-query-readable-fallback-v1"
        job.coverage = coverage
        if job.current_stage in {"source_recovery", "gap_follow_up"}:
            job.current_stage = "fetch"
    started_at_value = coverage.get("started_at" if incremental_bounded else "step_started_at")
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
        if job.current_stage.startswith("readability_fallback:"):
            return _process_readability_fallback(
                session,
                user,
                job,
                company,
                policy,
                providers,
                job.current_stage.split(":", 1)[1],
            )
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
        if job.current_stage == "source_recovery":
            return _process_source_recovery(
                session,
                user,
                job,
                company,
                policy,
                providers,
            )
        if job.current_stage == "gap_follow_up":
            return _process_gap_follow_up(
                session,
                user,
                job,
                company,
                policy,
                providers,
            )
        if job.current_stage == "finalize":
            return _finalize(session, job)
        raise RuntimeError("unknown web research job stage")
    except WebResearchBudgetDeferred as error:
        job = _refresh_job(session, job.id)
        if _job_should_stop(session, job):
            return _cancel_job(session, job)
        return _defer_budget(session, job, error)
    except Exception:
        if job.trigger_type != "watchlist":
            raise
        job_id = job.id
        session.rollback()
        _restore_worker_context(session, user)
        job = _refresh_job(session, job_id)
        if budget.recover(session, task_key=f"web-research:{job.id}"):
            return _defer_budget(
                session, job, WebResearchBudgetDeferred("uncertain spend requires reconciliation")
            )
        job.status, job.leased_until = "failed", None
        job.heartbeat_at = utc_now()
        job.last_error_code = "watchlist_step_failed"
        _record_watch_outcome(session, job)
        session.commit()
        return WebResearchWorkerResult(
            status="failed",
            job_id=job.id,
            company_id=job.company_id,
            error_code="watchlist_step_failed",
        )


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
                or_(
                    PersonalCompanyRequest.status.in_(
                        (
                            "pending",
                            "research_queued",
                            "identity_queued",
                            "identity_checking",
                            "cancel_requested",
                        )
                    ),
                    (
                        (PersonalCompanyRequest.status == "in_review")
                        & (PersonalCompanyRequest.last_error_code == "curator_identity_confirmed")
                        & (PersonalCompanyRequest.company_id.is_not(None))
                        & (PersonalCompanyRequest.cancel_requested_at.is_(None))
                    )
                    if policy.incremental_research_enabled
                    else False,
                ),
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
        "incremental_research_enabled": policy.incremental_research_enabled,
        "query_strategy_version": QUERY_STRATEGY_VERSION
        if policy.incremental_research_enabled
        else "legal-name-only",
        "pending_requests": pending_requests,
        "queued_jobs": queued_jobs,
        "primary_provider": policy.primary_provider,
        "fallback_provider": policy.fallback_provider,
        "max_search_calls_per_job": policy.max_search_calls_per_job,
        "max_documents_per_job": policy.max_documents_per_job,
        "identity_budget": {
            "max_search_calls": min(6, policy.identity_max_search_calls),
            "max_fetch_requests": policy.identity_max_fetch_requests,
            "max_download_bytes": policy.identity_max_download_bytes,
        },
        "new_company_max_search_calls": min(6, policy.identity_max_search_calls)
        + policy.max_search_calls_per_job,
        "external_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": None,
        "cost_status": "unknown",
        "cost_policy": {
            "version": policy.cost.version,
            "currency": "CNY",
            "baidu_price_per_call": (
                str(policy.cost.baidu_price_per_call)
                if policy.cost.baidu_price_per_call is not None
                else None
            ),
            "bocha_price_per_call": (
                str(policy.cost.bocha_price_per_call)
                if policy.cost.bocha_price_per_call is not None
                else None
            ),
            "unknown_price_upper_bound": (
                str(policy.cost.unknown_price_upper_bound)
                if policy.cost.unknown_price_upper_bound is not None
                else None
            ),
            **{
                name: str(getattr(policy.cost, name))
                for name in (
                    "task_limit",
                    "company_daily_limit",
                    "company_weekly_limit",
                    "system_weekly_limit",
                    "system_monthly_limit",
                )
            },
        },
    }
