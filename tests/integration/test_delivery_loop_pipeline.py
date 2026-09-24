from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import event as sql_event
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import RefreshPolicy
from backend.app.models import CompanyResearchJob, Event, EventObservation, UsageLedger
from backend.app.research_extraction import document_matters
from backend.app.research_subject import load_subject
from backend.app.services import get_company_detail
from backend.app.web_research_budget import SCOPE, WebResearchBudgetDeferred, subject_key
from tests.integration import test_curated_import as curated
from tests.integration.test_incremental_research import initial
from tests.integration.test_research_matter_storage import POLICY, ingest

database = curated.database


@pytest.mark.parametrize("kind", ["html", "pdf"])
def test_fetch_store_window_extract_late_passage(database, tmp_path, kind):
    import json
    from uuid import uuid4

    import httpx

    from backend.app.config import SourceMonitoringPolicy
    from backend.app.research_extraction import extraction_payload
    from backend.app.research_subject import BusinessExcerptSelector
    from backend.app.source_fetcher import TrustedSourceFetcher
    from backend.app.web_research_service import (
        _candidate_event,
        _content_quality_decision,
        _raw_document,
        _source,
    )
    from tests.pdf_fixtures import unicode_pdf

    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        subject = load_subject(session, company)
        target = "示例山海于2024年6月1日完成A轮融资。"
        body = (
            "示例山海资料介绍。"
            + "无关背景。" * 220
            + "示例山海其他资料。"
            + "无关背景。" * 900
            + target
        )
        assert body.index(target) > 1500
        content = unicode_pdf(body) if kind == "pdf" else f"<article>{body}</article>".encode()

        def handler(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(404, headers={"content-type": "text/plain"})
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf" if kind == "pdf" else "text/html"},
                content=content,
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            fetcher = TrustedSourceFetcher(
                SourceMonitoringPolicy(),
                client=client,
                allow_private_test_hosts=True,
                sleep=lambda _: None,
            )
            batch = fetcher.check(
                source_type="single_page",
                root_domain="example.com",
                start_url=f"https://example.com/late.{kind}",
                retention_policy="minimal_excerpt",
                conditional_state={},
                excerpt_selector=BusinessExcerptSelector(subject, 6000),
            )
        discovered = batch.documents[0]
        assert target in discovered.excerpt and discovered.excerpt.index(target) > 1500
        assert discovered.metadata["retained_characters"] == len(discovered.excerpt)
        for span in discovered.metadata["retained_spans"]:
            assert span["stored_end"] <= len(discovered.excerpt)
            assert (
                span["stored_end"] - span["stored_start"]
                == span["source_end"] - span["source_start"]
            )
        quality = _content_quality_decision(
            subject,
            title=discovered.title,
            excerpt=discovered.excerpt,
            published_at=discovered.published_at,
            observed_at=datetime.now(UTC),
            policy=POLICY,
        )
        doc, new = _raw_document(
            session,
            _source(session),
            subject,
            {},
            discovered,
            SimpleNamespace(id=uuid4(), coverage={"matter_processing": True}),
            quality,
        )
        windows = json.loads(
            extraction_payload(subject, doc.payload["excerpt"], POLICY)["messages"][1]["content"]
        )["source_windows"]
        assert any(target in window["text"] for window in windows)
        event, created, quality = _candidate_event(
            session, subject, doc, _source(session), POLICY, new_document=new, actor=user
        )
        assert created and quality.eligible and event.occurred_at.date().isoformat() == "2024-06-01"


def test_multicategory_storage_projection_and_repeat(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        body = "示例山海于2024年6月1日完成A轮融资，同时于2026年9月1日向港交所递交招股书。"
        first, _, _, quality = ingest(session, user, company, body, "https://example.com/multi")
        assert set(quality.matter_categories) == {"financing_cap_table", "exit_liquidity"}
        assert quality.matters_created == 2 and quality.eligible
        assert len(quality.matter_ids) == 2
        _, created, _, again = ingest(session, user, company, body, "https://example.com/multi")
        assert not created and again.matters_created == 0
        assert (
            len(
                session.scalars(
                    select(EventObservation).where(
                        EventObservation.event_id.in_(
                            [__import__("uuid").UUID(v) for v in quality.matter_ids]
                        )
                    )
                ).all()
            )
            == 2
        )
        session.commit()
        user = curated.enter(session)
        detail = get_company_detail(
            session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
        )
        assert len(detail.platform_unconfirmed_leads) == 2 and not detail.events
        historical = next(e for e in detail.platform_unconfirmed_leads if e.id == first.id)
        assert historical.temporal_status == "historical"
        assert not session.dirty


@pytest.mark.parametrize("uncertain", [False, True])
def test_known_model_limit_preserves_rules_unknown_spend_blocks(database, tmp_path, uncertain):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        job = CompanyResearchJob(
            company_id=company.id,
            created_by_user_id=user.id,
            policy_version=POLICY.version,
            coverage={"stats": {}, "budget_baseline": {}},
        )
        session.add(job)
        session.flush()
        for i in range(3):
            session.add(
                UsageLedger(
                    company_id=company.id,
                    tenant_id=user.tenant_id,
                    idempotency_key=f"fixture-{job.id}-{i}",
                    provider="web_model_mock",
                    operation="matter_extraction",
                    external_calls=1,
                    quota_scope=SCOPE,
                    task_key=f"web-research:{job.id}",
                    subject_key=subject_key(company.credit_code, company.id),
                    usage_state="uncertain" if uncertain and i == 2 else "settled",
                    reserved_calls=1,
                    metrics={},
                    cost_status="estimated",
                )
            )
        session.flush()
        doc = SimpleNamespace(
            id=job.id,
            payload={"excerpt": "示例山海完成A轮融资。"},
            content_hash="fixture",
            published_on=None,
        )
        provider = SimpleNamespace(
            code="mock", extract=lambda _: pytest.fail("must not dispatch fourth call")
        )
        policy = replace(
            POLICY,
            matter_model_enabled=True,
            extraction_model="fixture-model",
            model_input_price_per_million=Decimal("1"),
            model_output_price_per_million=Decimal("1"),
        )
        if uncertain:
            with pytest.raises(WebResearchBudgetDeferred):
                document_matters(
                    session, user, job, load_subject(session, company), doc, policy, provider
                )
        else:
            result = document_matters(
                session, user, job, load_subject(session, company), doc, policy, provider
            )
            assert result and job.coverage["model_budget_exhausted"]
            assert any(d["reason"] == "model_budget_exhausted" for d in doc._matter_dispositions)


def test_ipo_stages_link_without_merging_events(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        first, _, _, _ = ingest(
            session,
            user,
            company,
            "示例山海于2026年9月1日向港交所递交上市申请。",
            "https://example.com/application",
        )
        second, created, _, _ = ingest(
            session,
            user,
            company,
            "示例山海于2026年9月1日向港交所提交招股书。",
            "https://example.com/reprint",
        )
        assert second.id == first.id and not created
        hearing, created, _, _ = ingest(
            session,
            user,
            company,
            "示例山海于2026年9月20日通过港交所聆讯。",
            "https://example.com/hearing",
        )
        assert hearing.id != first.id and created
        observation = session.scalar(
            select(EventObservation).where(EventObservation.event_id == hearing.id)
        )
        assert observation.candidate_payload["relations"][0]["event_id"] == str(first.id)


@pytest.mark.parametrize("size", [10, 100])
def test_candidate_comparison_query_count_is_bounded(database, tmp_path, size):
    from time import perf_counter

    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        for index in range(size):
            session.add(
                Event(
                    company_id=company.id,
                    visibility_scope="platform_shared",
                    event_type="financing_cap_table",
                    event_subtype="company_financing",
                    status="candidate",
                    direction="neutral",
                    materiality_score=0,
                    risk_severity="none",
                    confidence_score=0,
                    source_quality="B",
                    event_fingerprint=f"fixture-{index}",
                    title=f"虚构既有事项{index}",
                    summary="未取得更多可读证据",
                    facts=[],
                    uncertainties=[],
                )
            )
        session.flush()
        queries = []

        def count(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                queries.append(statement)

        sql_event.listen(database.app, "before_cursor_execute", count)
        try:
            started = perf_counter()
            load_subject(session, company)
            subject_queries, subject_ms = len(queries), (perf_counter() - started) * 1000
            queries.clear()
            started = perf_counter()
            _, created, _, _ = ingest(
                session,
                user,
                company,
                "示例山海于2026年9月1日完成A轮融资。",
                "https://example.com/count",
                datetime(2026, 9, 2, tzinfo=UTC),
            )
            comparison_queries, comparison_ms = len(queries), (perf_counter() - started) * 1000
            queries.clear()
            started = perf_counter()
            detail = get_company_detail(
                session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
            )
            page_queries, page_ms = len(queries), (perf_counter() - started) * 1000
            assert any(
                e.event_type == "financing_cap_table" for e in detail.platform_unconfirmed_leads
            )
        finally:
            sql_event.remove(database.app, "before_cursor_execute", count)
        print(
            f"comparison_benchmark events={size} selects={comparison_queries} "
            f"postgres={database.postgres} ms={comparison_ms:.2f} "
            f"subject_selects={subject_queries} subject_ms={subject_ms:.2f} "
            f"page_selects={page_queries} page_ms={page_ms:.2f}"
        )
        assert created and comparison_queries < 60, (size, comparison_queries)
        assert subject_queries == 2 and page_queries < 60


def test_partial_result_is_terminal_and_keeps_readable_outcome(database, tmp_path):
    from backend.app.config import PersonalEntitlementPolicy
    from backend.app.models import PersonalCompanyRequest
    from backend.app.personal_features import create_refresh_request
    from backend.app.research_outcome import research_result
    from backend.app.web_research_service import ACTIVE_JOB_STATUSES, _finalize

    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        job = CompanyResearchJob(
            company_id=company.id,
            created_by_user_id=user.id,
            policy_version=POLICY.version,
            status="partial",
            current_stage="finalize",
            coverage={
                "matter_processing": True,
                "model_budget_exhausted": True,
                "stats": {"quality_gate_passed": 1},
                "documents": [
                    {
                        "status": "created",
                        "quality_gate": {"status": "eligible", "matter_ids": ["fixture"]},
                        "matter_categories": ["financing_cap_table"],
                    },
                    {"status": "failed", "error_code": "network_error"},
                ],
            },
        )
        session.add(job)
        session.flush()
        request = create_refresh_request(session, user, PersonalEntitlementPolicy(), company.id)
        user = curated.enter(session)
        request = session.get(PersonalCompanyRequest, request.id)
        request.status = "partial"
        request.research_job_id = job.id
        session.flush()
        result = _finalize(session, job)
        assert result.status not in ACTIVE_JOB_STATUSES
        assert job.coverage["completion_status"] == "partial"
        assert job.coverage["unique_matters_retained"] == 1
        visible = research_result(job)
        assert visible and visible.outcome == "candidates_available"
        assert any("模型次数" in reason for reason in visible.limitations)
        user = curated.enter(session)
        detail = get_company_detail(
            session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
        )
        assert detail.personal_research_result == visible


def test_professional_fetch_failure_is_settled_without_transport_log(database, tmp_path):
    from backend.app.config import PersonalEntitlementPolicy, WebResearchCostPolicy
    from backend.app.models import utc_now
    from backend.app.personal_features import create_refresh_request
    from backend.app.research_plan import TOPICS, topic_terms
    from backend.app.research_subject import short_business_query
    from backend.app.source_fetcher import SourceFetchError
    from backend.app.web_research_service import run_web_research_worker_once
    from backend.app.web_search import MockSearchProvider, SearchResult

    class BrokenProvider:
        request_count = 0
        downloaded_bytes = 0

        def extract(self, url, title, max_bytes):
            self.request_count += 1
            self.downloaded_bytes = 4
            raise SourceFetchError("network_error", "fictional provider interruption")

    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        create_refresh_request(session, user, PersonalEntitlementPolicy(), company.id)
        user = curated.enter(session)
        subject = load_subject(session, company)
        row = SearchResult(
            "source",
            "示例山海融资",
            "https://example.com/article",
            "示例山海完成融资",
            "虚构媒体",
            utc_now().date().isoformat(),
        )
        primary = MockSearchProvider(
            "tavily",
            {
                short_business_query(subject, topic_terms(t, POLICY)): [row]
                for t in list(TOPICS)[:2]
            },
        )
        policy = replace(
            POLICY,
            tavily_enabled=True,
            tavily_allowed_domains=("example.com",),
            primary_provider="tavily",
            fallback_provider="bocha",
            cost=WebResearchCostPolicy(
                tavily_extract_price_per_call=Decimal("0"),
                task_limit=Decimal("1"),
                company_daily_limit=Decimal("1"),
                company_weekly_limit=Decimal("1"),
                system_weekly_limit=Decimal("1"),
                system_monthly_limit=Decimal("1"),
            ),
        )
        provider = BrokenProvider()
        for _ in range(20):
            user = curated.enter(session)
            result = run_web_research_worker_once(
                session,
                user,
                {"tavily": primary, "bocha": MockSearchProvider("bocha")},
                policy,
                document_provider=provider,
                fetcher_factory=lambda _: pytest.fail("unexpected direct fetch"),
            )
            if result.status in {"completed", "failed", "budget_deferred"}:
                break
        assert result.status == "failed" and provider.request_count == 1
        curated.enter(session)
        job = session.get(CompanyResearchJob, result.job_id)
        assert job.coverage["completion_status"] == "partial"
        failed = job.coverage["documents"][0]
        assert failed["error_code"] == "network_error"
        rows = session.scalars(
            select(UsageLedger).where(UsageLedger.provider == "web_extract_tavily")
        ).all()
        assert len(rows) == 1 and rows[0].usage_state == "settled"
        assert rows[0].external_calls == 1 and rows[0].metrics["downloaded_bytes"] == 4


def test_search_cache_reused_across_task_times_but_not_intents_or_windows(database, tmp_path):
    from datetime import timedelta

    from backend.app.models import utc_now
    from backend.app.web_research_service import _cached_response, _store_search_response
    from backend.app.web_search import MockSearchProvider, SearchRequest, SearchResult

    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        _, company = initial(session, tmp_path, mode="identity_only")
        now = utc_now()
        subject = replace(
            load_subject(session, company),
            reference_at=now.isoformat(),
            event_window_days=90,
        )
        query = '"示例山海" 融资'
        row = SearchResult("fixture", "示例山海融资", "https://example.com/article", "", None, None)
        response = MockSearchProvider("bocha", {query: [row]}).search(SearchRequest(query))
        _store_search_response(session, subject, response, "business_capital", query, POLICY)
        session.flush()
        later = now + timedelta(days=1)
        next_task = replace(subject, reference_at=later.isoformat())
        cached = _cached_response(session, next_task, "bocha", query, later)
        assert cached is not None and cached.external_calls == 0
        for different in (
            replace(next_task, research_intent="maintenance"),
            replace(next_task, event_window_days=30),
        ):
            assert _cached_response(session, different, "bocha", query, later) is None
        expired = now + timedelta(days=POLICY.search_cache_ttl_days + 1)
        assert _cached_response(session, next_task, "bocha", query, expired) is None
