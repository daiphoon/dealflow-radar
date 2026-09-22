"""Durable reservations for platform public-web research, not tenant billing."""

import hashlib
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, func, or_, select, text

from backend.app.database import set_request_context
from backend.app.models import CompanyResearchJob, PersonalCompanyRequest, UsageLedger, utc_now
from backend.app.services import user_has_role
from backend.app.web_search import MockSearchProvider

SCOPE = "platform_web"
HELD = ("reserved", "in_flight", "uncertain")
SEARCH_PROVIDERS = ("web_search_baidu", "web_search_bocha", "web_search_tavily")


class WebResearchBudgetDeferred(RuntimeError):
    pass


def subject_key(credit_code, fallback):
    return hashlib.sha256(str(credit_code or fallback).strip().upper().encode()).hexdigest()


def search_price(provider, policy):
    if isinstance(provider, MockSearchProvider):
        return Decimal("0")
    return getattr(policy.cost, f"{provider.code}_price_per_call", None)


def platform_usage():
    return or_(
        UsageLedger.quota_scope == SCOPE,
        (UsageLedger.provider.in_(SEARCH_PROVIDERS))
        & (UsageLedger.operation == "company_discovery"),
    )


def _lock(session):
    # One short transaction serializes all platform quota decisions. No HTTP under this lock.
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(30103001)"))


def _commit(session, user):
    session.commit()
    set_request_context(session, user.id, user.tenant_id)


def charged_calls():
    return case(
        (UsageLedger.usage_state.in_(HELD), UsageLedger.reserved_calls),
        (UsageLedger.usage_state == "released", 0),
        else_=func.coalesce(UsageLedger.external_calls, 0),
    )


def _window(since):
    # Outstanding liabilities survive day/month rollover until actually reconciled.
    return or_(
        UsageLedger.usage_state.in_(HELD),
        func.coalesce(UsageLedger.dispatched_at, UsageLedger.created_at) >= since,
    )


def search_call_count(session, *, since, exclude_id=None):
    query = select(func.coalesce(func.sum(charged_calls()), 0)).where(
        UsageLedger.provider.in_(SEARCH_PROVIDERS),
        UsageLedger.operation == "company_discovery",
        _window(since),
    )
    if exclude_id is not None:
        query = query.where(UsageLedger.id != exclude_id)
    return int(session.scalar(query) or 0)


def _check(session, row, policy, task_limit, task_baseline):
    now = utc_now()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = day - timedelta(days=day.weekday())
    month = day.replace(day=1)
    is_search = row.operation == "company_discovery"
    other = UsageLedger.id != row.id if row.id else True
    used = (
        session.scalar(
            select(func.coalesce(func.sum(charged_calls()), 0)).where(
                UsageLedger.quota_scope == SCOPE,
                UsageLedger.task_key == row.task_key,
                UsageLedger.operation == row.operation,
                other,
            )
        )
        or 0
    )
    if task_baseline + used + row.reserved_calls > task_limit:
        raise WebResearchBudgetDeferred("task call limit reached")
    if is_search:
        for since, limit, label in (
            (day, policy.daily_search_call_limit, "daily"),
            (month, policy.monthly_search_call_limit, "monthly"),
        ):
            if (
                search_call_count(session, since=since, exclude_id=row.id) + row.reserved_calls
                > limit
            ):
                raise WebResearchBudgetDeferred(f"{label} search call limit reached")
    if row.reserved_cost is None:
        raise WebResearchBudgetDeferred("price unknown: configure a conservative upper bound")
    if row.reserved_cost == 0:
        return
    amount = case(
        (UsageLedger.usage_state == "released", 0),
        (UsageLedger.usage_state.in_(HELD), UsageLedger.reserved_cost),
        (
            UsageLedger.usage_state == "legacy",
            case(
                (
                    or_(
                        UsageLedger.estimated_cost > 0,
                        UsageLedger.cost_status.in_(("actual", "confirmed_free")),
                    ),
                    UsageLedger.estimated_cost,
                ),
                else_=None,
            ),
        ),
        (UsageLedger.cost_status == "unknown", UsageLedger.reserved_cost),
        else_=UsageLedger.estimated_cost,
    )
    platform = platform_usage()
    company = or_(
        UsageLedger.subject_key == row.subject_key,
        UsageLedger.quota_scope.is_(None)
        & or_(
            UsageLedger.company_id == row.company_id if row.company_id else False,
            UsageLedger.company_id.is_(None),
        ),
    )
    for conditions, limit, label in (
        ([UsageLedger.task_key == row.task_key], policy.cost.task_limit, "task"),
        (
            [company, _window(day)],
            policy.cost.company_daily_limit,
            "company daily",
        ),
        (
            [company, _window(week)],
            policy.cost.company_weekly_limit,
            "company weekly",
        ),
        ([_window(week)], policy.cost.system_weekly_limit, "system weekly"),
        ([_window(month)], policy.cost.system_monthly_limit, "system monthly"),
    ):
        entries = select(amount).where(platform, other, *conditions)
        values = session.scalars(entries).all()
        # Historical zero means unpriced, not free. It cannot authorize new paid work.
        if any(value is None for value in values):
            raise WebResearchBudgetDeferred(f"{label} cost history requires reconciliation")
        if sum(values, Decimal("0")) + row.reserved_cost > limit:
            raise WebResearchBudgetDeferred(f"{label} money limit reached")


