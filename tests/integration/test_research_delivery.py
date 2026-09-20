from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.company_refresh import schedule_stale_company
from backend.app.config import PersonalEntitlementPolicy, RefreshPolicy, Settings, WebResearchPolicy
from backend.app.curated_batch import apply_curated_batch, preview_curated_batch
from backend.app.curated_workbook import CuratedWorkbookProvider
from backend.app.database import set_request_context
from backend.app.demo import BETA_TENANT_ID, BETA_USER_ID
from backend.app.models import (
    CompanyResearchJob,
    CompanySnapshot,
    Event,
    PersonalCompanyRequest,
    UsageLedger,
    User,
    utc_now,
)
from backend.app.personal_features import create_refresh_request
from backend.app.research_plan import TOPICS, VERSION
from backend.app.research_subject import load_subject, short_business_query
from backend.app.services import AccessDeniedError, ImportConflictError, get_company_detail
from backend.app.source_fetcher import TrustedSourceFetcher
from backend.app.web_research_service import (
    prepare_pending_research_requests,
    run_web_research_worker_once,
)
from backend.app.web_search import MockSearchProvider, SearchResult
from tests.curated_fixtures import workbook_file
from tests.integration import test_curated_import as curated
from tests.integration.test_incremental_research import initial

database = curated.database
POLICY = WebResearchPolicy(incremental_research_enabled=True, topic_planning_enabled=True)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda *_: pytest.fail("real network")
    )


def test_batch_import_all_rows_notes_retry_and_access_boundary(database, tmp_path):
    curated.curator(database)

    def change(data):
        company = deepcopy(data["公司总表"][0])
        company.update(
            {
                "公司ID": "C002",
                "工商全称": "示例批次另一企业有限公司",
                "统一社会信用代码": "913100009999999980",
                "项目简称": "示例另一企业",
            }
        )
        # 使用算法构造虚构代码的校验位，避免真实主体进入测试。
        alphabet = "0123456789ABCDEFGHJKLMNPQRTUWXY"
        weights = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)
        prefix = company["统一社会信用代码"][:17]
        company["统一社会信用代码"] = (
            prefix
            + alphabet[
                (31 - sum(alphabet.index(c) * weight for c, weight in zip(prefix, weights)) % 31)
                % 31
            ]
        )
        data["公司总表"].append(company)
        row = data["事件明细"][0]
        data["事件明细"].extend(
            [
                {
                    **deepcopy(row),
                    "事件ID": f"E{i}",
                    "公司ID": "C002",
                    "工商全称": company["工商全称"],
                    "统一社会信用代码": company["统一社会信用代码"],
                    "事件类别": category,
                }
                for i, category in enumerate(TOPICS, 3)
            ]
        )
        data["待核事项"].append(
            {
                "线索ID": "N1",
                "公司ID": "多家",
                "待核主题": "全表说明",
                "目前信息": "保留人工整理口径，不是新的事件",
            }
        )

    path = workbook_file(tmp_path / "batch.xlsx", change=change)
    loaded = CuratedWorkbookProvider(
        path, dataset_key="batch", mode="initial_data", allowed_root=tmp_path
    ).load()
    assert not loaded.issues and len(loaded.records) == 11
    with Session(database.app, expire_on_commit=False) as session:
        user = curated.enter(session)
        kwargs = {"confirmed_at": utc_now(), "reason": "负责人确认整批虚构测试资料"}
        preview = preview_curated_batch(session, user, loaded, **kwargs)
        assert preview["record_count"] == 11 and len(preview["notes"]) == 1
        with pytest.raises(ImportConflictError, match="preview changed"):
            apply_curated_batch(session, user, loaded, preview_hash="wrong", **kwargs)
        result = apply_curated_batch(
            session, user, loaded, preview_hash=preview["preview_hash"], **kwargs
        )
        assert result["status"] == "completed"
        user = curated.enter(session)
        count = session.scalar(select(func.count()).select_from(Event))
        preview = preview_curated_batch(session, user, loaded, **kwargs)
        second = apply_curated_batch(
            session, user, loaded, preview_hash=preview["preview_hash"], **kwargs
        )
        user = curated.enter(session)
        assert all(r["status"] == "duplicate" for r in second["companies"])
        assert session.scalar(select(func.count()).select_from(Event)) == count
        note = session.scalar(
            select(UsageLedger).where(UsageLedger.operation == "curated_batch_notes")
        )
        assert note.metrics["notes"][0]["kind"] == "dataset_note"
        assert session.scalar(select(func.count()).select_from(CompanyResearchJob)) == 0
    with Session(database.app) as session:
        set_request_context(session, BETA_USER_ID, BETA_TENANT_ID)
        with pytest.raises(AccessDeniedError):
            preview_curated_batch(session, session.get(User, BETA_USER_ID), loaded, **kwargs)


