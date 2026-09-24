import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.app.config import RefreshPolicy
from backend.app.curated_import import apply_curated_import, preview_curated_import
from backend.app.curated_workbook import CuratedWorkbookProvider
from backend.app.database import set_request_context
from backend.app.demo import ALPHA_TENANT_ID, ALPHA_USER_ID, BETA_TENANT_ID, BETA_USER_ID
from backend.app.models import (
    Company,
    CompanyAlias,
    CompanyResearchJob,
    CompanySnapshot,
    Event,
    EventSharingDecision,
    IdentityResearchState,
    PersonalCompanyRequest,
    RawDocument,
    ResearchImport,
    Role,
    UsageLedger,
    User,
    UserRoleAssignment,
    utc_now,
)
from backend.app.personal_features import _report_markdown
from backend.app.services import AccessDeniedError, ImportConflictError, get_company_detail
from tests.curated_fixtures import COMPANY_KEY, COMPANY_NAME, CREDIT_CODE, workbook_file
from tests.integration import test_tender_storage

database = test_tender_storage.database


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import httpx

    def denied(*args, **kwargs):
        pytest.fail("E4.1 must not make external HTTP calls")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)


def curator(database):
    with Session(database.owner) as session:
        role = session.scalar(select(Role.id).where(Role.code == "platform_admin"))
        session.add(UserRoleAssignment(user_id=ALPHA_USER_ID, role_id=role))
        session.commit()


def loaded(tmp_path, mode="initial_data", change=None):
    path = workbook_file(tmp_path / "fixture.xlsx", change=change)
    return CuratedWorkbookProvider(
        path, dataset_key="fixture", mode=mode, company_keys=(COMPANY_KEY,), allowed_root=tmp_path
    ).load()


def enter(session):
    set_request_context(session, ALPHA_USER_ID, ALPHA_TENANT_ID)
    return session.get(User, ALPHA_USER_ID)


def apply(session, data, **kwargs):
    user = enter(session)
    options = {
        "confirmed_at": utc_now() - timedelta(seconds=1),
        "reason": "负责人已确认虚构测试资料",
        **kwargs,
    }
    plan = preview_curated_import(session, user, data, **options)
    result = apply_curated_import(session, user, data, preview_hash=plan["preview_hash"], **options)
    return result


def test_curated_preview_admission_initial_content_and_repeat(database, tmp_path):
    curator(database)
    data = loaded(tmp_path)
    with Session(database.app, expire_on_commit=False) as session:
        user = enter(session)
        before = session.scalar(select(func.count()).select_from(Company))
        plan = preview_curated_import(
            session, user, data, confirmed_at=utc_now(), reason="已复核虚构资料"
        )
        assert plan["database_writes"] == plan["external_calls"] == 0
        assert session.scalar(select(func.count()).select_from(Company)) == before
        assert plan["companies"][0]["action"] == "create"
        result = apply(session, data)
        assert result["shared_events_created"] == 2
        assert result["unconfirmed_records"] == 1
        user = enter(session)
        company = session.scalar(select(Company).where(Company.credit_code == CREDIT_CODE))
        assert company.identity_verification_basis == "curator_confirmed"
        assert company.official_website is None
        assert (
            session.scalar(select(CompanyAlias).where(CompanyAlias.company_id == company.id)).alias
            == "示例山海"
        )
        snapshot = session.scalar(
            select(CompanySnapshot).where(CompanySnapshot.company_id == company.id)
        )
        assert snapshot.last_checked_at is None and snapshot.freshness_status == "unknown"
        assert snapshot.data_as_of.isoformat() == "2026-09-01"
        own_detail = get_company_detail(
            session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
        )
        assert len(own_detail.unconfirmed_leads) == 1
        assert "待核的股东变化" == own_detail.unconfirmed_leads[0].title
        usage = list(
            session.scalars(
                select(UsageLedger).where(UsageLedger.provider == "curated_workbook_import")
            )
        )
        assert len(usage) == 1 and usage[0].external_calls == 0
        assert session.scalar(select(func.count()).select_from(CompanyResearchJob)) == 0
        assert apply(session, data)["status"] == "duplicate"
        user = enter(session)
        assert session.scalar(select(func.count()).select_from(ResearchImport)) == 1
        company_id = company.id
    with Session(database.app) as session:
        set_request_context(session, BETA_USER_ID, BETA_TENANT_ID)
        detail = get_company_detail(
            session,
            session.get(User, BETA_USER_ID),
            company_id,
            RefreshPolicy(),
            auto_refresh_enabled=False,
        )
        assert len(detail.events) == 2
        assert all(event.curated_versions for event in detail.events)
        assert all(event.evidence for event in detail.events)
        assert all(
            e.url_health_status == "unchecked" for event in detail.events for e in event.evidence
        )
        serialized = detail.model_dump_json()
        assert "fixture.xlsx" not in serialized and str(ALPHA_USER_ID) not in serialized
        assert all(e.occurred_at is None for e in detail.events)
        assert all(e.risk_severity == "unknown" for e in detail.events)
        assert all(f.support_status == "supported" for e in detail.events for f in e.fact_ledger)
        company = session.get(Company, company_id)
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == company_id, CompanySnapshot.is_current.is_(True)
            )
        )
        report = _report_markdown(company, snapshot, detail.events, utc_now(), RefreshPolicy())
        assert "2024-04（月；公司回溯月份）" in report
        assert "人工整理、负责人已复核" in report and "尚未联网检查" in report
        assert "可信度：0%" not in report and "无显著风险" not in report


