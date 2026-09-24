"""同组正文回退的离线 Worker 验证；公司及 HTTP 内容全部虚构。"""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import web_research_service
from backend.app.config import PersonalEntitlementPolicy, RefreshPolicy
from backend.app.database import set_request_context
from backend.app.demo import BETA_TENANT_ID, BETA_USER_ID
from backend.app.models import (
    CompanyResearchJob,
    CompanySnapshot,
    Event,
    EventObservation,
    PersonalCompanyRequest,
    RawDocument,
    UsageLedger,
    utc_now,
)
from backend.app.personal_features import cancel_personal_company_request, create_refresh_request
from backend.app.research_coverage import SHORT_SEARCH_TOPICS
from backend.app.research_subject import load_subject, short_business_query
from backend.app.services import get_company_detail
from backend.app.source_fetcher import TrustedSourceFetcher
from backend.app.web_research_service import (
    SEARCH_GROUPS,
    _store_search_response,
    run_web_research_worker_once,
)
from backend.app.web_search import (
    MockSearchProvider,
    SearchProviderError,
    SearchRequest,
    SearchResult,
)
from tests.integration import test_incremental_research as incremental

database = incremental.database


def row(path):
    return SearchResult(
        provider_record_id=path,
        title="示例山海完成B轮融资",
        snippet="示例山海融资披露",
        url=f"https://example.com/{path}",
        source_name="虚构公开媒体",
        published_at="2026-06-01",
    )


@pytest.fixture
def research(database, tmp_path, monkeypatch):
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda *_: pytest.fail("Unexpected real HTTP call")
    )
    incremental.curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = incremental.initial(session, tmp_path)
        request = create_refresh_request(session, user, PersonalEntitlementPolicy(), company.id)
        user = incremental.curated.enter(session)
        subject = load_subject(session, company)
        queries = [short_business_query(subject, topic) for topic in SHORT_SEARCH_TOPICS.values()]
        state = SimpleNamespace(
            session=session,
            company=company,
            request_id=request.id,
            subject=subject,
            queries=queries,
            policy=incremental.POLICY,
            seen=[],
            after_http=lambda: None,
            primary=MockSearchProvider("baidu", {q: [row("blocked/source")] for q in queries}),
            fallback=MockSearchProvider("bocha", {q: [row("article")] for q in queries}),
        )

        def factory(policy):
            def handler(req):
                state.seen.append(req.url.path)
                state.after_http()
                if req.url.path == "/robots.txt":
                    return httpx.Response(
                        200,
                        text="User-agent: *\nDisallow: /blocked/\n",
                        headers={"content-type": "text/plain"},
                    )
                if req.url.path == "/timeout":
                    raise httpx.ReadTimeout("fictional timeout")
                body = (
                    "请完成验证码"
                    if req.url.path == "/captcha"
                    else '<meta property="article:published_time" content="2026-06-01">'
                    "<main><p>示例山海完成近4亿元B轮融资，本轮资金将用于产品研发。</p></main>"
                )
                return httpx.Response(
                    200, text=body, headers={"content-type": "text/html; charset=utf-8"}
                )

            return TrustedSourceFetcher(
                policy,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                resolver=lambda *_: {"93.184.216.34"},
            )

        def job():
            incremental.curated.enter(session)
            session.expire_all()
            request = session.get(PersonalCompanyRequest, state.request_id)
            return session.get(CompanyResearchJob, request.research_job_id)

        def step():
            return run_web_research_worker_once(
                session,
                incremental.curated.enter(session),
                {"baidu": state.primary, "bocha": state.fallback},
                state.policy,
                fetcher_factory=state.fetcher_factory,
            )

        def run(stop_stage=None):
            for _ in range(24):
                result = step()
                if result.stage == stop_stage or result.status in {
                    "completed",
                    "failed",
                    "budget_deferred",
                    "cancelled",
                    "idle",
                }:
                    return result
            pytest.fail("Worker did not reach the bounded stop")

        state.job, state.step, state.run, state.fetcher_factory = job, step, run, factory
        yield state