def reserve(
    session,
    user,
    policy,
    *,
    task_key,
    subject,
    company_id,
    provider,
    operation,
    token,
    calls,
    unit_price,
    task_limit,
    task_baseline=0,
    metrics=None,
):
    if calls < 1 or task_baseline < 0:
        raise ValueError("reservation counts must be positive")
    _lock(session)
    key = hashlib.sha256(f"{task_key}:{token}".encode()).hexdigest()
    row = session.scalar(
        select(UsageLedger)
        .where(UsageLedger.idempotency_key == key)
        .execution_options(populate_existing=True)
    )
    if row is not None:
        if (row.provider, row.operation, row.subject_key, row.reserved_calls) != (
            provider,
            operation,
            subject,
            calls,
        ):
            raise ValueError("reservation key reused for a different call")
        if row.usage_state != "reserved":
            raise WebResearchBudgetDeferred(f"call already {row.usage_state}; do not replay")
        _check(session, row, policy, task_limit, task_baseline)
        _commit(session, user)
        return row
    if unit_price is not None and (not unit_price.is_finite() or unit_price < 0):
        raise ValueError("invalid unit price")
    upper = unit_price if unit_price is not None else policy.cost.unknown_price_upper_bound
    if unit_price is None and (upper is None or upper <= 0):
        raise WebResearchBudgetDeferred("price unknown: configure a positive upper bound")
    row = UsageLedger(
        tenant_id=user.tenant_id,
        company_id=company_id,
        provider=provider,
        operation=operation,
        external_calls=None,
        estimated_cost=None,
        cost_status="unknown",
        usage_state="reserved",
        quota_scope=SCOPE,
        task_key=task_key,
        subject_key=subject,
        reserved_calls=calls,
        reserved_cost=upper * calls,
        quoted_unit_price=unit_price,
        pricing_version=policy.cost.version,
        idempotency_key=key,
        metrics={**(metrics or {}), "task_limit": task_limit, "task_baseline": task_baseline},
    )
    _check(session, row, policy, task_limit, task_baseline)
    session.add(row)
    _commit(session, user)
    return row


def dispatch(session, user, row, policy, *, cancelled):
    _lock(session)
    session.refresh(row, with_for_update=True)
    if row.usage_state != "reserved":
        raise WebResearchBudgetDeferred(f"call already {row.usage_state}; do not replay")
    if cancelled():
        row.usage_state = "released"
        row.external_calls = 0
        row.estimated_cost = Decimal("0")
        row.cost_status = "confirmed_free"
        row.settled_at = utc_now()
        _commit(session, user)
        raise WebResearchBudgetDeferred("cancelled before dispatch")
    _check(session, row, policy, row.metrics["task_limit"], row.metrics["task_baseline"])
    row.usage_state = "in_flight"
    row.dispatched_at = utc_now()
    _commit(session, user)