def test_identity_only_then_full_import_keeps_research_answers_out_of_first_batch(
    database, tmp_path
):
    curator(database)
    data = loaded(tmp_path, "identity_only")
    with Session(database.app, expire_on_commit=False) as session:
        result = apply(session, data)
        assert result["private_events_created"] == result["shared_events_created"] == 0
        user = enter(session)
        documents = list(
            session.scalars(select(RawDocument).where(RawDocument.owner_user_id == user.id))
        )
        assert len(documents) == 1
        assert "一亿元" not in json.dumps(documents[0].payload, ensure_ascii=False)
        result = apply(session, loaded(tmp_path))
        assert result["shared_events_created"] == 2
        enter(session)
        assert session.scalar(select(func.count()).select_from(ResearchImport)) == 2


def test_non_curator_and_conflicting_name_cannot_admit(database, tmp_path):
    data = loaded(tmp_path, "identity_only")
    with Session(database.app) as session:
        user = enter(session)
        with pytest.raises(AccessDeniedError):
            preview_curated_import(
                session, user, data, confirmed_at=utc_now(), reason="虚构测试资料确认"
            )
    curator(database)
    with Session(database.owner) as session:
        session.add(
            Company(
                credit_code=CREDIT_CODE,
                legal_name="示例不同公司",
                tenant_id=None,
                visibility_scope="public",
                identity_status="verified",
            )
        )
        session.commit()
    with Session(database.app) as session:
        user = enter(session)
        with pytest.raises(ImportConflictError, match="targeted review"):
            apply(session, data)
        enter(session)
        assert not session.scalar(select(func.count()).select_from(ResearchImport))


