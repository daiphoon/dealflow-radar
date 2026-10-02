"""从正式身份导入、正文到事项读取的离线回归；不提升全局品牌身份。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.config import RefreshPolicy, Settings
from backend.app.demo import ALPHA_USER_ID, BETA_USER_ID
from backend.app.main import create_app
from backend.app.matter_comparison import compare_matters
from backend.app.matter_identity import context_for
from backend.app.models import (
    Company,
    CompanyAlias,
    CompanyResearchJob,
    Event,
    EventEvidence,
    EventFact,
    EventFactSupport,
    EventObservation,
    PersonalEventViewReceipt,
    RefreshJob,
    UsageLedger,
)
from backend.app.research_matters import extract_matters, validate_proposals
from backend.app.research_subject import load_subject
from backend.app.services import get_company_detail
from backend.app.web_research_service import _source
from tests.support import curated_import as curated
from tests.support.incremental_research import initial
from tests.support.research_matter_storage import ingest

database = curated.database


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda *_: pytest.fail("Unexpected real HTTP")
    )


def proposal(name, quote):
    return {
        "matters": [
            {
                "subject": name,
                "subtype": "company_financing",
                "subject_role": "fundraiser",
                "status": "reported",
                "action_quote": quote,
                "fields": {},
            }
        ]
    }


@pytest.mark.parametrize("route", ["rules", "mock_proposal"])
def test_existing_brand_keeps_explicit_legal_declaration(database, tmp_path, route):
    curated.curator(database)
    with Session(database.app) as session:
        _, company = initial(session, tmp_path, mode="identity_only")
        subject = load_subject(session, company)
        alias = session.scalar(select(CompanyAlias).where(CompanyAlias.company_id == company.id))
        assert alias.alias_type == "brand" and alias.verification_status == "verified"
        quote = alias.alias + "完成数千万元A2轮融资"
        body = f"{company.legal_name}（以下简称“{alias.alias}”）。\n{quote}。"
        if route == "rules":
            rows = extract_matters(subject, body)
        else:
            rows, rejected = validate_proposals(
                subject, body, proposal(alias.alias, quote), legacy_compat=False
            )
            assert not rejected
        assert len(rows) == 1 and rows[0].scope == "legal_entity"
        assert alias.alias_type == "brand" and subject.legal_aliases == ()


def test_brand_declaration_does_not_create_a_legal_local_alias(database, tmp_path):
    curated.curator(database)
    with Session(database.app) as session:
        _, company = initial(session, tmp_path, mode="identity_only")
        subject = load_subject(session, company)
        quote = "外部简称完成A2轮融资"
        body = f"{subject.aliases[0]}（以下简称“外部简称”）。\n{quote}。"
        rows, rejected = validate_proposals(
            subject, body, proposal("外部简称", quote), legacy_compat=False
        )
        assert not rows and rejected


def test_new_local_name_is_not_registered_or_reused_in_other_documents(database, tmp_path):
    curated.curator(database)
    with Session(database.app) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        before = list(session.scalars(select(CompanyAlias.alias)))
        quote = "临时代称完成数千万元A2轮融资"
        body = company.legal_name + "（以下简称“临时代称”）。\n" + quote + "。"
        subject = load_subject(session, company)
        rules = extract_matters(subject, body)
        proposals, rejected = validate_proposals(
            subject, body, proposal("临时代称", quote), legacy_compat=False
        )
        assert len(rules) == len(proposals) == 1 and not rejected
        assert rules[0].scope == proposals[0].scope == "legal_entity"
        event, _, _, _ = ingest(session, user, company, body, "https://example.com/local")
        assert event is not None
        assert list(session.scalars(select(CompanyAlias.alias))) == before
        assert not extract_matters(subject, quote)


@pytest.mark.parametrize("kind", ["round", "transaction", "stage", "product", "counterparty"])
def test_body_negative_boundaries_are_preserved_in_storage(database, tmp_path, kind):
    curated.curator(database)
    with Session(database.app) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        name = company.legal_name
        tails = {
            "round": ("完成数千万元A轮融资", "完成数千万元A2轮融资"),
            "transaction": ("完成A2轮融资，交易编号TX-101", "完成A2轮融资，交易编号TX-202"),
            "stage": ("拟完成A2轮融资，交易编号TX-101", "完成A2轮融资，交易编号TX-101"),
            "product": ("发布RAP产品，项目编号PR-101", "发布RKN产品，项目编号PR-202"),
            "counterparty": (
                "与示例甲航空签署租赁协议，项目编号CT-101",
                "与示例乙航空签署租赁协议，项目编号CT-202",
            ),
        }[kind]
        bodies = [name + text + "。" for text in tails]
        subject = load_subject(session, company)
        candidates = [extract_matters(subject, body)[0] for body in bodies]
        compared = compare_matters(
            *candidates,
            subject=subject,
            left_context=context_for(subject, bodies[0]),
            right_context=context_for(subject, bodies[1]),
        )
        assert not compared.same_matter
        if kind == "stage":
            assert compared.decision == "related_stage"
        ids = [
            ingest(session, user, company, body, f"https://example.com/{i}")[0].id
            for i, body in enumerate(bodies)
        ]
        assert ids[0] != ids[1]
        if kind == "stage":
            observation = session.scalar(
                select(EventObservation).where(EventObservation.event_id == ids[1])
            )
            assert any(
                r["type"] == "related_stage" for r in observation.candidate_payload["relations"]
            )


def test_cluster_missing_id_cannot_bridge_two_explicit_transactions(database, tmp_path):
    curated.curator(database)
    with Session(database.app) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        shared = (
            company.legal_name
            + "完成数千万元A2轮融资，本轮由示例投资机构领投并由示例长期资本持续追加支持，"
            "募集资金用于研发和扩大生产规模"
        )
        texts = [shared + "，交易编号TX-101。", shared + "。", shared + "，交易编号TX-202。"]
        ids = [
            ingest(session, user, company, body, f"https://example.com/cluster/{i}")[0].id
            for i, body in enumerate(texts)
        ]
        assert ids[0] == ids[1] and ids[2] != ids[0]
        last = session.scalar(select(EventObservation).where(EventObservation.event_id == ids[2]))
        assert any(
            r["type"] == "cluster_identity_conflict" for r in last.candidate_payload["relations"]
        )


def test_postgres_same_occurrence_concurrency_and_withdrawn_support(database, tmp_path):
    if not database.postgres:
        pytest.skip("concurrent transaction behavior requires PostgreSQL")
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        _, company = initial(session, tmp_path, mode="identity_only")
        company_id = company.id
        name = company.legal_name
        _source(session)
        session.commit()
    barrier = Barrier(2)
    body = name + "完成数千万元A2轮融资，交易编号F-CONCURRENT。"

    def run(i):
        with Session(database.app) as session:
            user = curated.enter(session)
            company = session.get(Company, company_id)
            barrier.wait(timeout=10)
            event, _, _, _ = ingest(
                session, user, company, body, f"https://example.com/concurrent/{i}"
            )
            event_id = event.id
            session.commit()
            return event_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(run, (0, 1)))
    assert ids[0] == ids[1]
    with Session(database.app) as session:
        user = curated.enter(session)
        company = session.get(Company, company_id)
        old = session.get(Event, ids[0])
        observations_before = {
            r.id: r.candidate_payload for r in session.scalars(select(EventObservation))
        }
        for evidence in session.scalars(
            select(EventEvidence).where(EventEvidence.event_id == old.id)
        ):
            evidence.display_allowed = False
        session.commit()
        user = curated.enter(session)
        new = ingest(session, user, company, body, "https://example.com/concurrent/new")[0]
        assert new.id != old.id
        detail = get_company_detail(
            session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
        )
        hidden = next(r for r in detail.platform_unconfirmed_leads if r.id == old.id)
        assert hidden.matter_observations == [] and hidden.facts == []
        assert {
            r.id: r.candidate_payload
            for r in session.scalars(
                select(EventObservation).where(EventObservation.event_id == old.id)
            )
        } == observations_before


def content_counts(session):
    return tuple(
        session.scalar(select(func.count()).select_from(model))
        for model in (Event, EventObservation, EventEvidence, EventFact, EventFactSupport)
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_body_identity_storage_retry_and_real_report_read(database, tmp_path, reverse):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        baseline_counts = content_counts(session)
        alias = load_subject(session, company).aliases[0]
        claim = "完成数千万元A2轮融资。本轮融资由示例投资机构领投，老股东示例长线资本持续追加。"
        texts = [
            alias
            + "完成数千万元A2轮融资\n1月19日，据示例长线资本消息，"
            + company.legal_name
            + claim,
            alias
            + "完成数千万元A2轮融资\n1月19日，据示例长线资本消息，"
            + company.legal_name
            + f"（以下简称“{alias}”）"
            + claim,
        ]
        order = [1, 0] if reverse else [0, 1]
        ids = []
        for i in order:
            event, _, _, _ = ingest(session, user, company, texts[i], f"https://example.com/{i}")
            assert event is not None
            ids.append(event.id)
        assert len(set(ids)) == 1
        event = session.get(Event, ids[0])
        assert event.occurred_at is None and event.status == "candidate"
        before = content_counts(session)
        assert tuple(a - b for a, b in zip(before[:3], baseline_counts[:3], strict=True)) == (
            1,
            2,
            2,
        )
        for i in order:
            ingest(session, user, company, texts[i], f"https://example.com/{i}")
        assert content_counts(session) == before
        observations = list(session.scalars(select(EventObservation)))
        assert all(r.candidate_payload["identity_context"]["excerpt_sha256"] for r in observations)
        session.commit()
        user = curated.enter(session)
        detail = get_company_detail(
            session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
        )
        assert [r.id for r in detail.platform_unconfirmed_leads] == [event.id]
        company_id = company.id
        side_effects = tuple(
            session.scalar(select(func.count()).select_from(m))
            for m in (RefreshJob, CompanyResearchJob, PersonalEventViewReceipt, UsageLedger)
        )
    app = create_app(
        Settings(
            database_url=database.app.url.render_as_string(hide_password=False),
            app_mode="demo",
            external_calls_enabled=False,
            paid_api_calls_enabled=False,
            auto_refresh_enabled=False,
        )
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/me/companies/{company_id}/reports",
                headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
                json={"idempotency_key": "f" * 64},
            )
            assert response.status_code == 200, response.text
            report = response.json()
            assert "A2" in report["markdown"] and "未确认" in report["markdown"]
            path = "/api/v1/me/reports/" + report["id"]
            read = client.get(path, headers={"X-Demo-User-Id": str(ALPHA_USER_ID)})
            assert read.status_code == 200 and read.json()["markdown"] == report["markdown"]
            assert (
                client.get(path, headers={"X-Demo-User-Id": str(BETA_USER_ID)}).status_code == 404
            )
        with Session(database.owner) as session:
            after = tuple(
                session.scalar(select(func.count()).select_from(m))
                for m in (RefreshJob, CompanyResearchJob, PersonalEventViewReceipt, UsageLedger)
            )
            assert after == side_effects
    finally:
        app.state.engine.dispose()
