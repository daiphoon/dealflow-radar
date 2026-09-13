from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.config import PersonalEntitlementPolicy, RefreshPolicy, WebResearchPolicy
from backend.app.database import set_request_context
from backend.app.demo import ALPHA_USER_ID, BETA_TENANT_ID, BETA_USER_ID
from backend.app.financing_events import SCHEMA_VERSION, digest
from backend.app.financing_storage import persist_financing_document
from backend.app.models import (
    Company,
    CompanyAlias,
    CompanyResearchJob,
    CompanySnapshot,
    Event,
    EventEvidence,
    EventObservation,
    IdentityResearchState,
    PersonalCompanyRequest,
    RawDocument,
    User,
)
from backend.app.personal_features import create_refresh_request
from backend.app.research_subject import load_subject
from backend.app.services import AccessDeniedError, get_company_detail
from backend.app.source_fetcher import DiscoveredDocument, TrustedSourceFetcher
from backend.app.web_research_service import (
    SEARCH_GROUPS,
    _candidate_event,
    _content_quality_decision,
    _query_for,
    _raw_document,
    _source,
    prepare_pending_research_requests,
    run_web_research_worker_once,
)
from backend.app.web_search import MockSearchProvider, SearchResult
from tests.curated_fixtures import CREDIT_CODE
from tests.integration import test_curated_import as curated

database = curated.database
POLICY = WebResearchPolicy(incremental_research_enabled=True)
DAY = datetime(2026, 6, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda *_: pytest.fail("Unexpected real HTTP call")
    )


def initial(session, tmp_path, mode="initial_data"):
    curated.apply(session, curated.loaded(tmp_path, mode=mode))
    user = curated.enter(session)
    company = session.scalar(select(Company).where(Company.credit_code == CREDIT_CODE))
    return user, company


def ingest(session, user, company, body, url="https://example.com/financing", day=DAY):
    subject = load_subject(session, company)
    source = _source(session)
    discovered = DiscoveredDocument(
        canonical_url=url,
        title="示例融资披露",
        published_at=day,
        content_hash=digest(body),
        excerpt=body,
        http_status=200,
        etag=None,
        last_modified=None,
        link_health_status="healthy",
        metadata={"extraction_method": "business_passage"},
    )
    quality = _content_quality_decision(
        subject,
        title=discovered.title,
        excerpt=body,
        published_at=day,
        observed_at=datetime(2026, 9, 13, tzinfo=UTC),
        policy=POLICY,
    )
    document, new = _raw_document(
        session, source, subject, {}, discovered, SimpleNamespace(id=uuid4()), quality
    )
    session.flush()
    event, created, quality = _candidate_event(
        session, subject, document, source, POLICY, new_document=new, actor=user
    )
    assert quality.eligible
    return event, document, created


def test_baseline_new_sources_correction_and_retry_keep_one_event(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path)
        baseline = session.scalar(
            select(Event).where(
                Event.company_id == company.id,
                Event.visibility_scope == "platform_shared",
                Event.published_on == DAY.date(),
            )
        )
        old_fields = list(baseline.facts)
        old_snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == company.id, CompanySnapshot.is_current.is_(True)
            )
        )
        body = "示例山海完成近一亿元A轮融资。"
        event, document, created = ingest(session, user, company, body)
        assert event.id == baseline.id and not created
        event2, _, created2 = ingest(session, user, company, body, "https://example.com/reprint")
        assert event2.id == event.id and not created2
        corrected, _, _ = ingest(
            session,
            user,
            company,
            "更正：示例山海完成近两亿元A轮融资。",
            "https://example.com/correction",
        )
        assert corrected.id == event.id and corrected.facts == old_fields
        count = session.scalar(select(func.count()).select_from(EventObservation))
        _, _, retry_created = ingest(session, user, company, body)
        assert (
            not retry_created
            and session.scalar(select(func.count()).select_from(EventObservation)) == count
        )
        assert document.payload["excerpt"] == body
        assert old_snapshot.last_checked_at is None
        session.commit()
        user = curated.enter(session)
        detail = get_company_detail(
            session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
        )
        shown = next(e for e in detail.events if e.id == baseline.id)
        assert shown.facts == old_fields and len(shown.financing_observations) == 3
        assert {o.kind for o in shown.financing_observations} == {
            "same_facts",
            "correction_candidate",
        }
        assert all(not o.confirmed for o in shown.financing_observations)
        evidence_id = shown.financing_observations[0].evidence_id
        withdrawn = session.get(EventEvidence, evidence_id)
        withdrawn_document = session.get(RawDocument, withdrawn.raw_document_id)
        retry_body = withdrawn_document.payload["excerpt"]
        retry_url = withdrawn_document.canonical_url
        withdrawn.display_allowed = False
        session.commit()
        user = curated.enter(session)
        detail = get_company_detail(
            session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
        )
        shown = next(e for e in detail.events if e.id == baseline.id)
        assert len(shown.financing_observations) == 2
        _, _, retry_created = ingest(session, user, company, retry_body, retry_url)
        assert not retry_created
        assert not session.get(EventEvidence, evidence_id).display_allowed
        assert session.scalar(select(func.count()).select_from(EventObservation)) == count


