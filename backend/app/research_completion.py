"""由当次执行记录投影完成状态，不能从已有事件反推已研究。"""

from backend.app import research_plan as planning
from backend.app.research_coverage import SEARCH_GROUP_MODULES, SUCCESSFUL_SEARCHES, _latest
from backend.app.research_network import public_failure_class

VERSION = "research-completion-v1"
STATUS_LABELS = {
    "completed": "已完成本轮有限检查",
    "complete": "已完成本轮有限检查",
    "partial": "部分完成",
    "failed": "研究失败",
    "not_run": "尚未开始 / 未检查",
    "budget_deferred": "预算暂缓 / 未完成",
}
CATEGORY_LABELS = {
    "financing_cap_table": "融资与股权",
    "exit_liquidity": "退出与流动性",
    "legal_compliance": "司法与合规",
    "financial_operation": "财务与经营",
    "contract_commercial": "合同与商业进展",
    "product_technology": "产品与技术",
    "governance_people": "治理与人员",
    "capacity_assets": "产能与资产",
}
FAILURE_LABELS = {
    "source_unreachable": "来源不可达",
    "network_environment_blocked": "环境预检失败",
    "robots_denied": "网站自动读取规则受阻",
    "dynamic_content_unavailable": "正文不可获取",
    "budget_deferred": "预算不足，未完成",
    "not_checked": "未检查",
}


def completion_projection(coverage):
    groups = coverage.get("search_groups", {})
    documents = coverage.get("documents", [])
    candidates = coverage.get("candidates", [])
    deferred = coverage.get("deferred_candidates", [])
    routes = coverage.get("source_routes", {})
    network_failed = coverage.get("network_preflight", {}).get("status") not in {
        None,
        "research_network_ready",
    }
    rows = []
    for category in planning.TOPICS:
        entries = [
            (code, state)
            for code, state in groups.items()
            if state.get("topic_category") == category
            or (
                not planning.planned(coverage)
                and (
                    routes.get(category, {}).get("search_group") == code
                    if isinstance(routes.get(category), dict)
                    else not routes and category in SEARCH_GROUP_MODULES.get(code, ())
                )
            )
        ]
        group = entries[0][1] if entries else {}
        group_code = entries[0][0] if entries else None
        planned = bool(entries)
        relevant_candidates = [
            c
            for c in candidates
            if c.get("coverage_category") == category
            or group_code in c.get("query_kinds", [c.get("query_kind")])
        ]
        urls = {
            c.get("url")
            for c in relevant_candidates
            if not planning.directory_page(c.get("url", ""))
        }
        docs = [
            d
            for d in documents
            if category in planning.document_categories(d)
            or category in d.get("searched_topics", [])
            or d.get("url") in urls
        ]
        readable = [d for d in docs if planning.checked_document(d)]
        failed = [d for d in docs if d.get("status") == "failed"]
        success = any(
            s.get("status") in SUCCESSFUL_SEARCHES for s in group.get("providers", {}).values()
        )
        unfinished_routes = [
            state
            for state in group.get("providers", {}).values()
            if state.get("status") == "budget_deferred"
            or (
                state.get("status") == "failed"
                and state.get("reason")
                in {"primary_failed", "insufficient_qualified_subject_results"}
            )
        ]
        skipped = any(c.get("coverage_category") == category for c in deferred)
        unread = urls - {d.get("url") for d in readable}
        failure = None
        acquisition = "not_attempted"
        if network_failed and not group.get("attempted_at"):
            status, failure = "not_run", "network_environment_blocked"
        elif group.get("status") == "budget_deferred":
            status, failure = "budget_deferred", "budget_deferred"
        elif not planned or group.get("status") in {None, "pending", "not_run"}:
            status, failure = "not_run", "not_checked"
        elif not success:
            status, failure, acquisition = "failed", "source_unreachable", "search_only"
        elif failed:
            status = "partial" if readable else "failed"
            failure = public_failure_class(failed[0].get("error_code"))
            acquisition = "body_unavailable"
        elif unfinished_routes:
            status = "partial"
            failure = (
                "budget_deferred"
                if any(s.get("status") == "budget_deferred" for s in unfinished_routes)
                else "source_unreachable"
            )
            acquisition = "body_obtained" if readable else "search_only"
        elif unread or skipped or (group.get("subject_results", 0) > 0 and not readable):
            status = "partial"
            failure = (
                "budget_deferred"
                if coverage.get("stop_reason")
                in {
                    "http_limit_reached",
                    "byte_limit_reached",
                    "document_limit_reached",
                    "max_elapsed_seconds_reached",
                }
                or skipped
                else "not_checked"
            )
            acquisition = "body_obtained" if readable else "search_only"
        else:
            status = "partial" if group.get("coverage_scope") == "intent_only" else "completed"
            failure = "not_checked" if status == "partial" else None
            acquisition = "body_obtained" if readable else "no_qualified_results"
        previous = _latest([coverage.get("previous_successful_checks", {}).get(category)])
        checked = _latest([group.get("checked_at")]) if status == "completed" else None
        # 缓存仍沿用来源原检查时间，失败只更新尝试时间。
        times = [_latest([d.get("checked_at")]) for d in readable]
        if status == "completed" and (checked is None or any(t is None for t in times)):
            status, failure, checked = "partial", "not_checked", None
        if checked and times and all(times):
            checked = min(checked, *times)
        rows.append(
            {
                "category": category,
                "planned": planned,
                "status": status,
                "last_attempt_at": _latest(
                    [group.get("attempted_at"), *[d.get("attempted_at") for d in docs]]
                ),
                "last_successful_check_at": max(checked, previous)
                if checked and previous
                else checked or previous,
                "failure_class": failure,
                "source_acquisition_status": acquisition,
            }
        )
    statuses = [r["status"] for r in rows]
    overall = (
        "complete"
        if all(s == "completed" for s in statuses)
        else "not_run"
        if all(s == "not_run" for s in statuses)
        else "failed"
        if all(s in {"failed", "not_run"} for s in statuses)
        else "partial"
    )
    if overall == "not_run" and not network_failed:
        # 旧任务可能有读取记录而没有类别计划；只承认发生过处理，不反推查全类别。
        if any(d.get("status") in {"created", "reused"} for d in documents):
            overall = "partial"
        elif any(d.get("status") == "failed" for d in documents) or coverage.get("stop_reason"):
            overall = "failed"
    return {
        "version": VERSION,
        "scope": coverage.get("research_scope", "limited_scope_check"),
        "status": overall,
        "network_preflight_failed": network_failed,
        "categories": rows,
    }


def stored_completion(coverage):
    result = completion_projection(coverage)
    return {
        **result,
        "categories": [
            {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in row.items()}
            for row in result["categories"]
        ],
    }
