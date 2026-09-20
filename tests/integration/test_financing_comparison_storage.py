from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.config import RefreshPolicy
from backend.app.database import set_request_context
from backend.app.demo import BETA_TENANT_ID, BETA_USER_ID
from backend.app.financing_events import SCHEMA_VERSION
from backend.app.models import Company, Event, EventEvidence, EventObservation, RawDocument, User
from backend.app.services import get_company_detail
from tests.curated_fixtures import CREDIT_CODE
from tests.integration import test_curated_import as curated
from tests.integration import test_incremental_research as base

database = base.database
offline = base.offline
BODY = "示例山海近日完成近2亿元人民币新一轮融资。本轮融资由示例 甲资本领投。"


def setup(session, tmp_path, *, duplicate=False, mode="initial_data", occurred="未单独确认"):
    def change(data):
        row = data["事件明细"][0]
        row["重要事件"] = "近2亿元B轮融资"
        row["事件摘要"] = "示例甲资本领投，示例乙资本跟投。"
        row["实际发生日期"] = occurred
        if duplicate:
            data["事件明细"].append(
                {**row, "事件ID": "E003", "交易/进程组": "G003", "重要事件": "近2亿元C轮融资"}
            )

    curated.apply(session, curated.loaded(tmp_path, change=change, mode=mode))
    user = curated.enter(session)
    company = session.scalar(select(Company).where(Company.credit_code == CREDIT_CODE))
    return user, company


def test_partial_sources_attach_to_curated_event_and_keep_original_facts(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = setup(session, tmp_path)
        baseline = session.scalar(
            select(Event).where(
                Event.company_id == company.id,
                Event.title == "近2亿元B轮融资",
                Event.visibility_scope == "platform_shared",
            )
        )
        original = (baseline.facts, baseline.summary, baseline.published_on, baseline.observed_at)
        for offset in (0, 1):
            event, document, created = base.ingest(
                session,
                user,
                company,
                BODY,
                url=f"https://example.com/report-{offset}",
                day=base.DAY + timedelta(days=offset),
            )
            assert not created and event.id == baseline.id
            item = session.scalar(
                select(EventObservation).where(EventObservation.raw_document_id == document.id)
            )
            assert item.observation_kind == "same_facts"
            payload = item.candidate_payload
            assert payload["candidate"]["round"] is None
            assert payload["candidate"]["issues"] == ["round_unknown"]
            assert payload["comparison"]["relation"] == "compatible_evidence"
            again, _, new = base.ingest(
                session,
                user,
                company,
                BODY,
                url=f"https://example.com/report-{offset}",
                day=base.DAY + timedelta(days=offset),
            )
            assert not new and again.id == baseline.id
        assert (
            baseline.facts,
            baseline.summary,
            baseline.published_on,
            baseline.observed_at,
        ) == original
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventObservation)
                .where(EventObservation.schema_version == SCHEMA_VERSION)
            )
            == 2
        )
        company_id, baseline_id = company.id, baseline.id
        session.commit()
        set_request_context(session, BETA_USER_ID, BETA_TENANT_ID)
        ordinary = session.get(User, BETA_USER_ID)
        detail = get_company_detail(
            session, ordinary, company_id, RefreshPolicy(), auto_refresh_enabled=False
        )
        shown = next(e for e in detail.events if e.id == baseline_id)
        assert len(shown.financing_observations) == 2
        assert all(
            not item.confirmed and item.fields.round is None
            for item in shown.financing_observations
        )
        assert not any(
            e.event_type == "financing_cap_table" for e in detail.platform_unconfirmed_leads
        )
        if database.postgres:
            assert not list(
                session.scalars(select(RawDocument).where(RawDocument.id == document.id))
            )
        session.commit()
        curated.enter(session)
        evidence = session.get(EventEvidence, shown.financing_observations[0].evidence_id)
        evidence.display_allowed = False
        session.commit()
        user = curated.enter(session)
        detail = get_company_detail(
            session, user, company_id, RefreshPolicy(), auto_refresh_enabled=False
        )
        assert (
            len(next(e for e in detail.events if e.id == baseline_id).financing_observations) == 1
        )


@pytest.mark.parametrize(
    "body,offset",
    [
        (BODY.replace("新一轮", "C轮"), 0),
        (BODY.replace("近2亿元人民币", "近3亿元人民币"), 0),
        (BODY.replace("近2亿元人民币", "近2亿美元"), 0),
        (BODY.replace("示例 甲资本", "另一资本"), 0),
        (BODY, 2),
    ],
)
def test_distinct_financing_remains_a_separate_candidate(database, tmp_path, body, offset):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = setup(session, tmp_path)
        event, _, created = base.ingest(
            session, user, company, body, day=base.DAY + timedelta(days=offset)
        )
        assert created and event.status == "candidate"
        assert event.fingerprint_version == "financing-v2"