def test_stale_visit_queues_once_fresh_and_disabled_visits_do_not(database, tmp_path):
    curated.curator(database)
    settings = Settings(
        database_url="sqlite://",
        app_mode="demo",
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=True,
    )
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path)
        detail = get_company_detail(
            session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
        )
        detail.data_as_of = utc_now().date()
        assert schedule_stale_company(session, user, detail, settings)["status"] == "not_due"
        detail.data_as_of = utc_now().date() - timedelta(days=30)
        assert (
            schedule_stale_company(
                session, user, detail, replace(settings, auto_refresh_enabled=False)
            )["status"]
            == "disabled"
        )
        assert session.scalar(select(func.count()).select_from(PersonalCompanyRequest)) == 0
        first = schedule_stale_company(session, user, detail, settings)
        assert first["status"] == "queued"
        user = curated.enter(session)
        assert (
            schedule_stale_company(session, user, detail, settings)["request_id"]
            == first["request_id"]
        )
        user = curated.enter(session)
        assert session.scalar(select(func.count()).select_from(PersonalCompanyRequest)) == 1
        prepare_pending_research_requests(session, user, POLICY)
        user = curated.enter(session)
        assert schedule_stale_company(session, user, detail, settings)["status"] == "refreshing"
        assert session.scalar(select(func.count()).select_from(CompanyResearchJob)) == 1
        job = session.scalar(select(CompanyResearchJob))
        job.status = "failed"
        session.commit()
        user = curated.enter(session)
        assert schedule_stale_company(session, user, detail, settings)["status"] == "cooldown"
        current = get_company_detail(
            session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
        )
        assert len(current.events) == len(detail.events) == 2


def test_topic_worker_reads_financing_and_ipo_before_high_rank_homepage(database, tmp_path):
    curated.curator(database)
    today = utc_now().date().isoformat()
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path)
        create_refresh_request(session, user, PersonalEntitlementPolicy(), company.id)
        user = curated.enter(session)
        subject = load_subject(session, company)
        titles = ["示例山海官方网站", "示例山海启动IPO辅导备案", "示例山海完成近4亿元B轮融资"]
        paths = ["/", "/ipo", "/financing"]
        rows = [
            SearchResult(
                provider_record_id=str(i),
                title=title,
                snippet=title,
                url=f"https://example.com{path}",
                source_name="公开媒体",
                published_at=today,
            )
            for i, (path, title) in enumerate(zip(paths, titles))
        ]
        primary = MockSearchProvider(
            "baidu",
            {short_business_query(subject, topic): rows for topic in list(TOPICS.values())[:2]},
        )
        seen = []

        def factory(policy):
            def handler(request):
                seen.append(request.url.path)
                if request.url.path == "/robots.txt":
                    return httpx.Response(
                        200, text="User-agent: *\nAllow: /", headers={"content-type": "text/plain"}
                    )
                body = {
                    "/financing": "示例山海完成近4亿元B轮融资。本轮融资由示例投资方领投。",
                    "/ipo": "示例山海启动IPO辅导备案，辅导进度已披露。",
                }.get(request.url.path, "示例山海欢迎访问官网。")
                return httpx.Response(
                    200,
                    text=(
                        f'<meta property="article:published_time" content="{today}">'
                        f"<main><p>{body}</p></main>"
                    ),
                    headers={"content-type": "text/html; charset=utf-8"},
                )

            return TrustedSourceFetcher(
                policy,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                resolver=lambda *_: {"93.184.216.34"},
            )

        for _ in range(20):
            user = curated.enter(session)
            result = run_web_research_worker_once(
                session,
                user,
                {"baidu": primary, "bocha": MockSearchProvider("bocha")},
                POLICY,
                fetcher_factory=factory,
            )
            if result.status == "completed":
                break
        assert result.status == "completed"
        assert [path for path in seen if path != "/robots.txt"][:2] == ["/financing", "/ipo"]
        user = curated.enter(session)
        job = session.get(CompanyResearchJob, result.job_id)
        assert job.coverage["query_strategy_version"] == VERSION
        assert job.coverage["stats"]["fetch_calls"] <= 8
        assert job.coverage["stats"]["search_calls"] <= 4
        eligible = [
            d
            for d in job.coverage["documents"]
            if d.get("quality_gate", {}).get("status") == "eligible"
        ]
        assert {d["coverage_category"] for d in eligible} >= {
            "financing_cap_table",
            "exit_liquidity",
        }
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == company.id, CompanySnapshot.is_current.is_(True)
            )
        )
        assert snapshot.last_checked_at is None


def test_batch_preserves_existing_dataset_identity(database, tmp_path):
    curated.curator(database)
    data = curated.loaded(tmp_path)
    with Session(database.app, expire_on_commit=False) as session:
        curated.apply(session, data)
        user = curated.enter(session)
        count = session.scalar(select(func.count()).select_from(Event))
        renamed = replace(data, dataset_key="new-batch")
        kwargs = {
            "confirmed_at": utc_now(),
            "reason": "整批接收沿用原公司资料标识",
            "dataset_keys": {data.companies[0].key: data.dataset_key},
        }
        preview = preview_curated_batch(session, user, renamed, **kwargs)
        result = apply_curated_batch(
            session, user, renamed, preview_hash=preview["preview_hash"], **kwargs
        )
        user = curated.enter(session)
        assert result["companies"][0]["status"] == "duplicate"
        assert session.scalar(select(func.count()).select_from(Event)) == count
        with pytest.raises(ImportConflictError, match="dataset mapping"):
            preview_curated_batch(
                session, user, renamed, **{**kwargs, "dataset_keys": {"C999": "x"}}
            )


