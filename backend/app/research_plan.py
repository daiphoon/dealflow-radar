"""可回放的主题计划和正文调度；不调用 Provider，不读取盲测答案。"""

import re
from datetime import datetime
from urllib.parse import urlsplit

VERSION = "topic-research-v1"
TOPICS = {
    "financing_cap_table": "融资 股权",
    "exit_liquidity": "IPO 上市 并购",
    "legal_compliance": "诉讼 执行 处罚",
    "financial_operation": "经营 营收 订单",
    "contract_commercial": "合同 中标 合作",
    "product_technology": "产品 认证 技术",
    "governance_people": "高管 董事 离职",
    "capacity_assets": "产能 投产 工厂",
}
GROUPS = ("business_capital", "technology_risk_exit")


def planned(coverage):
    return coverage.get("query_strategy_version") == VERSION


def plan_topics(histories):
    # 最近尝试过的主题后置，失败仍保留，不让一种失败永远挤占其余类别。
    last_attempt = {}
    for sequence, coverage in enumerate(histories):
        for state in coverage.get("search_groups", {}).values():
            category = state.get("topic_category")
            if category in TOPICS and state.get("attempted_at"):
                last_attempt[category] = sequence
    order = sorted(TOPICS, key=lambda category: last_attempt.get(category, -1))
    return dict(zip(GROUPS, order[: len(GROUPS)]))


def directory_page(url):
    path = urlsplit(url).path.rstrip("/")
    leaf = path.rsplit("/", 1)[-1].split(".", 1)[0].casefold()
    return not path or leaf in {
        "index",
        "home",
        "about",
        "about-us",
        "contact",
        "contact-us",
        "faq",
        "service",
        "services",
        "support",
        "product",
        "products",
        "news",
        "search",
    }


def topic_categories(coverage):
    return [
        state["topic_category"]
        for state in coverage.get("search_groups", {}).values()
        if state.get("topic_category") in TOPICS
    ]


def eligible_document(document, category=None):
    return (
        document.get("status") in {"created", "reused"}
        and document.get("quality_gate", {}).get("status") == "eligible"
        and (category is None or document.get("coverage_category") == category)
    )


def order_candidates(candidates, coverage):
    """调用者先按证据等级/时间排序，再按类别轮转；已处理前缀不传入此函数。"""
    remaining = list(candidates)
    ordered = []
    categories = topic_categories(coverage)
    documents = coverage.get("documents", [])
    categories.sort(key=lambda c: sum(eligible_document(d, c) for d in documents))
    while remaining:
        changed = False
        for category in categories:
            candidate = next(
                (
                    item
                    for item in remaining
                    if item.get("coverage_category") == category and not directory_page(item["url"])
                ),
                None,
            )
            if candidate is not None:
                ordered.append(candidate)
                remaining.remove(candidate)
                changed = True
        if not changed:
            break
    return [*ordered, *sorted(remaining, key=lambda item: directory_page(item["url"]))]


def document_limit(coverage, policy):
    # 新策略的存储量由有限候选队列和 HTTP/字节限制约束，底稿不提前结束主题研究。
    return policy.max_candidate_urls if planned(coverage) else policy.max_documents_per_job


def recovery_terms(category, candidates):
    text = " ".join(
        str(c.get("title", "")) + " " + str(c.get("snippet", ""))
        for c in candidates
        if c.get("coverage_category") == category
    )
    if category == "financing_cap_table":
        rounds = set(re.findall(r"(?:Pre[- ]?[A-F]|[A-F][+＋]?|天使|种子|战略)轮", text, re.I))
        if len(rounds) == 1:
            return f"{next(iter(rounds))} 融资 公告"
    if category == "exit_liquidity" and "辅导" in text:
        return "IPO 辅导 备案 公告"
    return f"{TOPICS[category]} 公告"


def successful_topic_checks(histories):
    """只使用完整主题检查的原时间；部分读取、失败和缓存命中不重置保鲜期。"""
    checks = {}
    for coverage in histories:
        if not planned(coverage):
            continue
        docs = coverage.get("documents", [])
        for group in coverage.get("search_groups", {}).values():
            category = group.get("topic_category")
            if category not in TOPICS or group.get("status") != "completed":
                continue
            if not any(
                s.get("status") in {"completed", "cache_hit", "cache_fused"}
                for s in group.get("providers", {}).values()
            ):
                continue
            candidates = [
                c
                for c in coverage.get("candidates", [])
                if c.get("coverage_category") == category and not directory_page(c["url"])
            ]
            if any(
                c.get("coverage_category") == category
                for c in coverage.get("deferred_candidates", [])
            ):
                continue
            relevant_docs = [d for d in docs if d.get("coverage_category") == category]
            if any(d.get("status") == "failed" for d in relevant_docs):
                continue
            eligible = {d["url"]: d for d in relevant_docs if eligible_document(d, category)}
            if any(c["url"] not in eligible for c in candidates):
                continue
            if not eligible and group.get("subject_results") != 0:
                continue
            values = [group.get("checked_at"), *[d.get("checked_at") for d in eligible.values()]]
            dates = []
            for value in values:
                try:
                    parsed = datetime.fromisoformat(str(value))
                    if parsed.tzinfo:
                        dates.append(parsed)
                except ValueError:
                    pass
            if len(dates) == len(values):
                checked = min(dates)
                checks[category] = max(checks.get(category, checked), checked)
    return checks


def coverage_routes(assignments, primary, fallback):
    return {
        **{
            category: {
                "search_group": next((g for g, c in assignments.items() if c == category), None),
                "providers": [primary, fallback],
            }
            for category in TOPICS
        },
        "information_quality": None,
    }