def test_migration_retains_financing_observations(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path)
        ingest(session, user, company, "示例山海完成B轮融资。")
        session.commit()
    with pytest.raises(RuntimeError, match="Retain financing observations"):
        command.downgrade(Config("alembic.ini"), "0032")
    with Session(database.owner) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventObservation)
                .where(EventObservation.schema_version == SCHEMA_VERSION)
            )
            == 1
        )


def test_new_round_scope_unknowns_and_non_admin_write_boundary(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path)
        brand, document, created = ingest(
            session, user, company, "示例山海完成B轮融资，金额及投资方未披露。"
        )
        assert created and brand.status == "candidate"
        legal, _, created = ingest(
            session,
            user,
            company,
            f"{company.legal_name}完成B轮融资。",
            "https://example.com/legal",
        )
        assert created and legal.id != brand.id
        source = _source(session)
        session.commit()
        set_request_context(session, BETA_USER_ID, BETA_TENANT_ID)
        other = session.get(User, BETA_USER_ID)
        with pytest.raises(AccessDeniedError):
            persist_financing_document(session, company, document, source, other)
        if database.postgres:
            assert (
                session.scalar(select(RawDocument.id).where(RawDocument.id == document.id)) is None
            )
            assert not list(
                session.scalars(
                    select(EventObservation).where(EventObservation.event_id == brand.id)
                )
            )


def test_alias_scope_and_collision_exclude_untrusted_names(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        _, company = initial(session, tmp_path, "identity_only")
        session.add_all(
            [
                CompanyAlias(
                    company_id=company.id,
                    alias="未确认别名",
                    normalized_alias="未确认别名",
                    alias_type="brand",
                    verification_status="unresolved",
                    visibility_scope="platform_shared",
                ),
                CompanyAlias(
                    company_id=company.id,
                    alias="私密品牌",
                    normalized_alias="私密品牌",
                    alias_type="brand",
                    visibility_scope="personal_private",
                    owner_user_id=ALPHA_USER_ID,
                ),
            ]
        )
        session.flush()
        assert load_subject(session, company).aliases == ("示例山海",)
        other = session.scalar(
            select(Company).where(Company.id != company.id, Company.visibility_scope == "public")
        )
        session.add(
            CompanyAlias(
                company_id=other.id,
                alias="示例山海",
                normalized_alias="示例山海",
                alias_type="brand",
                visibility_scope="platform_shared",
            )
        )
        session.flush()
        assert load_subject(session, company).aliases == ()


def test_worker_reads_late_alias_body_continues_after_captcha_and_keeps_baseline(
    database, tmp_path
):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path)
        before = session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.company_id == company.id, Event.status == "published")
        )
        request = create_refresh_request(session, user, PersonalEntitlementPolicy(), company.id)
        user = curated.enter(session)
        subject = load_subject(session, company)
        rows = [
            SearchResult(
                provider_record_id=str(i),
                title="示例山海完成B轮融资",
                snippet="融资披露",
                url=f"https://example.com/{path}",
                source_name="公开媒体",
                published_at="2026-06-01",
            )
            for i, path in enumerate(("blocked", "article"))
        ]
        primary = MockSearchProvider(
            "baidu", {_query_for(subject, terms): rows for _, terms in SEARCH_GROUPS}
        )
        fallback = MockSearchProvider("bocha")
        seen = []

        def factory(policy):
            def handler(http_request):
                seen.append(http_request.url.path)
                if http_request.url.path == "/robots.txt":
                    return httpx.Response(
                        200, text="User-agent: *\nAllow: /", headers={"content-type": "text/plain"}
                    )
                if http_request.url.path == "/blocked":
                    content = "<html><body>请完成验证码</body></html>"
                else:
                    content = (
                        '<meta property="article:published_time" content="2026-06-01"><main><p>'
                        + "行业介绍。" * 400
                        + "</p><p>示例山海完成近4亿元B轮融资。</p></main>"
                    )
                return httpx.Response(
                    200, text=content, headers={"content-type": "text/html; charset=utf-8"}
                )

            return TrustedSourceFetcher(
                policy,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                resolver=lambda *_: {"93.184.216.34"},
            )

        for _ in range(16):
            user = curated.enter(session)
            result = run_web_research_worker_once(
                session,
                user,
                {"baidu": primary, "bocha": fallback},
                POLICY,
                fetcher_factory=factory,
            )
            if result.status in {"completed", "failed", "budget_deferred"}:
                break
        assert result.status == "completed"
        user = curated.enter(session)
        job = session.get(
            CompanyResearchJob, session.get(PersonalCompanyRequest, request.id).research_job_id
        )
        assert job.coverage["query_strategy_version"] == "verified-business-names-v1"
        assert len(primary.calls) == 2 and not fallback.calls, job.coverage["documents"]
        assert "/blocked" in seen and "/article" in seen
        assert any(d.get("error_code") == "captcha_required" for d in job.coverage["documents"])
        assert job.coverage["stats"]["events_created"] == 1
        assert not list(session.scalars(select(IdentityResearchState)))
        assert (
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.company_id == company.id, Event.status == "published")
            )
            == before
        )
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == company.id, CompanySnapshot.is_current.is_(True)
            )
        )
        assert snapshot.last_checked_at is None
        detail = get_company_detail(
            session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
        )
        assert detail.events