def test_request_identity_confirmation_preserves_old_failure_and_cost(database, tmp_path):
    curator(database)
    data = loaded(tmp_path, "identity_only")
    with Session(database.owner) as session:
        request = PersonalCompanyRequest(
            owner_user_id=BETA_USER_ID,
            request_type="inclusion",
            requested_name=COMPANY_NAME,
            requested_credit_code=CREDIT_CODE,
            target_key="curated-request-fixture",
            status="in_review",
            last_error_code="identity_evidence_missing",
            external_calls=16,
        )
        session.add(request)
        session.flush()
        session.add(
            IdentityResearchState(
                request_id=request.id, progress={"search_calls": 5, "fetch_requests": 11}
            )
        )
        request_id = request.id
        session.commit()
    with Session(database.app) as session:
        apply(session, data, request_id=request_id)
        enter(session)
        request = session.get(PersonalCompanyRequest, request_id)
        assert request.company_id and request.research_job_id is None
        assert (
            request.status == "in_review"
            and request.last_error_code == "curator_identity_confirmed"
        )
        assert request.external_calls == 16
        assert session.get(IdentityResearchState, request_id).progress == {
            "search_calls": 5,
            "fetch_requests": 11,
        }
        usage = session.scalar(
            select(UsageLedger).where(UsageLedger.provider == "curated_workbook_import")
        )
        assert usage.metrics["request_before"]["last_error_code"] == "identity_evidence_missing"
        from backend.app.personal_features import _request_out

        assert "负责人已确认主体" in _request_out(session, request).status_message
        request.status = "cancelled"
        with session.no_autoflush:
            message = _request_out(session, request).status_message
        assert "取消" in message and "待安排" not in message


def detail_for_curator(session, company_id):
    user = enter(session)
    return get_company_detail(
        session, user, company_id, RefreshPolicy(), auto_refresh_enabled=False
    )


def test_revisions_preserve_old_evidence_and_same_record_retries(database, tmp_path):
    curator(database)
    original = loaded(tmp_path)
    old_review = utc_now() - timedelta(days=2)
    with Session(database.app, expire_on_commit=False) as session:
        first = apply(session, original, confirmed_at=old_review)
        from uuid import UUID

        company_id = UUID(first["company_id"])
        enter(session)
        before_documents = {
            d.id: (d.content_hash, d.payload)
            for d in session.scalars(
                select(RawDocument).where(RawDocument.owner_user_id == ALPHA_USER_ID)
            )
        }
        revision = loaded(
            tmp_path,
            change=lambda data: data["事件明细"][0].update(
                {"事件摘要": "示例品牌融资金额更正为近八千万元；品牌口径不变。"}
            ),
        )
        result = apply(session, revision, confirmed_at=old_review + timedelta(hours=1))
        assert result["corrected_records"] == 1 and result["shared_events_created"] == 0
        detail = detail_for_curator(session, company_id)
        event = next(e for e in detail.events if "A轮" in e.title)
        assert len(event.curated_versions) == 2
        assert sum(v.is_current for v in event.curated_versions) == 1
        assert "八千万元" in event.summary
        assert "一亿元" not in "".join(e.excerpt for e in event.evidence)
        for doc_id, before in before_documents.items():
            document = session.get(RawDocument, doc_id)
            assert (document.content_hash, document.payload) == before
        assert session.scalar(select(func.count()).select_from(EventSharingDecision)) == 3
        # 文件其他页的变化不能让同一公司记录再次生成事件、证据或审批。
        source_only = loaded(
            tmp_path,
            change=lambda data: (
                data["事件明细"][0].update(
                    {"事件摘要": "示例品牌融资金额更正为近八千万元；品牌口径不变。"}
                ),
                data["公司总表"][0].update({"地区（资料口径）": "示例补充地区"}),
            ),
        )
        repeated = apply(session, source_only)
        assert repeated["private_events_created"] == repeated["shared_events_created"] == 0
        assert repeated["documents_created"] == 1  # 仅追加不同的主体资料
        enter(session)
        assert session.scalar(select(func.count()).select_from(EventSharingDecision)) == 3
        # 新确认的更正可以回到早期事实，仍追加新版本；不是复用旧审批。
        restored = loaded(
            tmp_path,
            change=lambda data: data["事件明细"][0].update(
                {"核验说明": "再次核对后恢复原金额，保留更正历史"}
            ),
        )
        assert apply(session, restored)["corrected_records"] == 1
        event = next(e for e in detail_for_curator(session, company_id).events if "A轮" in e.title)
        assert "一亿元" in event.summary and len(event.curated_versions) == 3
        assert sum(v.is_current for v in event.curated_versions) == 1


