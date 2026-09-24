from __future__ import annotations

from datetime import datetime

from backend.app import research_plan as planning
from backend.app.schemas import CategoryCoverageOut

COVERAGE_VERSION = "category-source-checks-v1"

# Reuse the existing two bounded searches; a category route is not an extra call.
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
    ("business_capital", "财务 融资 股权 合同 中标 订单"),
    ("technology_risk_exit", "公告 变更 上市 备案 诉讼 处罚 投产 产品"),
)
SHORT_SEARCH_TOPICS = {"business_capital": "融资", "technology_risk_exit": "产品"}
SHORT_SEARCH_MODULES = {
    "business_capital": ("financing_cap_table",),
    "technology_risk_exit": ("product_technology",),
}
SEARCH_GROUP_MODULES = {
    "business_capital": ("financial_operation", "financing_cap_table", "contract_commercial"),
    "technology_risk_exit": (
        "product_technology",
        "governance_people",
        "legal_compliance",
        "capacity_assets",
        "exit_liquidity",
    ),
}
BLOCKED_ERRORS = {
    "robots_disallowed",
    "blocked_scheme",
    "blocked_host",
    "blocked_address",
    "pdf_source_not_authoritative",
    "request_limit_exceeded",
}
SUCCESSFUL_SEARCHES = {"completed", "cache_hit", "cache_fused"}


def _blocked_document(document: dict[str, object]) -> bool:
    return document.get("error_code") in BLOCKED_ERRORS or document.get("http_status") in {
        401,
        403,
        429,
    }


def source_routes(
    primary: str, fallback: str, *, short_topics: bool = False
) -> dict[str, dict[str, object] | None]:
    return {
        **{
            category: {"search_group": group, "providers": [primary, fallback]}
            for group, categories in (
                SHORT_SEARCH_MODULES if short_topics else SEARCH_GROUP_MODULES
            ).items()
            for category in categories
        },
        "information_quality": None,
    }


def _latest(values: list[object]) -> datetime | None:
    dates = []
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            continue
        # Unknown precision/timezone must not become a new check time.
        if parsed.tzinfo is not None:
            dates.append(parsed)
    return max(dates) if dates else None


def category_coverage(coverage: dict[str, object]) -> list[CategoryCoverageOut]:
    """Project only recorded checks; never inspect events or expose private source content."""
    if coverage.get("category_coverage_version") != COVERAGE_VERSION:
        return [
            CategoryCoverageOut(
                category=category,
                status="unknown",
                gaps=["历史任务未完整记录类别检查，不能根据已有事件反推来源覆盖。"],
            )
            for category in RESEARCH_MODULES
        ]
    routes = coverage.get("source_routes", {})
    groups = coverage.get("search_groups", {})
    documents = [item for item in coverage.get("documents", []) if isinstance(item, dict)]
    candidates = [item for item in coverage.get("candidates", []) if isinstance(item, dict)]
    result = []
    for category in RESEARCH_MODULES:
        route = routes.get(category) if isinstance(routes, dict) else None
        group_code = route.get("search_group") if isinstance(route, dict) else None
        if planning.planned(coverage) and category in planning.TOPICS and group_code is None:
            result.append(
                CategoryCoverageOut(
                    category=category,
                    status="not_checked",
                    gaps=["本轮预算分配给其他主题，本类尚未检查；后续任务按历史缺口轮换。"],
                )
            )
            continue
        if group_code not in SEARCH_GROUP_MODULES or not route.get("providers"):
            result.append(
                CategoryCoverageOut(
                    category=category,
                    status="not_configured",
                    gaps=[
                        "信息质量由各类检查记录汇总，不配置独立外部检索。"
                        if category == "information_quality"
                        else "本轮未配置本类来源路由。"
                    ],
                )
            )
            continue
        group = groups.get(group_code, {}) if isinstance(groups, dict) else {}
        providers = group.get("providers", {})
        states = [item for item in providers.values() if isinstance(item, dict)]
        succeeded = any(item.get("status") in SUCCESSFUL_SEARCHES for item in states)
        search_failed = sum(item.get("status") == "failed" for item in states)
        docs = [item for item in documents if category in planning.document_categories(item)]
        readable = [
            item
            for item in docs
            if (
                planning.eligible_document(item)
                if planning.planned(coverage)
                else item.get("status") in {"created", "reused"}
            )
        ]
        blocked = sum(_blocked_document(item) for item in docs)
        failed = sum(
            item.get("status") == "failed" and not _blocked_document(item) for item in docs
        )
        has_candidates = any(category in planning.document_categories(item) for item in candidates)
        gaps = []
        if readable:
            status = "evidence_obtained"
            gaps.append("取得相关正文不等于事实已核实，也不代表本类资料已查全。")
        elif blocked:
            status = "blocked"
        elif failed or search_failed:
            status = "failed"
        elif has_candidates or group.get("subject_results", 0) > 0:
            status = "candidates_only"
            gaps.append("组合检索存在候选资料，本类尚未取得可归类的相关正文。")
        elif succeeded and group.get("subject_results") == 0:
            status = "no_records"
            gaps.append("本轮组合检索未检出合格线索，不代表该类信息不存在或公司没有风险。")
        elif group.get("status") == "pending":
            status = "not_checked"
            gaps.append("已配置来源路由，但本轮尚未完成检查。")
        else:
            status = "unknown"
            gaps.append("检查记录不足，无法判断本类覆盖结果。")
        if blocked:
            gaps.append(f"{blocked} 份候选正文因访问规则、安全检查或读取上限受阻。")
        if failed or search_failed:
            gaps.append("部分搜索或正文读取失败，本轮覆盖不完整。")
        if any(
            isinstance(item.get("quality_gate"), dict)
            and item["quality_gate"].get("status") != "eligible"
            for item in readable
        ):
            gaps.append("部分正文未达到时间或变化证据要求，不能作为已确认事实。")
        known_urls = {item.get("url") for item in documents}
        if any(
            category in planning.document_categories(item) and item.get("url") not in known_urls
            for item in candidates
        ):
            gaps.append("仍有本类候选正文未读取，任务结束不等于检查完成。")
        result.append(
            CategoryCoverageOut(
                category=category,
                status=status,
                route=group_code,
                topic=group.get("topic") if planning.planned(coverage) else None,
                last_attempt_at=_latest(
                    [group.get("attempted_at"), *[d.get("attempted_at") for d in docs]]
                ),
                search_checked_at=_latest([group.get("checked_at")]) if succeeded else None,
                evidence_checked_at=_latest([d.get("checked_at") for d in readable]),
                cache_reused=bool(group.get("cache_reused"))
                or any(d.get("cache_reused") for d in docs),
                evidence_count=len(readable),
                blocked_count=blocked,
                failed_count=failed + search_failed,
                gaps=gaps,
            )
        )
    return result
