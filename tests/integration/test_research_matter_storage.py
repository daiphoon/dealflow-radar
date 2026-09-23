from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.app.config import (
    PersonalEntitlementPolicy,
    RefreshPolicy,
    WebResearchCostPolicy,
    WebResearchPolicy,
)
from backend.app.database import set_request_context
from backend.app.demo import BETA_TENANT_ID, BETA_USER_ID
from backend.app.models import (
    CompanyResearchJob,
    CompanySnapshot,
    Event,
    EventEvidence,
    EventObservation,
    RawDocument,
    UsageLedger,
    User,
    utc_now,
)
from backend.app.personal_features import create_refresh_request
from backend.app.research_matters import VERSION, digest
from backend.app.research_plan import TOPICS, topic_terms
from backend.app.research_subject import load_subject, short_business_query
from backend.app.services import get_company_detail
from backend.app.source_fetcher import DiscoveredDocument
from backend.app.web_research_service import (
    _candidate_event,
    _content_quality_decision,
    _raw_document,
    _source,
    run_web_research_worker_once,
)
from backend.app.web_search import MockSearchProvider, SearchResult
from tests.integration import test_curated_import as curated
from tests.integration.test_incremental_research import initial

database = curated.database
POLICY = WebResearchPolicy(
    incremental_research_enabled=True, topic_planning_enabled=True, matter_processing_enabled=True
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda *_: pytest.fail("Unexpected real HTTP")
    )


def ingest(session, user, company, body, url, day=None):
    subject = load_subject(session, company)
    discovered = DiscoveredDocument(
        url, "示例公开资料", day, digest(body), body, 200, None, None, "healthy"
    )
    quality = _content_quality_decision(
        subject,
        title=discovered.title,
        excerpt=body,
        published_at=day,
        observed_at=utc_now(),
        policy=POLICY,
    )
    document, new = _raw_document(
        session,
        _source(session),
        subject,
        {},
        discovered,
        SimpleNamespace(id=uuid4(), coverage={"matter_processing": True}),
        quality,
    )
    event, created, quality = _candidate_event(
        session, subject, document, _source(session), POLICY, new_document=new, actor=user
    )
    return event, created, document, quality


def test_curated_matter_maintenance_conflict_no_date_idempotency_and_permissions(
    database, tmp_path
):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path)
        baseline = session.scalar(
            select(Event).where(
                Event.company_id == company.id,
                Event.visibility_scope == "platform_shared",
                Event.published_on == datetime(2026, 6, 1).date(),
            )
        )
        facts = list(baseline.facts)
        published = baseline.published_on
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == company.id, CompanySnapshot.is_current.is_(True)
            )
        )
        event, created, doc, quality = ingest(
            session,
            user,
            company,
            "示例山海完成近一亿元A轮融资，由示例机构参与投资。",
            "https://example.com/same",
        )
        assert event.id == baseline.id and not created and not quality.eligible
        assert quality.matters_linked == 1
        event2, created2, _, _ = ingest(
            session, user, company, "示例山海完成近两亿元A轮融资。", "https://example.com/change"
        )
        assert event2.id != event.id and created2  # 无锚点不合并，日期未知保留为独立线索
        count = session.scalar(select(func.count()).select_from(EventObservation))
        ingest(
            session,
            user,
            company,
            "示例山海完成近一亿元A轮融资，由示例机构参与投资。",
            "https://example.com/same",
        )
        assert session.scalar(select(func.count()).select_from(EventObservation)) == count
        assert (
            baseline.facts == facts
            and baseline.published_on == published
            and snapshot.last_checked_at is None
        )
        company_id = company.id
        doc_id = doc.id
        session.commit()
        user = curated.enter(session)
        detail = get_company_detail(
            session, user, company_id, RefreshPolicy(), auto_refresh_enabled=False
        )
        shown = next(e for e in detail.events if e.id == baseline.id)
        assert {m.kind for m in shown.matter_observations} == {"same_facts"}
        assert all(not m.confirmed for m in shown.matter_observations)
    with Session(database.app) as session:
        set_request_context(session, BETA_USER_ID, BETA_TENANT_ID)
        user = session.get(User, BETA_USER_ID)
        detail = get_company_detail(
            session, user, company_id, RefreshPolicy(), auto_refresh_enabled=False
        )
        assert next(e for e in detail.events if e.id == baseline.id).matter_observations
        if database.postgres:
            with pytest.raises(RuntimeError, match="Retain matter observations"):
                command.downgrade(Config("alembic.ini"), "0033")
            assert session.get(RawDocument, doc_id) is None
            assert not list(
                session.scalars(
                    select(EventObservation).where(EventObservation.schema_version == VERSION)
                )
            )
            with pytest.raises(DBAPIError):
                with session.begin_nested():
                    session.add(
                        EventObservation(
                            event_id=baseline.id,
                            raw_document_id=doc_id,
                            schema_version=VERSION,
                            fact_version="forged",
                            observation_kind="initial",
                            date_precision="unknown",
                            candidate_payload={},
                            created_by=BETA_USER_ID,
                        )
                    )
                    session.flush()