def settle(session, row, *, calls, metrics=None, actual_cost=None, receipt=None):
    """Caller commits evidence, task progress and settlement in one transaction."""
    session.refresh(row, with_for_update=True)
    if (
        type(calls) is not int
        or calls < 0
        or (
            actual_cost is not None
            and (
                not actual_cost.is_finite()
                or actual_cost < 0
                or actual_cost >= 10**12
                or actual_cost.as_tuple().exponent < -6
                or not receipt
            )
        )
    ):
        raise ValueError("settlement needs valid counts and a receipt for actual cost")
    if row.usage_state == "settled":
        if row.external_calls != calls or (
            actual_cost is not None and row.estimated_cost != actual_cost
        ):
            raise ValueError("conflicting settlement")
        return
    if row.usage_state not in ("in_flight", "uncertain"):
        raise ValueError("only dispatched usage can be settled")
    row.external_calls = calls
    row.usage_state = "settled"
    row.settled_at = utc_now()
    row.metrics = {**row.metrics, **(metrics or {})}
    if receipt:
        row.metrics = {**row.metrics, "settlement_receipt": receipt}
    if actual_cost is not None:
        row.estimated_cost, row.cost_status = actual_cost, "actual"
    elif calls == 0 or row.quoted_unit_price == 0:
        row.estimated_cost, row.cost_status = Decimal("0"), "confirmed_free"
    elif row.quoted_unit_price is not None:
        row.estimated_cost = row.quoted_unit_price * calls
        row.cost_status = "estimated"
    else:
        row.estimated_cost, row.cost_status = None, "unknown"
    if calls > row.reserved_calls or (
        row.estimated_cost is not None and row.estimated_cost > row.reserved_cost
    ):
        row.metrics = {**row.metrics, "reservation_exceeded": True}


def recover(session, *, task_key, cancelled=False):
    """Run only after acquiring an expired job lease, or cancelling that task."""
    rows = session.scalars(
        select(UsageLedger)
        .where(
            UsageLedger.quota_scope == SCOPE,
            UsageLedger.task_key == task_key,
            UsageLedger.usage_state.in_(HELD),
        )
        .with_for_update()
    ).all()
    uncertain = False
    for row in rows:
        if row.usage_state == "reserved" and cancelled:
            row.usage_state, row.cost_status = "released", "confirmed_free"
            row.external_calls, row.estimated_cost = 0, Decimal("0")
            row.settled_at = utc_now()
        elif row.usage_state in ("in_flight", "uncertain"):
            row.usage_state = "uncertain"
            uncertain = True
    return uncertain


def summary(session, task_key, *, legacy_calls=0):
    rows = session.scalars(
        select(UsageLedger).where(
            UsageLedger.quota_scope == SCOPE,
            UsageLedger.task_key == task_key,
        )
    ).all()
    unknown = bool(legacy_calls) or any(row.cost_status == "unknown" for row in rows)
    known = sum((row.estimated_cost or Decimal("0") for row in rows), Decimal("0"))
    held = sum(
        (
            row.reserved_cost or Decimal("0")
            for row in rows
            if row.usage_state in HELD or row.cost_status == "unknown"
        ),
        Decimal("0"),
    )
    return {
        **(
            {
                key: None
                if any(
                    r.usage_state in ("in_flight", "uncertain")
                    and r.operation == "matter_extraction"
                    for r in rows
                )
                else sum(getattr(r, key) or 0 for r in rows)
                for key in ("input_tokens", "output_tokens")
            }
            if any(r.operation == "matter_extraction" for r in rows)
            else {}
        ),
        "currency": "CNY",
        "status": "unknown"
        if unknown
        else (
            "estimated"
            if any(row.cost_status == "estimated" for row in rows)
            else "actual"
            if any(row.cost_status == "actual" for row in rows)
            else "confirmed_free"
        ),
        "amount": None if unknown else str(known),
        "known_subtotal": str(known),
        "reserved_upper_bound": None if legacy_calls else str(held),
        "uncertain_calls": sum(
            row.reserved_calls for row in rows if row.usage_state in ("in_flight", "uncertain")
        ),
    }