def test_two_compatible_matters_are_not_selected_arbitrarily(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = setup(session, tmp_path, duplicate=True)
        event, doc, created = base.ingest(session, user, company, BODY)
        assert created and event.status == "candidate"
        obs = session.scalar(
            select(EventObservation).where(EventObservation.raw_document_id == doc.id)
        )
        assert "ambiguous_existing_matter" in obs.candidate_payload["candidate"]["issues"]


def test_unknown_round_cannot_bridge_two_known_rounds_or_walk_across_dates(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = setup(session, tmp_path, mode="identity_only")
        first, _, created = base.ingest(session, user, company, BODY)
        assert created
        second, _, created = base.ingest(
            session,
            user,
            company,
            BODY.replace("新一轮", "B轮"),
            url="https://example.com/second",
            day=base.DAY + timedelta(days=1),
        )
        assert not created and second.id == first.id
        different_round, _, created = base.ingest(
            session, user, company, BODY.replace("新一轮", "C轮"), url="https://example.com/third"
        )
        assert created and different_round.id != first.id
        later, _, created = base.ingest(
            session,
            user,
            company,
            BODY,
            url="https://example.com/later",
            day=base.DAY + timedelta(days=2),
        )
        assert created and later.id not in {first.id, different_round.id}


def test_concurrent_sources_join_one_existing_matter(database, tmp_path):
    if not database.postgres:
        pytest.skip("PostgreSQL transaction lock concurrency")
    curated.curator(database)
    with Session(database.app) as session:
        _, company = setup(session, tmp_path)
        company_id = company.id
        # 并发验收的是已有研究来源的事项写入，先完成来源初始化。
        base._source(session)
        session.commit()
    barrier = Barrier(2)

    def add_source(index):
        with Session(database.app) as session:
            user = curated.enter(session)
            company = session.get(Company, company_id)
            barrier.wait(timeout=15)
            event, _, created = base.ingest(
                session, user, company, BODY, url=f"https://example.com/concurrent-{index}"
            )
            event_id = event.id
            session.commit()
            return event_id, created

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(add_source, (0, 1)))
    assert results[0] == results[1] and results[0][1] is False
    with Session(database.owner) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventObservation)
                .where(EventObservation.event_id == results[0][0])
            )
            == 2
        )


@pytest.mark.parametrize("withdrawn", [False, True])
def test_same_day_unlinked_material_has_its_own_fingerprint(database, tmp_path, withdrawn):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = setup(session, tmp_path, mode="identity_only")
        body = BODY.replace("新一轮", "B轮")
        first, _, _ = base.ingest(session, user, company, body)
        if withdrawn:
            evidence = session.scalar(
                select(EventEvidence).where(EventEvidence.event_id == first.id)
            )
            evidence.display_allowed = False
            session.flush()
        else:
            body = body.replace("近2亿元人民币", "近3亿元人民币").replace("示例 甲资本", "另一资本")
        second, _, created = base.ingest(
            session, user, company, body, url="https://example.com/distinct"
        )
        assert created and first.id != second.id
        assert first.event_fingerprint != second.event_fingerprint
        again, _, created = base.ingest(
            session, user, company, body, url="https://example.com/distinct"
        )
        assert not created and again.id == second.id
        session.commit()
        user = curated.enter(session)
        detail = get_company_detail(
            session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
        )
        if withdrawn:
            old = next(e for e in detail.platform_unconfirmed_leads if e.id == first.id)
            assert old.title == "融资线索证据暂不可用" and not old.financing_observations


def test_legacy_financing_event_can_accept_new_compatible_evidence(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = setup(session, tmp_path, mode="identity_only")
        first, _, _ = base.ingest(session, user, company, BODY)
        first.fingerprint_version = "financing-v1"
        session.flush()
        second, _, created = base.ingest(
            session, user, company, BODY, url="https://example.com/next"
        )
        assert not created and second.id == first.id
        assert first.fingerprint_version == "financing-v1"


def test_explicit_curated_occurrence_conflict_blocks_partial_link(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = setup(session, tmp_path, occurred="2026-05-01")
        body = "2026年6月1日，" + BODY.replace("近日", "")
        event, document, created = base.ingest(session, user, company, body)
        assert created and event.status == "candidate"
        observation = session.scalar(
            select(EventObservation).where(EventObservation.raw_document_id == document.id)
        )
        assert observation.candidate_payload["candidate"]["occurred_on"] == "2026-06-01"