@pytest.mark.parametrize("path", ["article", "blocked/source", "captcha", "timeout"])
def test_readable_primary_skips_fallback_and_unreadable_uses_original_groups(research, path):
    r = research
    r.primary = MockSearchProvider("baidu", {q: [row(path)] for q in r.queries})
    assert r.run().status == "completed"
    job = r.job()
    assert len(r.primary.calls) == 2
    assert [call.query for call in r.fallback.calls] == ([] if path == "article" else r.queries), (
        job.coverage["documents"]
    )
    assert job.coverage["stats"]["search_calls"] == (2 if path == "article" else 4)
    assert job.coverage["stats"]["events_created"] == 1, job.coverage["documents"]
    assert job.coverage["stats"]["fetch_calls"] == len(r.seen) <= 8
    assert r.seen.count("/robots.txt") == 1
    assert r.seen.count("/article") == 1 and "/blocked/source" not in r.seen
    assert set(job.coverage["candidates"][0]["query_kinds"]) == {g for g, _ in SEARCH_GROUPS}
    assert r.step().status == "idle"
    assert len(r.primary.calls) == 2


def test_one_readable_group_is_retained_while_other_group_recovers(research, database):
    r = research
    old_events = {
        e.id: list(e.facts)
        for e in r.session.scalars(
            select(Event).where(Event.company_id == r.company.id, Event.status == "published")
        )
    }
    snapshot = r.session.scalar(
        select(CompanySnapshot).where(
            CompanySnapshot.company_id == r.company.id, CompanySnapshot.is_current.is_(True)
        )
    )
    old_snapshot_id = snapshot.id
    r.primary = MockSearchProvider(
        "baidu",
        {
            r.queries[0]: [row("blocked/source")],
            r.queries[1]: [row("existing")],
        },
    )
    r.fallback = MockSearchProvider(
        "bocha",
        {
            r.queries[0]: [
                row("blocked/source"),
                row("blocked/another"),
                row("article"),
                row("article"),
            ]
        },
    )
    assert r.run().status == "completed"
    job = r.job()
    assert [call.query for call in r.fallback.calls] == r.queries[:1]
    assert {d["url"] for d in job.coverage["documents"] if d["status"] == "created"} == {
        row("article").url,
        row("existing").url,
    }
    state = job.coverage["search_groups"][SEARCH_GROUPS[0][0]]["providers"]["bocha"]
    assert state["excluded_candidates"] == {"duplicate_url": 2, "robots_disallowed": 1}
    assert state["candidate_count_added"] == 1
    assert all(not path.startswith("/blocked/") for path in r.seen)
    assert {
        e.id: list(e.facts)
        for e in r.session.scalars(select(Event).where(Event.id.in_(old_events)))
    } == old_events
    assert r.session.get(CompanySnapshot, old_snapshot_id).last_checked_at is None
    detail = get_company_detail(
        r.session,
        incremental.curated.enter(r.session),
        r.company.id,
        RefreshPolicy(),
        auto_refresh_enabled=False,
    )
    assert old_events.keys() <= {e.id for e in detail.events}
    if database.postgres:
        document_ids = list(
            r.session.scalars(
                select(RawDocument.id).where(
                    RawDocument.canonical_url.in_([row("article").url, row("existing").url])
                )
            )
        )
        observation_ids = list(
            r.session.scalars(
                select(EventObservation.id).where(
                    EventObservation.event_id.in_(
                        select(Event.id).where(Event.company_id == r.company.id)
                    )
                )
            )
        )
        assert document_ids and observation_ids
        job_id = job.id
        r.session.rollback()
        set_request_context(r.session, BETA_USER_ID, BETA_TENANT_ID)
        assert r.session.get(PersonalCompanyRequest, r.request_id) is None
        assert r.session.get(CompanyResearchJob, job_id) is None
        assert not list(
            r.session.scalars(select(RawDocument).where(RawDocument.id.in_(document_ids)))
        )
        assert not list(
            r.session.scalars(
                select(EventObservation).where(EventObservation.id.in_(observation_ids))
            )
        )


