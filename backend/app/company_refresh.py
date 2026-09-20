"""公司访问只安排后台任务；不在请求链路中获取外部资料。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from backend.app.models import CompanyResearchJob, utc_now
from backend.app.personal_features import PersonalFeatureLimitError, create_refresh_request
from backend.app.research_plan import TOPICS, successful_topic_checks

ACTIVE = {"queued", "running", "partial", "budget_deferred"}


def _aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def schedule_stale_company(session, user, detail, settings):
    def result(status, message, request_id=None):
        return {"status": status, "message": message, "request_id": request_id}

    if not settings.auto_refresh_enabled:
        return result("disabled", "自动更新尚未启用；当前展示已有资料。")
    if not detail.is_platform_shared or detail.identity_status != "verified":
        return result("not_eligible", "该公司暂不符合公开资料自动更新条件。")
    latest = session.scalar(
        select(CompanyResearchJob)
        .where(CompanyResearchJob.company_id == detail.id)
        .order_by(CompanyResearchJob.created_at.desc(), CompanyResearchJob.id.desc())
        .limit(1)
    )
    if latest and latest.status in ACTIVE:
        return result(
            "budget_deferred" if latest.status == "budget_deferred" else "refreshing",
            "已有后台更新任务；当前继续展示原资料。",
        )
    now = utc_now()
    histories = session.scalars(
        select(CompanyResearchJob)
        .where(CompanyResearchJob.company_id == detail.id, CompanyResearchJob.status == "completed")
        .order_by(CompanyResearchJob.created_at.desc())
        .limit(32)
    ).all()
    checks = successful_topic_checks([job.coverage for job in histories])
    if set(checks) == set(TOPICS) and all(
        checked + timedelta(days=settings.refresh_policy.recent_query_ttl_days) > now
        for checked in checks.values()
    ):
        return result("not_due", "各类最近检查仍在保鲜期内，未重复联网；事实日期保持原记录。")
    baseline = detail.last_checked_at
    if baseline is None and detail.data_as_of:
        baseline = datetime.combine(detail.data_as_of, datetime.min.time(), tzinfo=UTC)
    if (
        baseline
        and _aware(baseline) + timedelta(days=settings.refresh_policy.recent_query_ttl_days) > now
    ):
        return result("not_due", "现有资料仍在保鲜期内，无需重复查询。")
    if (
        latest
        and _aware(latest.created_at)
        + timedelta(hours=settings.personal_entitlement_policy.request_cooldown_hours)
        > now
    ):
        return result("cooldown", "最近已尝试更新，冷却期内保留现有结果。")
    try:
        request = create_refresh_request(
            session, user, settings.personal_entitlement_policy, detail.id
        )
    except PersonalFeatureLimitError:
        session.rollback()
        return result("budget_deferred", "本期更新额度不足；已有资料仍可查看。")
    if request.status in {"research_queued", "researching", "partial", "budget_deferred"}:
        return result(
            "budget_deferred" if request.status == "budget_deferred" else "queued",
            "资料已过保鲜期，已自动安排后台更新；当前展示原资料。",
            str(request.id),
        )
    return result("cooldown", "已有近期申请，冷却期内不重复更新。", str(request.id))
