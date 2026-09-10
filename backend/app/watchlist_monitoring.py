"""Bounded public checks for watched companies; personal ownership stays private."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.app.database import set_request_context
from backend.app.models import (
    Company,
    CompanyResearchJob,
    CompanyWatchSchedule,
    PersonalWatchlistItem,
    User,
    utc_now,
)
from backend.app.research_coverage import SEARCH_GROUP_MODULES, SUCCESSFUL_SEARCHES
from backend.app.services import user_has_role

GROUP = "business_capital"
CATEGORIES = SEARCH_GROUP_MODULES[GROUP]
ACTIVE = ("queued", "running", "partial", "budget_deferred")


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _admin(session, user):
    if user.status != "active" or not user_has_role(session, user.id, "platform_admin"):
        raise PermissionError("platform_admin role required")


def targets(session, user):
    _admin(session, user)
    if session.get_bind().dialect.name == "postgresql":
        return list(
            session.execute(
                text("SELECT company_id, followed_at FROM public.watchlist_monitor_targets()")
            )
        )
    return list(
        session.execute(
            select(PersonalWatchlistItem.company_id, func.min(PersonalWatchlistItem.created_at))
            .join(User, User.id == PersonalWatchlistItem.owner_user_id)
            .join(Company, Company.id == PersonalWatchlistItem.company_id)
            .where(
                User.status == "active",
                Company.tenant_id.is_(None),
                Company.visibility_scope == "public",
                Company.identity_status == "verified",
                Company.credit_code.is_not(None),
            )
            .group_by(PersonalWatchlistItem.company_id)
        )
    )


def allowed(session, user, job):
    gate = session.info.get("watchlist_gate", lambda: False)
    if not gate() or job.cancel_requested_at is not None:
        return False
    return any(company_id == job.company_id for company_id, _ in targets(session, user))


def request_guard(session, user, job):
    # A fresh transaction observes unfollow/account suspension committed during the preceding HTTP.
    engine = session.get_bind()
    gate = session.info.get("watchlist_gate", lambda: False)
    user_id, tenant_id, job_id = user.id, user.tenant_id, job.id

    def check():
        if not gate():
            return False
        with Session(engine) as probe:
            set_request_context(probe, user_id, tenant_id)
            actor = probe.get(User, user_id)
            current = probe.get(CompanyResearchJob, job_id)
            if actor is None or actor.status != "active" or current is None:
                return False
            probe.info["watchlist_gate"] = gate
            try:
                return allowed(probe, actor, current)
            except PermissionError:
                return False

    return check


def due_at(state, followed_at):
    values = [aware(followed_at)]
    if state is not None:
        values.append(aware(state.next_check_at))
        if state.cooldown_until:
            values.append(aware(state.cooldown_until))
    return max(values)


def interval_days(policy, no_change_runs):
    steps = min(no_change_runs // policy.no_change_backoff_after, 8)
    return min(policy.interval_days * 2**steps, policy.max_interval_days)


def queue_due_watch_checks(session, user, web_policy, *, dry_run=True, now=None):
    from backend.app.web_research_service import _initial_coverage

    _admin(session, user)
    policy = web_policy.watchlist
    current = aware(now or utc_now())
    if not dry_run and not policy.enabled:
        return {"status": "disabled", "queued_count": 0, "external_calls": 0}
    candidates = []
    for company_id, followed_at in targets(session, user):
        state = session.get(CompanyWatchSchedule, company_id)
        due = due_at(state, followed_at)
        if due <= current:
            candidates.append((due, company_id, followed_at))
    candidates.sort(key=lambda item: (item[0], str(item[1])))
    selected = candidates[: policy.max_companies_per_run]
    prices = [
        web_policy.cost.baidu_price_per_call,
        web_policy.cost.bocha_price_per_call,
    ]
    prices = [
        value if value is not None else web_policy.cost.unknown_price_upper_bound
        for value in prices
    ]
    upper_bound = (
        max(prices) * web_policy.max_search_calls_per_job * len(selected)
        if all(value is not None for value in prices)
        else None
    )
    result = {
        "status": "dry_run" if dry_run else "completed",
        "due_count": len(candidates),
        "queued_count": 0,
        "merged_count": 0,
        "deferred_count": max(0, len(candidates) - len(selected)),
        "company_ids": [str(item[1]) for item in selected],
        "categories": list(CATEGORIES),
        "interval_days": policy.interval_days,
        "external_calls": 0,
        "providers": [web_policy.primary_provider, web_policy.fallback_provider],
        "max_search_calls_per_company": web_policy.max_search_calls_per_job,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": None,
        "planned_cost_upper_bound": str(upper_bound) if upper_bound is not None else None,
        "cost_status": "unknown",
    }
    if dry_run:
        return result
    for _, company_id, followed_at in selected:
        # Same company lock and active-job unique index as manual request preparation.
        company = session.scalar(select(Company).where(Company.id == company_id).with_for_update())
        if company is None or company_id not in {item[0] for item in targets(session, user)}:
            continue
        state = session.scalar(
            select(CompanyWatchSchedule)
            .where(CompanyWatchSchedule.company_id == company_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if due_at(state, followed_at) > current:
            continue
        if state is None:
            state = CompanyWatchSchedule(
                company_id=company_id,
                policy_version=policy.version,
                next_check_at=aware(followed_at),
            )
            session.add(state)
        active = session.scalar(
            select(CompanyResearchJob).where(
                CompanyResearchJob.company_id == company_id, CompanyResearchJob.status.in_(ACTIVE)
            )
        )
        if active is not None:
            state.last_job_id, state.last_outcome = active.id, "queued"
            result["merged_count"] += 1
            continue
        coverage = _initial_coverage(web_policy)
        coverage["search_groups"] = {GROUP: coverage["search_groups"][GROUP]}
        coverage["source_routes"] = {
            category: route if category in CATEGORIES else None
            for category, route in coverage["source_routes"].items()
        }
        coverage["watchlist_monitor"] = {
            "version": policy.version,
            "categories": list(CATEGORIES),
            "scheduled_for": due_at(state, followed_at).isoformat(),
            "interval_days": interval_days(policy, state.consecutive_no_change_runs or 0),
        }
        job = CompanyResearchJob(
            company_id=company_id,
            created_by_user_id=user.id,
            trigger_type="watchlist",
            status="queued",
            current_stage=f"search:{GROUP}",
            policy_version=web_policy.version,
            coverage=coverage,
        )
        session.add(job)
        session.flush()
        state.last_job_id, state.last_outcome = job.id, "queued"
        state.policy_version = policy.version
        result["queued_count"] += 1
    session.commit()
    set_request_context(session, user.id, user.tenant_id)
    return result


def record_outcome(session, user, job, policy, *, commit=True):
    state = session.scalar(
        select(CompanyWatchSchedule)
        .where(CompanyWatchSchedule.company_id == job.company_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if state is None:
        return
    if job.status not in {"completed", "cancelled", "failed", "budget_deferred"}:
        if state.last_job_id == job.id:
            state.last_outcome = "checking"
            state.last_attempt_at = job.heartbeat_at
            if commit:
                session.commit()
                set_request_context(session, user.id, user.tenant_id)
        return
    heartbeat = aware(job.heartbeat_at).isoformat() if job.heartbeat_at else "none"
    key = f"{job.id}:{job.status}:{heartbeat}"
    if state.last_outcome_key == key:
        return
    current = utc_now()
    coverage = job.coverage or {}
    group = coverage.get("search_groups", {}).get(GROUP, {})
    documents = coverage.get("documents", [])
    checked = group.get("checked_at")
    try:
        checked = datetime.fromisoformat(checked) if checked else None
    except (ValueError, TypeError):
        checked = None
    successful = (
        job.status == "completed"
        and group.get("status") == "completed"
        and checked is not None
        and checked.tzinfo is not None
        and any(
            item.get("status") in SUCCESSFUL_SEARCHES
            for item in group.get("providers", {}).values()
        )
        and not any(item.get("status") == "failed" for item in documents)
        and int(coverage.get("candidate_index", 0)) >= len(coverage.get("candidates", []))
        and coverage.get("stop_reason") is None
    )
    state.last_outcome_key, state.last_job_id = key, job.id
    state.last_attempt_at = job.heartbeat_at or current
    state.policy_version = policy.version
    state.cooldown_until = current + timedelta(hours=policy.cooldown_hours)
    if successful:
        if state.last_successful_check_at is None or checked > aware(
            state.last_successful_check_at
        ):
            state.last_successful_check_at = checked
            changed = any(item.get("status") == "created" for item in documents)
            state.consecutive_no_change_runs = (
                0 if changed else state.consecutive_no_change_runs + 1
            )
        state.consecutive_failures = 0
        state.last_outcome = "succeeded"
        state.next_check_at = max(
            state.cooldown_until,
            checked + timedelta(days=interval_days(policy, state.consecutive_no_change_runs)),
        )
    else:
        state.consecutive_failures += 1
        state.last_outcome = job.status if job.status != "completed" else "partial"
        wait = min(
            policy.cooldown_hours * 2 ** min(state.consecutive_failures - 1, 10),
            policy.failure_backoff_max_days * 24,
        )
        state.next_check_at = current + timedelta(hours=wait)
        state.cooldown_until = state.next_check_at
    if commit:
        session.commit()
        set_request_context(session, user.id, user.tenant_id)


def monitoring_summary(session, company_id, policy):
    from backend.app.schemas import WatchlistMonitorOut

    if policy is None or not policy.enabled:
        return WatchlistMonitorOut(status="disabled", categories=list(CATEGORIES))
    state = session.get(CompanyWatchSchedule, company_id)
    if state is None:
        return WatchlistMonitorOut(status="not_scheduled", categories=list(CATEGORIES))
    job = session.get(CompanyResearchJob, state.last_job_id) if state.last_job_id else None
    status = state.last_outcome
    if job is not None and job.status in ACTIVE:
        status = "budget_deferred" if job.status == "budget_deferred" else "checking"
    next_check = max(
        aware(state.next_check_at),
        aware(state.cooldown_until) if state.cooldown_until else aware(state.next_check_at),
    )
    return WatchlistMonitorOut(
        status=status,
        categories=list(CATEGORIES),
        last_attempt_at=state.last_attempt_at,
        last_successful_check_at=state.last_successful_check_at,
        next_check_at=next_check,
    )