@pytest.mark.parametrize("outcome", ["empty", "error", "unreadable"])
def test_exhausted_fallback_finishes_truthfully_without_repeating(research, outcome):
    r = research
    results = [row("captcha")] if outcome == "unreadable" else []
    r.fallback = MockSearchProvider("bocha", {q: results for q in r.queries})
    if outcome == "error":
        original = r.fallback.search

        def fail(request):
            original(request)
            raise SearchProviderError("timeout", "fictional failure", external_calls=1)

        r.fallback.search = fail
    assert r.run().status == "completed"
    job = r.job()
    assert len(r.primary.calls) == len(r.fallback.calls) == 2
    assert job.coverage["stats"]["events_created"] == 0
    assert all(d["status"] == "failed" for d in job.coverage["documents"])
    assert all(d["checked_at"] is None for d in job.coverage["documents"])
    assert r.step().status == "idle" and len(r.fallback.calls) == 2


@pytest.mark.parametrize("limit", ["search", "fetch", "bytes", "candidates", "time", "disabled"])
def test_no_new_fallback_dispatch_after_limit_or_gate_closes(research, limit):
    r = research
    stage = f"readability_fallback:{SEARCH_GROUPS[0][0]}"
    assert r.run(stage).stage == stage
    job = r.job()
    if limit == "fetch":
        job.coverage = {**job.coverage, "stats": {**job.coverage["stats"], "fetch_calls": 8}}
        r.session.commit()
    elif limit == "time":
        job.coverage = {
            **job.coverage,
            "started_at": (utc_now() - timedelta(seconds=181)).isoformat(),
        }
        r.session.commit()
    else:
        values = {
            "search": {"max_search_calls_per_job": 2},
            "bytes": {"max_download_bytes_per_job": 1},
            "candidates": {"max_candidate_urls": 1, "max_documents_per_job": 1},
            "disabled": {"incremental_research_enabled": False},
        }
        r.policy = replace(r.policy, **values[limit])
    assert r.run().status == ("budget_deferred" if limit == "search" else "completed")
    assert not r.fallback.calls and r.seen == ["/robots.txt"]
    assert r.job().coverage["stats"]["events_created"] == 0


def test_late_fallback_cache_reused_without_dispatch(research):
    r = research
    stage = f"readability_fallback:{SEARCH_GROUPS[0][0]}"
    assert r.run(stage).stage == stage
    job = r.job()
    cache_subject = replace(
        r.subject,
        reference_at=job.coverage.get("reference_at"),
        research_intent=job.coverage.get("research_intent", "discovery"),
        event_window_days=r.policy.recent_change_window_days,
    )
    for query in r.queries:
        cached = MockSearchProvider("bocha", {query: [row("article")]}).search(SearchRequest(query))
        _store_search_response(
            r.session, cache_subject, cached, SEARCH_GROUPS[0][0], query, r.policy
        )
    r.session.commit()
    assert r.run().status == "completed"
    job = r.job()
    assert not r.fallback.calls and job.coverage["stats"]["search_calls"] == 2
    assert job.coverage["stats"]["search_cache_hits"] == 2
    assert job.coverage["stats"]["events_created"] == 1


@pytest.mark.parametrize("during_dispatch", [False, True])
def test_cancelled_request_prevents_remaining_fallback_work(research, during_dispatch):
    r = research
    stage = f"readability_fallback:{SEARCH_GROUPS[0][0]}"
    assert r.run(stage).stage == stage

    def cancel():
        with Session(r.session.get_bind(), expire_on_commit=False) as cancelling:
            cancel_personal_company_request(
                cancelling,
                incremental.curated.enter(cancelling),
                r.request_id,
                reason="离线取消验证",
            )

    if during_dispatch:
        original = r.fallback.search

        def search(request):
            response = original(request)
            cancel()
            return response

        r.fallback.search = search
    else:
        cancel()
    assert r.run().status in {"cancelled", "idle"}
    assert r.job().status == "cancelled"
    assert len(r.fallback.calls) == int(during_dispatch)
    assert r.seen == ["/robots.txt"]
    assert not list(
        r.session.scalars(
            select(UsageLedger).where(
                UsageLedger.usage_state.in_(["reserved", "in_flight", "uncertain"])
            )
        )
    )