def test_new_pending_revision_keeps_published_facts_and_exposes_only_pending_record(
    database, tmp_path
):
    from uuid import UUID

    curator(database)
    with Session(database.app) as session:
        result = apply(session, loaded(tmp_path))
        pending = loaded(
            tmp_path,
            change=lambda data: data["事件明细"][0].update(
                {
                    "内容支持状态": "待核",
                    "事件摘要": "新的融资金额线索，尚待复核。",
                }
            ),
        )
        apply(session, pending)
        detail = detail_for_curator(session, UUID(result["company_id"]))
        assert len(detail.unconfirmed_leads) == 2
        published = next(e for e in detail.events if "A轮" in e.title)
        assert "一亿元" in published.summary
        candidate = next(e for e in detail.unconfirmed_leads if "A轮" in e.title)
        assert candidate.summary == "新的融资金额线索，尚待复核。"
        assert sum(v.is_current for v in candidate.curated_versions) == 1


def test_stale_preview_and_failure_roll_back_the_whole_import(database, tmp_path, monkeypatch):
    curator(database)
    data = loaded(tmp_path)
    with Session(database.app) as session:
        user = enter(session)
        options = {"confirmed_at": utc_now(), "reason": "负责人复核虚构资料"}
        plan = preview_curated_import(session, user, data, **options)
        with pytest.raises(ImportConflictError, match="preview changed"):
            apply_curated_import(session, user, data, preview_hash="0" * 64, **options)
        enter(session)
        assert session.scalar(select(func.count()).select_from(ResearchImport)) == 0
        import backend.app.curated_import as importer

        original = importer.promote_private_event
        calls = []

        def fail_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("injected transaction failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(importer, "promote_private_event", fail_second)
        user = enter(session)
        with pytest.raises(RuntimeError, match="injected"):
            apply_curated_import(session, user, data, preview_hash=plan["preview_hash"], **options)
        enter(session)
        assert not session.scalar(select(Company).where(Company.credit_code == CREDIT_CODE))
        assert session.scalar(select(func.count()).select_from(ResearchImport)) == 0
        assert session.scalar(select(func.count()).select_from(EventSharingDecision)) == 0


def test_partial_conflict_does_not_overwrite_or_stop_valid_rows(database, tmp_path):
    curator(database)
    with Session(database.app) as session:
        first = apply(session, loaded(tmp_path))
        revised = loaded(
            tmp_path,
            change=lambda data: data["事件明细"][0].update({"事件摘要": "一条迟到的旧资料更正"}),
        )
        result = apply(session, revised, confirmed_at=utc_now() - timedelta(days=10))
        assert result["unresolved_records"] == 1 and result["corrected_records"] == 0
        from uuid import UUID

        event = next(
            e
            for e in detail_for_curator(session, UUID(first["company_id"])).events
            if "A轮" in e.title
        )
        assert "一亿元" in event.summary
        invalid = loaded(
            tmp_path, change=lambda data: data["事件明细"][0].update({"工商全称": "示例另一主体"})
        )
        result = apply(session, invalid)
        assert result["status"] == "completed_with_unresolved"
        assert result["confirmed_records"] == 1 and result["unresolved_records"] == 1


def test_curator_rls_is_not_available_to_ordinary_or_other_tenant_users(database, tmp_path):
    if not database.postgres:
        pytest.skip("database enforcement requires non-owner PostgreSQL")
    curator(database)
    with Session(database.app) as session:
        result = apply(session, loaded(tmp_path))
    from uuid import UUID

    company_id = UUID(result["company_id"])
    for user_id, tenant_id in ((BETA_USER_ID, BETA_TENANT_ID), (BETA_USER_ID, ALPHA_TENANT_ID)):
        with Session(database.app) as session:
            set_request_context(session, user_id, tenant_id)
            assert session.scalar(select(func.count()).select_from(ResearchImport)) == 0
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(RawDocument)
                    .where(RawDocument.owner_user_id == ALPHA_USER_ID)
                )
                == 0
            )
            assert (
                session.execute(
                    update(Company)
                    .where(Company.id == company_id)
                    .values(legal_name="不应覆盖的名称")
                ).rowcount
                == 0
            )
            with pytest.raises(DBAPIError):
                session.add(
                    Company(
                        legal_name="伪造负责人准入",
                        credit_code="91310000123456789X",
                        visibility_scope="public",
                        identity_status="verified",
                        identity_verification_basis="curator_confirmed",
                    )
                )
                session.flush()
            session.rollback()


