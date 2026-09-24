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
INTENTS = {
    "financing_cap_table": {
        "company_financing": "融资",
        "fund_commitment": "基金 认缴",
        "outbound_investment": "对外投资 股权",
        "registered_capital": "注册资本 变更",
    },
    "exit_liquidity": {
        "ipo_application": "IPO 递表 招股书",
        "acquisition_target": "被收购 控制权",
        "ipo_listed": "挂牌上市",
        "ipo_guidance": "上市 辅导 备案",
    },
}


def plan_intent(category, histories):
    options = INTENTS.get(category, {category: TOPICS[category]})
    attempts = {}
    for sequence, coverage in enumerate(histories):
        for state in coverage.get("search_groups", {}).values():
            if state.get("topic_category") == category and state.get("attempted_at"):
                intent = state.get("topic_intent") or next(iter(options))
                attempts[intent] = sequence
    selected = min(options, key=lambda key: attempts.get(key, -1))
    return {"topic_intent": selected, "topic": options[selected], "coverage_scope": "intent_only"}


def topic_terms(category, policy):
    if policy.matter_processing_enabled:
        return {
            "financing_cap_table": "融资",
            "exit_liquidity": "IPO 递表 招股书",
        }.get(category, TOPICS[category])
    return TOPICS[category]


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


def document_categories(document):
    return set(document.get("matter_categories") or [document.get("coverage_category")])


def checked_document(document):
    return document.get("status") in {"created", "reused"} and (
        eligible_document(document)
        or document.get("disposition")
        in {"no_supported_matter", "duplicate", "historical", "matters_retained"}
    )


def eligible_document(document, category=None):
    return (
        document.get("status") in {"created", "reused"}
        and document.get("quality_gate", {}).get("status") == "eligible"
        and (category is None or category in document_categories(document))
    )


def order_candidates(candidates, coverage):
    """调用者先按证据等级/时间排序，再按类别轮转；已处理前缀不传入此函数。"""
    remaining, seen_titles, identity_slots = [], set(), 0
    for original in candidates:
        item = dict(original)
        signature = re.sub(r"\s|[，,。！!：:]", "", str(item.get("title", "")))
        duplicate = len(signature) >= 12 and signature in seen_titles
        if duplicate:
            item["scheduling_reason"] = "duplicate_title_deferred"
        if item.get("identity_pending"):
            identity_slots += 1
            if identity_slots > 1:
                item["scheduling_reason"] = "identity_verification_slot_exhausted"
        seen_titles.add(signature)
        remaining.append(item)
    remaining.sort(key=lambda c: bool(c.get("scheduling_reason")))
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
                    if item.get("coverage_category") == category
                    and not directory_page(item["url"])
                    and not item.get("scheduling_reason")
                ),
                None,
            )
            if candidate is not None:
                ordered.append(candidate)
                remaining.remove(candidate)
                changed = True
        if not changed:
            break
    return [
        *ordered,
        *sorted(
            remaining,
            key=lambda item: (bool(item.get("scheduling_reason")), directory_page(item["url"])),
        ),
    ]


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
            if group.get("coverage_scope") == "intent_only":
                # 一个子问题完成不能刷新整个融资/退出大类的完成时间。
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
            relevant_docs = [
                d
                for d in docs
                if category in document_categories(d) or category in d.get("searched_topics", [])
            ]
            if any(d.get("status") == "failed" for d in relevant_docs):
                continue
            eligible = {d["url"]: d for d in relevant_docs if checked_document(d)}
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