@pytest.mark.parametrize("outcome", ["success", "failed", "uncertain"])
def test_interrupted_fallback_reuses_settled_result_or_defers_uncertain_spend(
    research, monkeypatch, outcome
):
    r = research
    r.policy = replace(r.policy, max_search_calls_per_job=3)
    stage = f"readability_fallback:{SEARCH_GROUPS[0][0]}"
    assert r.run(stage).stage == stage
    original_search = r.fallback.search

    def search(request):
        response = original_search(request)
        if outcome == "uncertain":
            raise RuntimeError("simulated loss before settlement")
        if outcome == "failed":
            raise SearchProviderError("timeout", "fictional failure", external_calls=1)
        return response

    r.fallback.search = search
    original_response = web_research_service._provider_response

    def interrupt(*args, **kwargs):
        try:
            original_response(*args, **kwargs)
        except SearchProviderError:
            pass
        raise RuntimeError("simulated loss after settlement")

    monkeypatch.setattr(web_research_service, "_provider_response", interrupt)
    with pytest.raises(RuntimeError, match="simulated loss"):
        r.step()
    monkeypatch.setattr(web_research_service, "_provider_response", original_response)
    r.session.rollback()
    job = r.job()
    job.leased_until = utc_now() - timedelta(seconds=1)
    r.session.commit()
    result = r.run()
    assert len(r.fallback.calls) == 1
    assert result.status == ("budget_deferred" if outcome == "uncertain" else "completed")
    job = r.job()
    assert job.coverage["stats"]["events_created"] == int(outcome == "success")
    assert r.seen.count("/article") == int(outcome == "success")


def test_daily_budget_defers_then_resumes_without_consuming_attempt(research):
    r = research
    stage = f"readability_fallback:{SEARCH_GROUPS[0][0]}"
    assert r.run(stage).stage == stage
    r.policy = replace(r.policy, daily_search_call_limit=2)
    assert r.run().status == "budget_deferred"
    assert not r.fallback.calls
    r.policy = incremental.POLICY
    assert r.run().status == "completed"
    assert len(r.fallback.calls) == 2


def test_fallback_reuses_frozen_query_and_redirects_old_follow_up_stage(research, monkeypatch):
    r = research
    stage = f"readability_fallback:{SEARCH_GROUPS[0][0]}"
    assert r.run(stage).stage == stage
    job = r.job()
    job.current_stage = "source_recovery"
    r.session.commit()
    monkeypatch.setattr(
        web_research_service, "_query_for", lambda *_: pytest.fail("Query was rebuilt")
    )
    assert r.run().status == "completed"
    assert [call.query for call in r.fallback.calls] == r.queries


def test_deadline_between_robots_and_body_stops_http_and_new_search(research, monkeypatch):
    r = research
    r.primary = MockSearchProvider("baidu", {q: [row("article")] for q in r.queries})
    assert r.run("fetch").stage == "fetch"
    after_deadline = utc_now() + timedelta(seconds=181)
    r.after_http = lambda: monkeypatch.setattr(
        web_research_service, "utc_now", lambda: after_deadline
    )
    assert r.run().status == "completed"
    assert r.seen == ["/robots.txt"] and not r.fallback.calls
    job = r.job()
    assert job.coverage["stop_reason"] == "max_elapsed_seconds_reached"
    assert job.coverage["documents"][0]["error_code"] == "execution_time_limit_reached"


def test_deadline_after_primary_prevents_same_step_search_fallback(research, monkeypatch):
    r = research
    r.primary = MockSearchProvider("baidu")
    original = r.primary.search
    after_deadline = utc_now() + timedelta(seconds=181)

    def search(request):
        response = original(request)
        monkeypatch.setattr(web_research_service, "utc_now", lambda: after_deadline)
        return response

    r.primary.search = search
    assert r.run().status == "budget_deferred"
    assert len(r.primary.calls) == 1 and not r.fallback.calls and not r.seen
