"""EV12 uses isolated databases, fake prices and no real network services."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from backend.app import web_research_budget as budget
from backend.app.config import PersonalEntitlementPolicy, WebResearchCostPolicy, WebResearchPolicy
from backend.app.database import build_session_factory, set_request_context
from backend.app.demo import ALPHA_USER_ID, BETA_USER_ID, NO_ACCESS_USER_ID
from backend.app.models import (
    CompanyResearchJob,
    Role,
    UsageLedger,
    User,
    UserRoleAssignment,
    utc_now,
)
from backend.app.personal_features import create_refresh_request
from backend.app.web_research_service import run_web_research_worker_once
from tests.integration.test_bounded_web_research import (
    SHARED_COMPANY_ID,
    RecordingFetcherFactory,
    _providers,
)
from tests.integration.test_tender_storage import database as database

D = Decimal


def _admins(database):
    with build_session_factory(database.owner)() as session:
        role = session.scalar(select(Role).where(Role.code == "platform_admin"))
        session.add_all(
            UserRoleAssignment(user_id=uid, role_id=role.id)
            for uid in (ALPHA_USER_ID, BETA_USER_ID)
        )
        session.commit()


def _session(database, uid=ALPHA_USER_ID):
    session = build_session_factory(database.app)()
    user = session.get(User, uid)
    set_request_context(session, user.id, user.tenant_id)
    return session, user


def _policy(**overrides):
    costs = dict(
        task_limit=D("10"),
        company_daily_limit=D("10"),
        company_weekly_limit=D("10"),
        system_weekly_limit=D("10"),
        system_monthly_limit=D("10"),
        unknown_price_upper_bound=D("1"),
    )
    costs.update(overrides)
    return WebResearchPolicy(cost=WebResearchCostPolicy(**costs))


def _reserve(session, user, policy, **overrides):
    args = dict(
        task_key="web:test",
        subject="fictional-company",
        company_id=SHARED_COMPANY_ID,
        provider="web_search_baidu",
        operation="company_discovery",
        token="search:0",
        calls=1,
        unit_price=D("0.25"),
        task_limit=4,
    )
    args.update(overrides)
    return budget.reserve(session, user, policy, **args)


def test_cost_states_unknown_free_estimate_actual_and_durable_reservation(database):
    _admins(database)
    session, user = _session(database)
    with session:
        with pytest.raises(budget.WebResearchBudgetDeferred, match="price unknown"):
            _reserve(session, user, WebResearchPolicy(), unit_price=None)
        session.rollback()
        set_request_context(session, user.id, user.tenant_id)
        policy = _policy()
        row = _reserve(session, user, policy, unit_price=None)
        session.expire_all()
        assert row.external_calls is None and row.estimated_cost is None
        assert row.usage_state == "reserved" and row.reserved_cost == 1
        assert _reserve(session, user, policy, unit_price=None).id == row.id
        budget.dispatch(session, user, row, policy, cancelled=lambda: False)
        budget.settle(session, row, calls=1)
        session.commit()
        set_request_context(session, user.id, user.tenant_id)
        result = budget.summary(session, row.task_key)
        assert result["amount"] is None and result["status"] == "unknown"
        assert D(result["reserved_upper_bound"]) == 1
        budget.reconcile(
            session,
            user,
            row.id,
            calls=1,
            actual_cost=D("0.123456"),
            reference="fixture-receipt-1",
            reason="虚构账单核对",
        )
        session.commit()
        set_request_context(session, user.id, user.tenant_id)
        assert row.cost_status == "actual" and row.estimated_cost == D("0.123456")
        assert row.metrics["reconciliation"]["previous"]["amount"] is None
        for token, price, status in (
            ("free", D("0"), "confirmed_free"),
            ("estimated", D("0.25"), "estimated"),
        ):
            other = _reserve(session, user, policy, token=token, unit_price=price)
            budget.dispatch(session, user, other, policy, cancelled=lambda: False)
            budget.settle(session, other, calls=1)
            session.commit()
            set_request_context(session, user.id, user.tenant_id)
            assert other.cost_status == status
        assert budget.summary(session, row.task_key)["status"] == "estimated"
        before = session.scalar(select(func.count()).select_from(UsageLedger))
        with pytest.raises(budget.WebResearchBudgetDeferred, match="already settled"):
            _reserve(session, user, policy)
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == before
        with pytest.raises(RuntimeError, match="retain accounting history"):
            command.downgrade(Config("alembic.ini"), "0029")


def test_cancel_timeout_uncertainty_and_confirmed_failure_retry_accounting(database):
    _admins(database)
    session, user = _session(database)
    with session:
        policy = _policy()
        reserved = _reserve(session, user, policy)
        with pytest.raises(budget.WebResearchBudgetDeferred, match="cancelled"):
            budget.dispatch(session, user, reserved, policy, cancelled=lambda: True)
        assert reserved.usage_state == "released" and reserved.external_calls == 0
        active = _reserve(session, user, policy, token="retry:1", calls=2)
        budget.dispatch(session, user, active, policy, cancelled=lambda: False)
        # A timeout or process loss after dispatch keeps its full bound across period rollover.
        active.dispatched_at = utc_now() - timedelta(days=70)
        active.created_at = active.dispatched_at
        session.commit()
        set_request_context(session, user.id, user.tenant_id)
        assert budget.recover(session, task_key=active.task_key, cancelled=True)
        session.commit()
        set_request_context(session, user.id, user.tenant_id)
        assert active.usage_state == "uncertain" and active.external_calls is None
        assert budget.search_call_count(session, since=utc_now()) == 2
        with pytest.raises(budget.WebResearchBudgetDeferred, match="already uncertain"):
            _reserve(session, user, policy, token="retry:1", calls=2)
        # A late authoritative result releases only the unused portion, never duplicates the charge.
        budget.settle(session, active, calls=1, metrics={"status": "failed"})
        session.commit()
        set_request_context(session, user.id, user.tenant_id)
        budget.settle(session, active, calls=1)
        retry = _reserve(session, user, policy, token="retry:2")
        budget.dispatch(session, user, retry, policy, cancelled=lambda: False)
        budget.settle(session, retry, calls=1, metrics={"status": "completed"})
        session.commit()
        set_request_context(session, user.id, user.tenant_id)
        assert session.scalar(select(func.sum(UsageLedger.external_calls))) == 2
        assert D(budget.summary(session, active.task_key)["amount"]) == D("0.5")
        with pytest.raises(ValueError, match="conflicting settlement"):
            budget.settle(session, active, calls=0)


@pytest.mark.parametrize(
    "cap",
    [
        "calls",
        "task_limit",
        "company_daily_limit",
        "company_weekly_limit",
        "system_weekly_limit",
        "system_monthly_limit",
    ],
)
def test_two_workers_cannot_reserve_the_last_platform_allowance(database, cap):
    if not database.postgres:
        pytest.skip("concurrency and RLS are PostgreSQL guarantees")
    _admins(database)
    policy = _policy(**({cap: D("0.25")} if cap != "calls" else {}))
    if cap == "calls":
        policy = replace(policy, daily_search_call_limit=1)
    barrier = Barrier(2)

    def attempt(index):
        session, user = _session(database, (ALPHA_USER_ID, BETA_USER_ID)[index])
        with session:
            barrier.wait(timeout=10)
            try:
                row = _reserve(
                    session,
                    user,
                    policy,
                    token=f"attempt:{index}",
                    task_key="shared-task" if cap == "task_limit" else f"task:{index}",
                )
                return row.usage_state
            except budget.WebResearchBudgetDeferred:
                session.rollback()
                return "deferred"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(results) == ["deferred", "reserved"]
    with build_session_factory(database.owner)() as session:
        assert session.scalar(select(func.sum(UsageLedger.reserved_calls))) == 1


def test_platform_usage_rls_and_legacy_unpriced_spend(database):
    _admins(database)
    session, admin = _session(database)
    with session:
        row = _reserve(session, admin, _policy(), unit_price=D("0"))
        row_id = row.id
    session, other = _session(database, BETA_USER_ID)
    with session:
        assert budget.search_call_count(session, since=utc_now()) == 1
        if database.postgres:
            ordinary = session.get(User, NO_ACCESS_USER_ID)
            set_request_context(session, ordinary.id, ordinary.tenant_id)
            assert session.get(UsageLedger, row_id) is None
            with pytest.raises(DBAPIError):
                session.execute(
                    text(
                        "INSERT INTO usage_ledger (id, tenant_id, provider, operation, "
                        "external_calls, input_tokens, output_tokens, estimated_cost, metrics, "
                        "idempotency_key, created_at, updated_at, quota_scope, "
                        "task_key, subject_key) "
                        "VALUES (:id, :tenant, 'web_search_baidu', "
                        "'company_discovery', 0, 0, 0, 0, "
                        "'{}', :key, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                        "'platform_web', 'fake', 'fake')"
                    ),
                    {"id": uuid4(), "tenant": ordinary.tenant_id, "key": uuid4().hex},
                )
            session.rollback()
    with build_session_factory(database.owner)() as session:
        user = session.get(User, ALPHA_USER_ID)
        session.add(
            UsageLedger(
                tenant_id=user.tenant_id,
                provider="web_search_baidu",
                operation="company_discovery",
                external_calls=1,
                estimated_cost=D("0"),
                idempotency_key=uuid4().hex,
                metrics={},
            )
        )
        session.commit()
    session, admin = _session(database)
    with session:
        with pytest.raises(
            budget.WebResearchBudgetDeferred, match="history requires reconciliation"
        ):
            _reserve(session, admin, _policy(), token="new-paid")
        free = _reserve(session, admin, _policy(), token="new-free", unit_price=D("0"))
        assert free.usage_state == "reserved"


def test_worker_result_lost_after_external_success_is_not_replayed(database, monkeypatch):
    _admins(database)
    session, owner = _session(database, NO_ACCESS_USER_ID)
    with session:
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), SHARED_COMPANY_ID)
    primary, fallback = _providers()
    providers = {"baidu": primary, "bocha": fallback}

    def crash(*args, **kwargs):
        raise RuntimeError("fixture crash after provider success")

    with monkeypatch.context() as patch:
        patch.setattr("backend.app.web_research_service._store_search_response", crash)
        session, admin = _session(database)
        with session, pytest.raises(RuntimeError, match="after provider success"):
            run_web_research_worker_once(
                session,
                admin,
                providers,
                WebResearchPolicy(),
                fetcher_factory=RecordingFetcherFactory(),
            )
    with build_session_factory(database.owner)() as session:
        job = session.scalar(select(CompanyResearchJob))
        job.leased_until = utc_now() - timedelta(seconds=1)
        session.commit()
        job_id = job.id
    session, admin = _session(database)
    with session:
        result = run_web_research_worker_once(session, admin, providers, WebResearchPolicy())
        assert result.status == "budget_deferred"
        assert result.cost_summary["amount"] is None
        set_request_context(session, admin.id, admin.tenant_id)
        usage = session.scalar(select(UsageLedger).where(UsageLedger.quota_scope == budget.SCOPE))
        assert usage.usage_state == "uncertain"
        budget.reconcile(
            session,
            admin,
            usage.id,
            calls=1,
            actual_cost=D("0"),
            reference="fixture-response-id",
            reason="调用已完成但正文未入库",
        )
        session.commit()
        set_request_context(session, admin.id, admin.tenant_id)
        result = run_web_research_worker_once(session, admin, providers, WebResearchPolicy())
        assert result.status == "completed"
        set_request_context(session, admin.id, admin.tenant_id)
        assert (
            session.get(CompanyResearchJob, job_id).coverage["stop_reason"]
            == "reconciled_result_unavailable"
        )
    assert len(primary.calls) == 1 and not fallback.calls


def test_identity_and_business_share_company_scope_under_rls(database):
    from backend.app.identity_research import run_identity_step
    from backend.app.models import Company, PersonalCompanyRequest
    from tests.integration.test_public_identity_research import CODE, NAME, fetcher, providers

    _admins(database)
    with build_session_factory(database.owner)() as session:
        session.get(Company, SHARED_COMPANY_ID).credit_code = None
        request = PersonalCompanyRequest(
            owner_user_id=NO_ACCESS_USER_ID,
            request_type="inclusion",
            requested_name=NAME,
            requested_credit_code=CODE,
            target_key=f"credit:{CODE}",
            status="pending",
        )
        session.add(request)
        session.commit()
        request_id = request.id
    searches = providers()
    for _ in range(4):
        session, admin = _session(database)
        with session:
            result = run_identity_step(session, admin, searches, WebResearchPolicy(), fetcher)
            assert result.cost_summary["status"] == "confirmed_free"
    session, admin = _session(database)
    with session:
        request = session.get(PersonalCompanyRequest, request_id)
        assert request.company_id is not None and request.status == "research_queued"
        run_web_research_worker_once(
            session, admin, searches, WebResearchPolicy(), fetcher_factory=fetcher
        )
        set_request_context(session, admin.id, admin.tenant_id)
        rows = session.scalars(
            select(UsageLedger).where(UsageLedger.quota_scope == budget.SCOPE)
        ).all()
        assert len({row.task_key.split(":", 1)[0] for row in rows}) == 2
        assert {row.subject_key for row in rows} == {budget.subject_key(CODE, None)}
        assert all(row.usage_state == "settled" for row in rows)
        assert (
            sum(row.external_calls for row in rows if row.task_key == f"identity:{request_id}") == 5
        )


@pytest.mark.parametrize("primary_failed", [False, True])
def test_deferred_fallback_resumes_without_repaying_the_primary(database, primary_failed):
    from backend.app.web_search import MockSearchProvider
    from tests.integration.test_bounded_web_research import SEARCH_GROUPS, _query

    class PricedFixture:
        def __init__(self, code, *, fail=False):
            self.code = code
            self.mock = MockSearchProvider(
                code, failures={_query(SEARCH_GROUPS[0][1]): "request_failed"} if fail else None
            )

        def search(self, request):
            return self.mock.search(request)

    _admins(database)
    session, owner = _session(database, NO_ACCESS_USER_ID)
    with session:
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), SHARED_COMPANY_ID)
    primary = PricedFixture("baidu", fail=primary_failed)
    fallback = PricedFixture("bocha")
    policy = WebResearchPolicy(cost=WebResearchCostPolicy(baidu_price_per_call=D("0")))
    session, admin = _session(database)
    with session:
        assert (
            run_web_research_worker_once(
                session, admin, {"baidu": primary, "bocha": fallback}, policy
            ).status
            == "budget_deferred"
        )
    assert len(primary.mock.calls) == 1 and not fallback.mock.calls
    session, admin = _session(database)
    with session:
        policy = replace(policy, cost=replace(policy.cost, bocha_price_per_call=D("0")))
        assert (
            run_web_research_worker_once(
                session, admin, {"baidu": primary, "bocha": fallback}, policy
            ).status
            == "partial"
        )
    assert len(primary.mock.calls) == 1 and len(fallback.mock.calls) == 1