def test_multiple_matters_ipo_dedupe_withdrawal_and_old_source(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        day = utc_now()
        body = "示例山海通过港交所聆讯。示例山海中标500万元合同。"
        first, created, doc, quality = ingest(
            session, user, company, body, "https://example.com/a", day
        )
        assert created and quality.matters_created == 2
        other, created, _, _ = ingest(
            session, user, company, "示例山海获得港交所聆讯通过。", "https://example.com/b", day
        )
        assert other.id != first.id and created  # 仅相同市场/阶段不能证明同一申请项目
        assert (
            ingest(session, user, company, "示例山海正式挂牌上市。", "https://example.com/undated")[
                0
            ]
            is not None
        )
        assert (
            ingest(
                session,
                user,
                company,
                "示例山海正式挂牌上市。",
                "https://example.com/old",
                datetime(2020, 1, 1, tzinfo=UTC),
            )[0]
            is not None
        )
        for row in session.scalars(select(EventEvidence).where(EventEvidence.event_id == first.id)):
            row.display_allowed = False
        session.commit()
        user = curated.enter(session)
        detail = get_company_detail(
            session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
        )
        shown = next(e for e in detail.platform_unconfirmed_leads if e.id == first.id)
        assert shown.facts == [] and shown.matter_observations == []
        ingest(session, user, company, body, "https://example.com/a", day)
        assert not any(
            row.display_allowed
            for row in session.scalars(
                select(EventEvidence).where(EventEvidence.event_id == first.id)
            )
        )


class Model:
    code = "mock"

    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def extract(self, payload):
        self.calls += 1
        assert "tools" not in payload
        if self.fail:
            raise ValueError("malformed response")
        return {"output": {"matters": []}, "input_tokens": 100, "output_tokens": 10}


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("cached_body", [True, False])
def test_worker_cached_professional_body_model_budget_and_restart(
    database, tmp_path, failure, cached_body
):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        create_refresh_request(session, user, PersonalEntitlementPolicy(), company.id)
        user = curated.enter(session)
        subject = load_subject(session, company)
        today = utc_now().date().isoformat()
        row = SearchResult(
            "source",
            "示例山海通过港交所聆讯",
            "https://example.com/a",
            "示例山海通过港交所聆讯",
            "示例媒体",
            today,
            source_body=f"发布时间：{today}\n示例山海通过港交所聆讯。" if cached_body else None,
            body_provider="tavily",
        )
        primary = MockSearchProvider(
            "tavily",
            {
                short_business_query(subject, topic_terms(t, POLICY)): [row]
                for t in list(TOPICS)[:2]
            },
        )
        costs = WebResearchCostPolicy(
            tavily_extract_price_per_call=Decimal("0"),
            task_limit=Decimal("1"),
            company_daily_limit=Decimal("1"),
            company_weekly_limit=Decimal("1"),
            system_weekly_limit=Decimal("1"),
            system_monthly_limit=Decimal("1"),
        )
        policy = replace(
            POLICY,
            tavily_enabled=True,
            tavily_allowed_domains=("example.com",),
            primary_provider="tavily",
            fallback_provider="bocha",
            matter_model_enabled=True,
            extraction_model="fixture-model",
            model_input_price_per_million=Decimal("2"),
            model_output_price_per_million=Decimal("8"),
            cost=costs,
        )

        class Documents:
            calls = 0

            def extract(self, url, title, max_bytes):
                from backend.app.research_acquisition import acquired_document

                self.calls += 1
                return acquired_document(
                    url, title, f"发布时间：{today}\n示例山海通过港交所聆讯。", "tavily"
                ), 100

        documents = Documents()
        model = Model(failure)
        for _ in range(20):
            user = curated.enter(session)
            result = run_web_research_worker_once(
                session,
                user,
                {"tavily": primary, "bocha": MockSearchProvider("bocha")},
                policy,
                matter_provider=model,
                document_provider=documents,
                fetcher_factory=lambda _: pytest.fail("must reuse acquired body"),
            )
            if result.status in {"completed", "budget_deferred"}:
                break
        assert documents.calls == int(not cached_body)
        assert model.calls == 1
        assert result.to_dict()["input_tokens"] == (None if failure else 100)
        assert result.to_dict()["output_tokens"] == (None if failure else 10)
        assert result.status == ("budget_deferred" if failure else "completed")
        user = curated.enter(session)
        job = session.get(CompanyResearchJob, result.job_id)
        rows = list(
            session.scalars(select(UsageLedger).where(UsageLedger.operation == "matter_extraction"))
        )
        if not cached_body:
            fetches = list(
                session.scalars(
                    select(UsageLedger).where(UsageLedger.provider == "web_extract_tavily")
                )
            )
            assert (
                len(fetches) == 1
                and fetches[0].usage_state == "settled"
                and fetches[0].external_calls == 1
            )
        assert len(rows) == 1 and rows[0].usage_state == ("uncertain" if failure else "settled")
        if not failure:
            assert job.coverage["stats"]["model_calls"] == 1 and job.coverage["stats"][
                "fetch_calls"
            ] == int(not cached_body)
            assert rows[0].input_tokens == 100 and rows[0].estimated_cost == Decimal("0.00028")
            # 只改本地校验版本，复用已结算输出，不派发第二次模型请求。
            from unittest.mock import patch

            from backend.app.research_extraction import document_matters

            document = session.scalar(
                select(RawDocument).where(RawDocument.canonical_url == "https://example.com/a")
            )
            with patch(
                "backend.app.research_extraction.VALIDATION_VERSION", "fixture-validation-v2"
            ):
                document_matters(session, user, job, subject, document, policy, model)
            assert model.calls == 1
            assert any(
                r.get("outcome") == "cache_replay"
                and r.get("validation_version") == "fixture-validation-v2"
                for r in job.coverage["matter_dispositions"]
            )
            assert "source_body" not in str(job.coverage)
            assert (
                session.scalar(
                    select(func.count()).select_from(Event).where(Event.company_id == company.id)
                )
                == 1
            )
        user = curated.enter(session)
        run_web_research_worker_once(
            session,
            user,
            {"tavily": primary, "bocha": MockSearchProvider("bocha")},
            policy,
            matter_provider=model,
        )
        assert documents.calls == int(not cached_body)
        assert model.calls == 1