def test_curator_admitted_request_starts_only_enabled_business_path(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, "identity_only")
        request = PersonalCompanyRequest(
            owner_user_id=ALPHA_USER_ID,
            request_type="inclusion",
            company_id=None,
            requested_name=company.legal_name,
            requested_credit_code=company.credit_code,
            target_key=f"company:{company.id}",
            status="pending",
            external_calls=0,
        )
        session.add(request)
        session.commit()
        user = curated.enter(session)
        request.company_id = company.id
        request.status = "in_review"
        request.last_error_code = "curator_identity_confirmed"
        request.external_calls = 16
        session.commit()
        user = curated.enter(session)
        assert (
            prepare_pending_research_requests(
                session, user, replace(POLICY, incremental_research_enabled=False)
            )
            == 0
        )
        user = curated.enter(session)
        from backend.app.web_research_service import inspect_web_research_queue

        preview = inspect_web_research_queue(session, user, POLICY)
        assert preview["pending_requests"] == 1 and preview["external_calls"] == 0
        assert preview["query_strategy_version"] == "verified-business-names-v1"
        assert prepare_pending_research_requests(session, user, POLICY) == 1
        user = curated.enter(session)
        assert request.research_job_id and request.external_calls == 16
        assert request.status == "research_queued"


def test_simultaneous_reprints_create_one_matter_and_append_observations(database, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    if not database.postgres:
        pytest.skip("concurrent worker transactions require PostgreSQL")
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        _, company = initial(session, tmp_path, "identity_only")
        company_id = company.id
        _source(session)
        session.commit()
    barrier = Barrier(2)

    def run(number):
        with Session(database.app, expire_on_commit=False) as session:
            actor = curated.enter(session)
            company = session.get(Company, company_id)
            barrier.wait(timeout=10)
            event, _, created = ingest(
                session,
                actor,
                company,
                "示例山海完成B轮融资。",
                f"https://example.com/reprint-{number}",
            )
            session.commit()
            return event.id, created

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, [1, 2]))
    assert len({item[0] for item in results}) == 1
    assert sum(item[1] for item in results) == 1
    with Session(database.app) as session:
        curated.enter(session)
        assert session.scalar(select(func.count()).select_from(EventObservation)) == 2


def test_recipient_mismatch_stays_internal_and_revoked_new_evidence_hides_fields(
    database, tmp_path
):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, "identity_only")
        source = _source(session)
        document = RawDocument(
            source_id=source.id,
            external_record_id="investor-fixture",
            visibility_scope="system_restricted",
            canonical_url="https://example.com/investor",
            title="示例投资报道",
            content_hash=digest("investor"),
            document_dedupe_key=digest("investor"),
            license_status="public",
            observed_at=DAY,
            published_at=DAY,
            published_on=DAY.date(),
            payload={
                "excerpt": "示例山海作为投资方，其他公司完成B轮融资。",
                "_source_verification": {"status": "healthy"},
            },
        )
        session.add(document)
        session.flush()
        # 质量初筛命中融资，但融资方规则不得退回通用标题事件而误归属。
        from backend.app.web_research_service import _ensure_entity_mention

        _ensure_entity_mention(session, document, company)
        session.flush()
        event, created, quality = _candidate_event(
            session,
            load_subject(session, company),
            document,
            source,
            POLICY,
            new_document=True,
            actor=user,
        )
        assert event is None and not created and not quality.eligible
        event, _, _ = ingest(session, user, company, "示例山海完成B轮融资。")
        evidence = session.scalar(select(EventEvidence).where(EventEvidence.event_id == event.id))
        evidence.display_allowed = False
        session.commit()
        user = curated.enter(session)
        detail = get_company_detail(
            session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
        )
        shown = next(e for e in detail.platform_unconfirmed_leads if e.id == event.id)
        assert not shown.facts and not shown.financing_observations
        assert shown.title == "融资线索证据暂不可用"