def test_ordinary_user_stale_visit_reuses_company_job_without_reading_others_requests(
    database, tmp_path
):
    curated.curator(database)
    settings = Settings(
        database_url="sqlite://",
        app_mode="demo",
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=True,
    )
    with Session(database.app, expire_on_commit=False) as session:
        admin, company = initial(session, tmp_path)
        company_id = company.id
        create_refresh_request(session, admin, PersonalEntitlementPolicy(), company_id)
        admin = curated.enter(session)
        prepare_pending_research_requests(session, admin, POLICY)
    with Session(database.app, expire_on_commit=False) as session:
        set_request_context(session, BETA_USER_ID, BETA_TENANT_ID)
        ordinary = session.get(User, BETA_USER_ID)
        detail = get_company_detail(
            session, ordinary, company_id, RefreshPolicy(), auto_refresh_enabled=False
        )
        detail.data_as_of = utc_now().date() - timedelta(days=30)
        result = schedule_stale_company(session, ordinary, detail, settings)
        assert result["status"] in {"queued", "refreshing"}
        if database.postgres:
            # 未关联前不能读其他人的任务，但可以申请并由 Worker 合并。
            assert result["status"] == "queued"
    with Session(database.app, expire_on_commit=False) as session:
        admin = curated.enter(session)
        prepare_pending_research_requests(session, admin, POLICY)
        admin = curated.enter(session)
        assert session.scalar(select(func.count()).select_from(CompanyResearchJob)) == 1
        requests = session.scalars(select(PersonalCompanyRequest)).all()
        assert len({r.research_job_id for r in requests}) == 1
        job = session.scalar(select(CompanyResearchJob))
        job.status = "completed"
        for request in requests:
            request.status = "completed"
            request.created_at = utc_now() - timedelta(days=2)
        session.commit()
    with Session(database.app, expire_on_commit=False) as session:
        set_request_context(session, BETA_USER_ID, BETA_TENANT_ID)
        ordinary = session.get(User, BETA_USER_ID)
        create_refresh_request(session, ordinary, PersonalEntitlementPolicy(), company_id)
    with Session(database.app, expire_on_commit=False) as session:
        admin = curated.enter(session)
        prepare_pending_research_requests(session, admin, POLICY)
        admin = curated.enter(session)
        assert session.scalar(select(func.count()).select_from(CompanyResearchJob)) == 1
        assert all(r.status == "completed" for r in session.scalars(select(PersonalCompanyRequest)))


def test_concurrent_stale_visits_create_one_request(database, tmp_path):
    if not database.postgres:
        pytest.skip("concurrent locking is validated on PostgreSQL")
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path)
        company_id = company.id
    barrier = Barrier(2)
    settings = Settings(
        database_url="sqlite://",
        app_mode="demo",
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=True,
    )

    def visit():
        with Session(database.app, expire_on_commit=False) as session:
            user = curated.enter(session)
            detail = get_company_detail(
                session, user, company_id, RefreshPolicy(), auto_refresh_enabled=False
            )
            detail.data_as_of = utc_now().date() - timedelta(days=30)
            barrier.wait(timeout=10)
            return schedule_stale_company(session, user, detail, settings)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: visit(), range(2)))
    assert results[0]["request_id"] == results[1]["request_id"]
    with Session(database.app) as session:
        curated.enter(session)
        assert session.scalar(select(func.count()).select_from(PersonalCompanyRequest)) == 1


def test_partial_selection_then_full_file_keeps_old_events_and_adds_missing_rows(
    database, tmp_path
):
    from backend.app.curated_workbook import digest
    from backend.app.models import ResearchImport

    curated.curator(database)
    full = curated.loaded(tmp_path)
    partial = replace(full, records=full.records[:1])
    with Session(database.app, expire_on_commit=False) as session:
        curated.apply(session, partial)
        user = curated.enter(session)
        old_ids = set(session.scalars(select(Event.id)))
        # 旧版回执只包含公司选择键，不能让它吞掉后续新增行。
        receipt = session.scalar(select(ResearchImport))
        receipt.selection_key = digest([full.dataset_key, full.mode, full.company_keys])
        session.commit()
        user = curated.enter(session)
        kwargs = {"confirmed_at": utc_now(), "reason": "补齐原表其余事项"}
        preview = preview_curated_batch(session, user, full, **kwargs)
        apply_curated_batch(session, user, full, preview_hash=preview["preview_hash"], **kwargs)
        user = curated.enter(session)
        ids = set(session.scalars(select(Event.id)))
        assert old_ids < ids
        assert len(ids) == len(full.records) + sum(r.confirmed for r in full.records)
        preview = preview_curated_batch(session, user, full, **kwargs)
        again = apply_curated_batch(
            session, user, full, preview_hash=preview["preview_hash"], **kwargs
        )
        user = curated.enter(session)
        assert again["companies"][0]["status"] == "duplicate"
        assert set(session.scalars(select(Event.id))) == ids
