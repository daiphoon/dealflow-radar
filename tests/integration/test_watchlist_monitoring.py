"""EV13: fictitious companies, isolated databases and Mock-only network calls."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from threading import Event as ThreadEvent
from types import SimpleNamespace

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, text
from sqlalchemy import event as sql_event
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from backend.app import watchlist_monitoring as monitor
from backend.app.config import PersonalEntitlementPolicy, RefreshPolicy, WatchlistMonitorPolicy
from backend.app.database import set_request_context
from backend.app.demo import ALPHA_USER_ID, BETA_USER_ID, NO_ACCESS_USER_ID
from backend.app.models import (
    Company,
    CompanyResearchJob,
    CompanySnapshot,
    CompanyWatchSchedule,
    Event,
    PersonalCompanyRequest,
    PersonalWatchlistItem,
    User,
    WebSearchCacheEntry,
    utc_now,
)
from backend.app.personal_features import create_refresh_request, list_personal_watchlist
from backend.app.source_fetcher import TrustedSourceFetcher
from backend.app.web_research_service import (
    prepare_pending_research_requests,
    run_web_research_worker_once,
)
from backend.app.web_search import MockSearchProvider
from tests.integration.test_bounded_web_research import (
    SHARED_COMPANY_ID,
    RecordingFetcherFactory,
    _providers,
)
from tests.integration.test_tender_storage import database as database
from tests.integration.test_web_budget_delivery import _admins, _policy, _session


def policy(**changes):
    return replace(_policy(), watchlist=WatchlistMonitorPolicy(enabled=True, **changes))


def setup_watch(database, followers=(NO_ACCESS_USER_ID,)):
    _admins(database)
    with Session(database.owner) as session:
        session.add_all(
            PersonalWatchlistItem(owner_user_id=uid, company_id=SHARED_COMPANY_ID)
            for uid in followers
        )
        session.commit()


def enqueue(database, current=None):
    session, user = _session(database)
    with session:
        return monitor.queue_due_watch_checks(session, user, current or policy(), dry_run=False)


def step(database, providers, fetcher=None, current=None, gate=None):
    session, user = _session(database)
    with session:
        return run_web_research_worker_once(
            session,
            user,
            providers,
            current or policy(),
            fetcher_factory=fetcher,
            watchlist_gate=gate,
        )


def drain(database, providers, fetcher=None, current=None, gate=None):
    results = []
    for _ in range(20):
        result = step(database, providers, fetcher, current, gate)
        results.append(result)
        if result.status in {"idle", "failed", "budget_deferred"}:
            return results
    pytest.fail("watchlist worker did not reach a bounded stopping point")


def test_due_dry_run_disabled_and_private_watchlist_boundary(database):
    setup_watch(database)
    session, user = _session(database)
    with session:
        result = monitor.queue_due_watch_checks(session, user, policy())
        assert result["company_ids"] == [str(SHARED_COMPANY_ID)]
        assert result["external_calls"] == 0
        assert session.scalar(select(func.count()).select_from(CompanyResearchJob)) == 0
        assert session.scalar(select(func.count()).select_from(CompanyWatchSchedule)) == 0
        off = replace(policy(), watchlist=WatchlistMonitorPolicy())
        assert (
            monitor.queue_due_watch_checks(session, user, off, dry_run=False)["status"]
            == "disabled"
        )
        if database.postgres:
            # Admin sees only the public company aggregate, never this user's raw watchlist.
            assert session.scalars(select(PersonalWatchlistItem)).all() == []
    session, user = _session(database, NO_ACCESS_USER_ID)
    with session:
        with pytest.raises(PermissionError):
            monitor.queue_due_watch_checks(session, user, policy())
        if database.postgres:
            assert (
                session.execute(text("SELECT * FROM public.watchlist_monitor_targets()")).all()
                == []
            )
    assert enqueue(database)["queued_count"] == 1
    assert enqueue(database)["merged_count"] == 1
    with Session(database.owner) as session:
        assert session.scalar(select(func.count()).select_from(PersonalCompanyRequest)) == 0
        session.get(User, NO_ACCESS_USER_ID).status = "suspended"
        session.commit()
    assert enqueue(database)["due_count"] == 0


def test_same_company_concurrent_followers_queue_once(database):
    if not database.postgres:
        pytest.skip("row locking requires PostgreSQL")
    setup_watch(database, (NO_ACCESS_USER_ID, BETA_USER_ID))
    barrier = Barrier(2)

    def run(uid):
        session, user = _session(database, uid)
        with session:
            barrier.wait(timeout=10)
            return monitor.queue_due_watch_checks(session, user, policy(), dry_run=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, (ALPHA_USER_ID, BETA_USER_ID)))
    assert sum(item["queued_count"] for item in results) == 1
    with Session(database.owner) as session:
        assert session.scalar(select(func.count()).select_from(CompanyResearchJob)) == 1


def test_finishing_worker_cannot_leave_scheduler_with_stale_due_state(database):
    if not database.postgres:
        pytest.skip("transaction interleaving requires PostgreSQL")
    setup_watch(database)
    enqueue(database)
    before_read, after_read, released = ThreadEvent(), ThreadEvent(), ThreadEvent()
    reads = [0]

    def before(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT") and "FROM company_watch_schedules" in statement:
            reads[0] += 1
            if reads[0] == 2:
                before_read.set()

    def after(conn, cursor, statement, parameters, context, executemany):
        if (
            reads[0] == 2
            and statement.startswith("SELECT")
            and "FROM company_watch_schedules" in statement
        ):
            after_read.set()
            assert released.wait(5)

    sql_event.listen(database.app, "before_cursor_execute", before)
    sql_event.listen(database.app, "after_cursor_execute", after)
    try:
        with Session(database.owner) as finishing:
            finishing.scalar(select(CompanyResearchJob)).status = "completed"
            state = finishing.get(CompanyWatchSchedule, SHARED_COMPANY_ID)
            state.next_check_at = utc_now() + timedelta(days=7)
            state.last_outcome = "succeeded"
            finishing.flush()
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(enqueue, database)
                try:
                    assert before_read.wait(5)
                    # An unlocked read would return the old due time here. Commit between
                    # that read and the active-job lookup to reproduce the duplicate window.
                    after_read.wait(0.3)
                    finishing.commit()
                finally:
                    released.set()
                assert future.result(timeout=5)["queued_count"] == 0
        with Session(database.owner) as session:
            assert session.scalar(select(func.count()).select_from(CompanyResearchJob)) == 1
    finally:
        sql_event.remove(database.app, "before_cursor_execute", before)
        sql_event.remove(database.app, "after_cursor_execute", after)


def test_three_category_delivery_keeps_success_separate_and_read_owner_only(database):
    setup_watch(database)
    assert enqueue(database)["queued_count"] == 1
    primary, fallback = _providers()
    fetcher = RecordingFetcherFactory()
    results = drain(database, {"baidu": primary, "bocha": fallback}, fetcher)
    assert results[-1].status == "idle"
    assert len(primary.calls) == 1 and not fallback.calls
    with Session(database.owner) as session:
        job = session.scalar(select(CompanyResearchJob))
        assert job.status == "completed"
        assert list(job.coverage["search_groups"]) == [monitor.GROUP]
        assert all(
            route is None
            for category, route in job.coverage["source_routes"].items()
            if category not in monitor.CATEGORIES
        )
        assert all(
            event.event_type in monitor.CATEGORIES for event in session.scalars(select(Event))
        )
        state = session.get(CompanyWatchSchedule, SHARED_COMPANY_ID)
        assert state.last_outcome == "succeeded"
        assert state.last_successful_check_at is not None
        assert monitor.aware(state.next_check_at) >= monitor.aware(
            state.last_successful_check_at
        ) + timedelta(days=7)
        assert session.scalar(select(func.count()).select_from(CompanySnapshot)) == 0
    assert enqueue(database)["due_count"] == 0
    session, user = _session(database, NO_ACCESS_USER_ID)
    with session:
        item = list_personal_watchlist(session, user, RefreshPolicy(), policy().watchlist)[0]
        assert item.monitoring.status == "succeeded" and item.last_checked_at is None
        assert item.monitoring.last_successful_check_at is not None
        assert "last_job_id" not in item.model_dump_json()
        if database.postgres:
            assert session.scalars(select(CompanyResearchJob)).all() == []
            with pytest.raises(StaleDataError):
                session.get(CompanyWatchSchedule, SHARED_COMPANY_ID).last_outcome = "forged"
                session.flush()


@pytest.mark.parametrize("stop", ["unfollow", "gate"])
def test_stop_after_search_prevents_fallback_and_documents(database, stop):
    setup_watch(database)
    enqueue(database)
    enabled = [True]

    class StopSearch(MockSearchProvider):
        def search(self, request):
            result = super().search(request)
            if stop == "gate":
                enabled[0] = False
            else:
                with Session(database.owner) as session:
                    session.execute(delete(PersonalWatchlistItem))
                    session.commit()
            return result

    primary, fallback = StopSearch("baidu"), MockSearchProvider("bocha")
    fetcher = RecordingFetcherFactory()
    drain(database, {"baidu": primary, "bocha": fallback}, fetcher, gate=lambda: enabled[0])
    assert len(primary.calls) == 1 and not fallback.calls and not fetcher.requests
    with Session(database.owner) as session:
        assert session.scalar(select(CompanyResearchJob)).status == "cancelled"
        assert session.get(CompanyWatchSchedule, SHARED_COMPANY_ID).last_successful_check_at is None


def test_unfollow_after_robots_prevents_body_request(database):
    setup_watch(database)
    enqueue(database)
    primary, fallback = _providers()
    calls = []

    def factory(fetch_policy):
        def handler(request):
            calls.append(str(request.url))
            with Session(database.owner) as session:
                session.execute(delete(PersonalWatchlistItem))
                session.commit()
            return httpx.Response(404)

        return TrustedSourceFetcher(
            fetch_policy,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            allow_private_test_hosts=True,
        )

    drain(database, {"baidu": primary, "bocha": fallback}, factory)
    assert len(calls) == 1 and calls[0].endswith("/robots.txt")
    with Session(database.owner) as session:
        assert session.scalar(select(CompanyResearchJob)).status == "cancelled"


def test_budget_failure_preserves_old_success_and_respects_exponential_retry(database):
    setup_watch(database)
    enqueue(database)
    old = utc_now() - timedelta(days=30)
    with Session(database.owner) as session:
        session.get(CompanyWatchSchedule, SHARED_COMPANY_ID).last_successful_check_at = old
        session.commit()
    from backend.app.config import WebResearchCostPolicy

    blocked = replace(policy(), cost=WebResearchCostPolicy(baidu_price_per_call=Decimal("1")))
    primary, fallback = _providers()
    # Quoted-price adapter delegates to in-memory Mock; it has no HTTP capability.
    providers = {"baidu": SimpleNamespace(code="baidu", search=primary.search), "bocha": fallback}
    result = step(database, providers, current=blocked)
    assert result.status == "budget_deferred" and not primary.calls
    assert step(database, providers, current=blocked).status == "idle"
    with Session(database.owner) as session:
        state = session.get(CompanyWatchSchedule, SHARED_COMPANY_ID)
        assert monitor.aware(state.last_successful_check_at) == old
        assert state.consecutive_failures == 1
        state.cooldown_until = state.next_check_at = utc_now() - timedelta(seconds=1)
        session.commit()
    assert step(database, providers, current=blocked).status == "budget_deferred"
    with Session(database.owner) as session:
        state = session.get(CompanyWatchSchedule, SHARED_COMPANY_ID)
        assert state.consecutive_failures == 2
        assert monitor.aware(state.cooldown_until) > utc_now() + timedelta(hours=47)
        assert monitor.aware(state.last_successful_check_at) == old


def test_manual_request_waits_then_reuses_watch_cache_and_completes_original_scope(database):
    setup_watch(database)
    enqueue(database)
    session, owner = _session(database, NO_ACCESS_USER_ID)
    with session:
        request_id = create_refresh_request(
            session, owner, PersonalEntitlementPolicy(), SHARED_COMPANY_ID
        ).id
    session, user = _session(database)
    with session:
        assert prepare_pending_research_requests(session, user, policy()) == 0
        set_request_context(session, user.id, user.tenant_id)
        assert session.get(PersonalCompanyRequest, request_id).research_job_id is None
    primary, fallback = _providers()
    fetcher = RecordingFetcherFactory()
    drain(database, {"baidu": primary, "bocha": fallback}, fetcher)
    with Session(database.owner) as session:
        jobs = session.scalars(select(CompanyResearchJob)).all()
        assert len(jobs) == 2 and all(job.status == "completed" for job in jobs)
        manual = next(job for job in jobs if job.trigger_type == "manual")
        assert len(manual.coverage["search_groups"]) == 2
        assert session.get(PersonalCompanyRequest, request_id).research_job_id == manual.id
        assert manual.coverage["stats"]["search_cache_hits"] >= 1
    assert len(primary.calls) == 2


def test_schedule_reuses_existing_manual_job(database):
    setup_watch(database)
    session, owner = _session(database, NO_ACCESS_USER_ID)
    with session:
        create_refresh_request(session, owner, PersonalEntitlementPolicy(), SHARED_COMPANY_ID)
    session, user = _session(database)
    with session:
        prepare_pending_research_requests(session, user, policy())
    assert enqueue(database)["merged_count"] == 1
    primary, fallback = _providers()
    drain(database, {"baidu": primary, "bocha": fallback}, RecordingFetcherFactory())
    with Session(database.owner) as session:
        assert session.scalar(select(func.count()).select_from(CompanyResearchJob)) == 1
        assert session.get(CompanyWatchSchedule, SHARED_COMPANY_ID).last_successful_check_at


def test_repeated_no_new_documents_backoff_cache_age_and_downgrade_refusal(database):
    setup_watch(database)
    enqueue(database)
    primary, fallback = MockSearchProvider("baidu"), MockSearchProvider("bocha")
    providers = {"baidu": primary, "bocha": fallback}
    drain(database, providers)
    for count in (2, 3):
        with Session(database.owner) as session:
            state = session.get(CompanyWatchSchedule, SHARED_COMPANY_ID)
            state.last_successful_check_at = utc_now() - timedelta(days=8)
            state.cooldown_until = state.next_check_at = utc_now() - timedelta(seconds=1)
            for entry in session.scalars(select(WebSearchCacheEntry)):
                entry.fetched_at = utc_now() - timedelta(days=8)
                entry.expires_at = utc_now() + timedelta(days=6)
            session.commit()
        assert enqueue(database)["queued_count"] == 1
        drain(database, providers)
        with Session(database.owner) as session:
            state = session.get(CompanyWatchSchedule, SHARED_COMPANY_ID)
            assert state.consecutive_no_change_runs == count
    assert len(primary.calls) == 3
    with Session(database.owner) as session:
        state = session.get(CompanyWatchSchedule, SHARED_COMPANY_ID)
        assert monitor.aware(state.next_check_at) >= monitor.aware(
            state.last_successful_check_at
        ) + timedelta(days=14)
    with pytest.raises(RuntimeError, match="history exists"):
        command.downgrade(Config("alembic.ini"), "0030")


def test_unverified_company_not_scheduled(database):
    setup_watch(database)
    with Session(database.owner) as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        company.identity_status = "unresolved"
        session.commit()
    assert enqueue(database)["due_count"] == 0
    session, user = _session(database)
    with session:
        set_request_context(session, user.id, user.tenant_id)
        assert monitor.targets(session, user) == []


def test_failure_does_not_refresh_success_and_retries_after_due_time(database, monkeypatch):
    setup_watch(database)
    enqueue(database)
    primary, fallback = _providers()
    providers = {"baidu": primary, "bocha": fallback}

    def broken_stage(*_):
        raise RuntimeError("fixture worker interruption before dispatch")

    with monkeypatch.context() as patch:
        patch.setattr("backend.app.web_research_service._process_search_group", broken_stage)
        assert step(database, providers).status == "failed"
    assert not primary.calls
    assert enqueue(database)["due_count"] == 0
    with Session(database.owner) as session:
        state = session.get(CompanyWatchSchedule, SHARED_COMPANY_ID)
        assert state.last_outcome == "failed" and state.last_successful_check_at is None
        state.next_check_at = state.cooldown_until = utc_now() - timedelta(seconds=1)
        session.commit()
    assert enqueue(database)["queued_count"] == 1


def test_batch_limit_and_no_access_without_own_follow(database):
    from uuid import uuid4

    setup_watch(database)
    with Session(database.owner) as session:
        for index in range(2):
            company = Company(
                id=uuid4(),
                tenant_id=None,
                legal_name=f"虚构巡检测试{index}有限公司",
                credit_code=f"91310000MOCK00000{index}",
                identity_status="verified",
                identity_verification_basis="public_crosscheck",
                visibility_scope="public",
            )
            session.add(company)
            session.flush()
            session.add(
                PersonalWatchlistItem(owner_user_id=NO_ACCESS_USER_ID, company_id=company.id)
            )
        session.commit()
    result = enqueue(database, policy(max_companies_per_run=1))
    assert result["due_count"] == 3 and result["queued_count"] == 1
    assert result["deferred_count"] == 2
    if database.postgres:
        session, user = _session(database, NO_ACCESS_USER_ID)
        with session:
            # A caller-created object cannot impersonate the public role tables in the definer.
            session.execute(text("CREATE TEMP TABLE roles (id uuid, code text)"))
            session.execute(text("INSERT INTO roles VALUES (gen_random_uuid(), 'platform_admin')"))
            assert (
                session.execute(text("SELECT * FROM public.watchlist_monitor_targets()")).all()
                == []
            )
        with Session(database.owner) as session:
            session.execute(delete(PersonalWatchlistItem))
            session.commit()
        session, user = _session(database, NO_ACCESS_USER_ID)
        with session:
            assert session.scalars(select(CompanyWatchSchedule)).all() == []