def test_concurrent_apply_is_atomic_and_idempotent(database, tmp_path):
    if not database.postgres:
        pytest.skip("concurrent transaction test requires PostgreSQL")
    curator(database)
    data = loaded(tmp_path)
    options = {"confirmed_at": utc_now(), "reason": "负责人确认的并发虚构导入"}
    with Session(database.app) as session:
        user = enter(session)
        plan = preview_curated_import(session, user, data, **options)
    barrier = Barrier(2)

    def run():
        with Session(database.app) as session:
            user = enter(session)
            barrier.wait(timeout=10)
            return apply_curated_import(
                session, user, data, preview_hash=plan["preview_hash"], **options
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sorted(r["status"] for r in results) == ["completed", "duplicate"]
    with Session(database.owner) as session:
        assert session.scalar(select(func.count()).select_from(ResearchImport)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.visibility_scope == "platform_shared")
            )
            == 2
        )


def test_migration_round_trip_and_no_destructive_downgrade(database, tmp_path):
    command.downgrade(Config("alembic.ini"), "0031")
    command.upgrade(Config("alembic.ini"), "head")
    command.check(Config("alembic.ini"))
    curator(database)
    with Session(database.app) as session:
        apply(session, loaded(tmp_path))
    with pytest.raises(RuntimeError, match="Event observations exist; Retain matter observations"):
        # 0035 提前阻止跨版本降级；不能先移除当前模型字段再等待旧迁移报错。
        command.downgrade(Config("alembic.ini"), "0031")
    with Session(database.owner) as session:
        assert session.scalar(select(func.count()).select_from(ResearchImport)) == 1
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0036"


def test_cancelled_request_cannot_be_reopened_by_curated_admission(database, tmp_path):
    curator(database)
    with Session(database.owner) as session:
        request = PersonalCompanyRequest(
            owner_user_id=BETA_USER_ID,
            request_type="inclusion",
            requested_name=COMPANY_NAME,
            requested_credit_code=CREDIT_CODE,
            target_key="cancelled-curated-fixture",
            status="cancelled",
            cancelled_at=utc_now(),
            last_error_code="identity_evidence_missing",
            external_calls=16,
        )
        session.add(request)
        session.flush()
        request_id = request.id
        session.commit()
    with Session(database.app) as session:
        with pytest.raises(ImportConflictError, match="not eligible"):
            apply(session, loaded(tmp_path, "identity_only"), request_id=request_id)
        enter(session)
        assert session.scalar(select(func.count()).select_from(ResearchImport)) == 0
        request = session.get(PersonalCompanyRequest, request_id)
        assert request.status == "cancelled" and request.external_calls == 16


def test_alias_collision_does_not_bind_the_wrong_company(database, tmp_path):
    curator(database)
    with Session(database.owner) as session:
        existing = session.scalar(select(Company).limit(1))
        session.add(
            CompanyAlias(
                company_id=existing.id,
                visibility_scope="platform_shared",
                alias="示例山海",
                normalized_alias="示例山海",
                alias_type="brand",
                verification_status="verified",
            )
        )
        session.commit()
    with Session(database.app) as session:
        result = apply(session, loaded(tmp_path, "identity_only"))
        assert result["unresolved_records"] == 1
        enter(session)
        company = session.scalar(select(Company).where(Company.credit_code == CREDIT_CODE))
        assert (
            session.scalar(
                select(func.count())
                .select_from(CompanyAlias)
                .where(CompanyAlias.company_id == company.id)
            )
            == 0
        )
