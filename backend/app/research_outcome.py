from __future__ import annotations

from datetime import datetime

from backend.app.models import CompanyResearchJob
from backend.app.schemas import ResearchResultOut


def research_result(job: CompanyResearchJob | None) -> ResearchResultOut | None:
    """Return allowlisted result metadata, never private URLs, excerpts or owner details."""
    if (
        job is None
        or job.status != "completed"
        or not job.policy_version.startswith("bounded-web-")
    ):
        return None
    coverage = job.coverage
    stats = coverage.get("stats", {})
    passed = stats.get("quality_gate_passed", 0) if isinstance(stats, dict) else 0
    usable = isinstance(passed, int) and passed > 0
    documents = [item for item in coverage.get("documents", []) if isinstance(item, dict)]
    errors = {item.get("error_code") for item in documents}
    limitations: list[str] = []
    if "robots_disallowed" in errors:
        limitations.append(
            "部分来源未通过自动读取规则检查，可能为网站限制或规则文件不可用，未取得正文。"
        )
    if errors & {"blocked_scheme", "blocked_host", "blocked_address"}:
        limitations.append("部分来源未通过安全访问检查，未继续读取。")
    if "request_limit_exceeded" in errors or any(
        isinstance(coverage.get(key), dict)
        and (
            coverage[key].get("reason") == "document_or_request_limit_reached"
            or coverage[key].get("evidence_gap") == "document_or_request_limit_reached"
        )
        for key in ("source_recovery", "gap_follow_up")
    ):
        limitations.append("本轮读取预算已用完，尚有资料未检查；系统没有继续追加调用。")
    if any(item.get("status") == "failed" for item in documents) and not limitations:
        limitations.append("部分页面读取未完成，本轮资料覆盖不完整。")
    if any(
        isinstance(item.get("quality_gate"), dict)
        and item["quality_gate"].get("status") == "internal_only"
        for item in documents
    ):
        limitations.append("部分正文未达到主体、时间或重要变化的证据要求，未作为事实展示。")
    coverage_summary: list[str] = []
    search_calls = stats.get("search_calls") if isinstance(stats, dict) else None
    if type(search_calls) is int and search_calls >= 0:
        coverage_summary.append(f"记录了 {search_calls} 次搜索调用；不代表各类信息已经逐项查全。")
    if isinstance(coverage.get("documents"), list):
        readable = sum(item.get("status") in {"created", "reused"} for item in documents)
        coverage_summary.append(f"当时取得或复用了 {readable} 份正文；取得正文不等于事实已核实。")
    coverage_summary.append("各类信息的检查情况未完整记录，不能据此判断某类信息不存在。")
    finished_at = None
    value = coverage.get("completed_at")
    if isinstance(value, str):
        try:
            finished_at = datetime.fromisoformat(value)
        except ValueError:
            pass
    return ResearchResultOut(
        outcome="candidates_available" if usable else "no_usable_evidence",
        finished_at=finished_at,
        message=(
            "本轮查询已结束，取得待核实变化线索；不等于事实已确认或资料已查全。"
            if usable
            else "本轮查询已结束，暂未取得可展示的变化证据；不代表公司没有重要变化。"
        ),
        limitations=limitations,
        coverage_summary=coverage_summary,
    )