def reconcile(session, user, usage_id, *, calls, actual_cost, reference, reason):
    """Administrator reconciliation is explicit, audited and never issues another HTTP request."""
    if user.status != "active" or not user_has_role(session, user.id, "platform_admin"):
        raise PermissionError("platform_admin role required")
    if not reference.strip() or not reason.strip():
        raise ValueError("reconciliation reference and reason are required")
    if (
        type(calls) is not int
        or calls < 0
        or actual_cost is None
        or (
            not actual_cost.is_finite()
            or actual_cost < 0
            or actual_cost >= 10**12
            or actual_cost.as_tuple().exponent < -6
        )
    ):
        raise ValueError("valid calls and actual cost with at most 6 decimals are required")
    _lock(session)
    row = session.scalar(
        select(UsageLedger).where(
            UsageLedger.id == usage_id,
            platform_usage(),
        )
    )
    if row is None:
        raise ValueError("platform web usage not found")
    job = None
    if row.task_key and row.task_key.startswith("web-research:"):
        job = session.scalar(
            select(CompanyResearchJob)
            .where(CompanyResearchJob.id == UUID(row.task_key.split(":", 1)[1]))
            .with_for_update()
        )
    session.refresh(row, with_for_update=True)
    if row.usage_state not in ("uncertain", "settled", "legacy"):
        raise ValueError("recover an interrupted task before reconciliation")
    if row.cost_status == "actual":
        if row.external_calls != calls or row.estimated_cost != actual_cost:
            raise ValueError("conflicting reconciliation")
        return row
    if row.usage_state in ("settled", "legacy") and row.external_calls != calls:
        raise ValueError("confirmed call counts cannot be changed")
    previous = {
        "state": row.usage_state,
        "cost_status": row.cost_status,
        "calls": row.external_calls,
        "amount": str(row.estimated_cost) if row.estimated_cost is not None else None,
    }
    audit = {
        "previous": previous,
        "operator_id": str(user.id),
        "reason": reason.strip(),
        "at": utc_now().isoformat(),
    }
    if row.usage_state == "legacy":
        row.estimated_cost, row.cost_status = actual_cost, "actual"
        row.settled_at = utc_now()
        row.metrics = {
            **row.metrics,
            "settlement_receipt": reference.strip(),
            "reconciliation": audit,
        }
    else:
        # A settled price can be unknown even when its call count is already confirmed.
        row.usage_state = "uncertain"
        session.flush()
        settle(
            session,
            row,
            calls=calls,
            actual_cost=actual_cost,
            receipt=reference.strip(),
            metrics={"reconciliation": audit},
        )
    if previous["state"] == "uncertain" and row.task_key.startswith("web-research:"):
        if job is not None and job.status == "budget_deferred":
            # Billing evidence cannot recreate a lost search response or page body.
            baseline = job.coverage.get("budget_baseline", {})
            rows = session.scalars(
                select(UsageLedger).where(
                    UsageLedger.quota_scope == SCOPE, UsageLedger.task_key == row.task_key
                )
            ).all()
            stats = dict(job.coverage.get("stats", {}))
            for key, operation in (
                ("search_calls", "company_discovery"),
                ("fetch_calls", "evidence_fetch"),
                ("model_calls", "matter_extraction"),
            ):
                stats[key] = int(baseline.get(key, 0)) + sum(
                    item.external_calls or 0 for item in rows if item.operation == operation
                )
            job.external_calls = stats["search_calls"] + stats["fetch_calls"] + stats["model_calls"]
            job.coverage = {
                **job.coverage,
                "stats": stats,
                "stop_reason": "reconciled_result_unavailable",
            }
            job.status, job.current_stage, job.leased_until = "partial", "finalize", None
            for request in session.scalars(
                select(PersonalCompanyRequest).where(
                    PersonalCompanyRequest.research_job_id == job.id,
                    PersonalCompanyRequest.status == "budget_deferred",
                )
            ):
                request.status, request.leased_until = "partial", None
    return row
