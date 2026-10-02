"""融资情态及否定对象经正式身份、存储、API 和报告约束；只有虚构资料。"""

import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import RefreshPolicy, WebResearchPolicy
from backend.app.financing_semantics import statement
from backend.app.matter_validation import scoped_status, validate_field
from backend.app.models import EventObservation
from backend.app.research_matter_storage import persist_matters
from backend.app.research_matters import digest, extract_matters, validate_proposals
from backend.app.research_subject import load_subject
from backend.app.services import get_company_detail
from backend.app.source_fetcher import DiscoveredDocument
from backend.app.web_research_service import _content_quality_decision, _raw_document, _source
from tests.support import curated_import as curated
from tests.support.incremental_research import initial
from tests.support.m2_f_core_lifecycle import (
    DAY,
    capture,
    proposed,
    report_roundtrip,
)

database = curated.database

# 审查者原八例及实现前固定的有限变体；不是新的独立留出或业务分数。
CASES = [
    ("completed", "完成B轮融资。", "reported", "completion_reported"),
    ("planned_control", "计划完成B轮融资。", "planned", "fundraising_in_progress"),
    ("expected_close", "预计完成B轮融资。", "planned", "fundraising_in_progress"),
    (
        "future_expected_close",
        "预计于2026年11月完成B轮融资。",
        "planned",
        "fundraising_in_progress",
    ),
    ("funds_positive", "收到B轮融资增资款。", "reported", "funds_received"),
    ("funds_not_received", "尚未收到B轮融资增资款。", "planned", "fundraising_in_progress"),
    ("completion_not_done", "尚未完成B轮融资。", "planned", "fundraising_in_progress"),
    ("completion_no_done", "没有完成B轮融资。", "planned", "fundraising_in_progress"),
    ("expected_postfix", "B轮融资预计于下月完成。", "planned", "fundraising_in_progress"),
    ("conditional_close", "若条件满足将完成B轮融资。", "planned", "fundraising_in_progress"),
    ("funds_no_done", "没有收到B轮融资增资款。", "planned", "fundraising_in_progress"),
    ("funds_postfix", "B轮融资增资款尚未到账。", "planned", "fundraising_in_progress"),
    ("funds_expected", "B轮融资增资款预计下月到账。", "planned", "fundraising_in_progress"),
    (
        "unspent",
        "已完成B轮融资，尚未完成资金使用。",
        "reported",
        "completion_reported",
    ),
    (
        "factory_unfinished",
        "已完成B轮融资，尚未完成工厂投产。",
        "reported",
        "completion_reported",
    ),
    (
        "planned_use",
        "完成B轮融资，预计融资资金用于研发。",
        "reported",
        "completion_reported",
    ),
    (
        "unrelated_denial",
        "已完成B轮融资，公司否认产品停产的说法，称该消息不实。",
        "reported",
        "completion_reported",
    ),
    (
        "denial_after_but",
        "已完成B轮融资，但公司否认产品停产的报道。",
        "reported",
        "completion_reported",
    ),
    (
        "unrelated_plan",
        "计划完成工厂投产，但公司已完成B轮融资。",
        "reported",
        "completion_reported",
    ),
    (
        "real_denial",
        "否认此前已完成B轮融资的报道，称该报道不实。",
        "denied",
        "completion_claim_disputed",
    ),
    (
        "anaphoric_denial",
        "完成B轮融资，但公司否认该消息。",
        "denied",
        "completion_claim_disputed",
    ),
    ("unknown_control", "披露B轮融资安排，未说明是否完成。", "reported", "completion_unknown"),
]


def phase(matter):
    return next(i.split(":", 1)[1] for i in matter.issues if i.startswith("financing_stage:"))


def candidates(subject, body, route, status, fields=None):
    if route == "rules":
        return [m for m in extract_matters(subject, body) if m.subtype == "company_financing"], []
    proposed_status = status if route == "mock_correct" else "reported"
    return validate_proposals(
        subject,
        body,
        proposed(subject.legal_name, body.rstrip("。"), status=proposed_status, fields=fields),
        legacy_compat=False,
    )


@pytest.mark.parametrize("route", ["rules", "mock_correct", "mock_overstated"])
def test_financing_modal_family_from_formal_identity(database, tmp_path, route):
    curated.curator(database)
    failures, results = [], []
    with Session(database.app) as session:
        _, company = initial(session, tmp_path, mode="identity_only")
        subject = load_subject(session, company)
        for name, tail, status, expected_phase in CASES:
            body = company.legal_name + tail
            rows, rejected = candidates(subject, body, route, status)
            result = {
                "case": name,
                "body": body,
                "statement": statement(body),
                "scoped_status": scoped_status(body, "company_financing"),
                "matters": [m.payload() for m in rows],
                "rejected": rejected,
            }
            results.append(result)
            if statement(body) != (status, expected_phase):
                failures.append((name, "statement", result["statement"]))
            if scoped_status(body, "company_financing") != status:
                failures.append((name, "scoped_status", result["scoped_status"]))
            if len(rows) != 1 or rows[0].status != status or phase(rows[0]) != expected_phase:
                failures.append((name, "candidate", result["matters"]))
            if rows:
                assert rows[0].scope == "legal_entity"
        capture(database, "financing-family-" + route, results)
    assert not failures, failures


CORE = [
    ("expected_close", "预计完成3200万元B轮融资。", "planned", "fundraising_in_progress"),
    (
        "future_expected_close",
        "预计于2026年11月18日完成3200万元B轮融资。",
        "planned",
        "fundraising_in_progress",
    ),
    (
        "funds_not_received",
        "尚未收到3200万元B轮融资增资款。",
        "planned",
        "fundraising_in_progress",
    ),
    ("completion_no_done", "没有完成3200万元B轮融资。", "planned", "fundraising_in_progress"),
    (
        "unrelated_denial",
        "于2026年8月18日完成3200万元B轮融资，公司否认产品停产的说法，称该消息不实。",
        "reported",
        "completion_reported",
    ),
    (
        "planned_use",
        "于2026年8月18日完成3200万元B轮融资，预计融资资金用于研发。",
        "reported",
        "completion_reported",
    ),
    ("completed", "于2026年8月18日完成3200万元B轮融资。", "reported", "completion_reported"),
    ("funds_positive", "收到3200万元B轮融资增资款。", "reported", "funds_received"),
    (
        "negated_use",
        "于2026年8月18日完成3200万元B轮融资，公司否认本轮融资资金用于研发的说法。",
        "reported",
        "completion_reported",
    ),
    (
        "anaphoric_use",
        "于2026年8月18日完成3200万元B轮融资，报道声称融资资金将用于研发，公司否认该消息。",
        "reported",
        "completion_reported",
    ),
    (
        "bare_unfinished",
        "签署3200万元B轮融资投资协议，尚未完成。",
        "planned",
        "fundraising_in_progress",
    ),
]


# 近义情态在正式解析/校验层全覆盖；每个存储机制仅保留代表性完整链。
STORE_CASES = [
    c
    for c in CORE
    if c[0]
    in {"expected_close", "funds_not_received", "unrelated_denial", "completed", "funds_positive"}
]


@pytest.mark.parametrize("route", ["rules", "mock_correct", "mock_overstated"])
def test_financing_amount_and_date_variants_formal_validation(database, tmp_path, route):
    curated.curator(database)
    with Session(database.app) as session:
        _, company = initial(session, tmp_path, mode="identity_only")
        subject = load_subject(session, company)
        for name, tail, status, expected_phase in CORE:
            body = company.legal_name + tail
            fields = {
                "financing": {"value": "3200万元", "quote": body.rstrip("。"), "role": "financing"}
            }
            match = re.search(r"2026年\d+月\d+日", body)
            if match:
                fields["occurred"] = {
                    "value": match.group(),
                    "quote": body.rstrip("。"),
                    "role": "occurred",
                }
            rows, rejected = candidates(subject, body, route, status, fields=fields)
            assert len(rows) == 1, (name, rejected)
            payload = rows[0].payload()
            # 规则草稿和Mock草稿在正式存储前均经过同一逐字段校验；
            # 此处调用生产校验，完整存储副作用由代表性案例另行验证。
            payload["fields"] = {
                key: value
                for key, value in payload["fields"].items()
                if validate_field(
                    value["role"],
                    value["value"],
                    value["quote"],
                    rows[0].action,
                    rows[0].subtype,
                )[0]
            }
            assert payload["status"] == status, name
            assert "financing_stage:" + expected_phase in payload["issues"], name
            if status == "planned":
                assert not {"financing", "date", "occurred"} & payload["fields"].keys(), name
            else:
                assert payload["fields"]["financing"]["value"] == "3200万元", name


@pytest.mark.parametrize("route", ["rules", "mock_correct", "mock_overstated"])
@pytest.mark.parametrize(
    "name,tail,status,expected_phase", STORE_CASES, ids=[c[0] for c in STORE_CASES]
)
def test_financing_boundaries_formal_store_api_report(
    database, tmp_path, route, name, tail, status, expected_phase
):
    curated.curator(database)
    with Session(database.app) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        subject = load_subject(session, company)
        body = company.legal_name + tail
        fields = {
            "financing": {"value": "3200万元", "quote": body.rstrip("。"), "role": "financing"}
        }
        date = re.search(r"2026年\d+月\d+日", body)
        if date:
            fields["occurred"] = {
                "value": date.group(),
                "quote": body.rstrip("。"),
                "role": "occurred",
            }
        rows, rejected = candidates(subject, body, route, status, fields=fields)
        assert rows
        draft = [m.payload() for m in rows]
        discovered = DiscoveredDocument(
            "https://example.com/financing-boundary/" + name,
            "虚构融资边界",
            DAY,
            digest(body),
            body,
            200,
            None,
            None,
            "healthy",
        )
        policy = WebResearchPolicy(
            matter_processing_enabled=True, incremental_research_enabled=True
        )
        quality = _content_quality_decision(
            subject,
            title=discovered.title,
            excerpt=body,
            published_at=DAY,
            observed_at=DAY,
            policy=policy,
        )
        source = _source(session)
        document, _ = _raw_document(
            session,
            source,
            subject,
            {},
            discovered,
            SimpleNamespace(id=uuid4(), coverage={"matter_processing": True}),
            quality,
        )
        events, _, _ = persist_matters(session, company, document, source, user, rows, policy)
        assert len(events) == 1
        observation = session.scalar(
            select(EventObservation).where(EventObservation.event_id == events[0].id)
        )
        payload = observation.candidate_payload["matter"]
        company_id = company.id
        session.commit()
        user = curated.enter(session)
        detail = get_company_detail(
            session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
        ).model_dump(mode="json")
    # 先实际完成读取/报告再断言，红例能定位实际受影响的层。
    report = report_roundtrip(database, company_id, expected="融资")
    capture(
        database,
        "financing-boundary-" + name + "-" + route,
        {
            "body": body,
            "draft": draft,
            "rejected": rejected,
            "persisted": payload,
            "detail": detail,
            "report": report,
        },
    )
    assert payload["status"] == status
    assert "financing_stage:" + expected_phase in payload["issues"]
    if status == "planned":
        assert "occurred" not in payload["fields"] and "date" not in payload["fields"]
        assert "financing" not in payload["fields"]
        assert "融资计划/进行中，未完成" in report["markdown"]
    else:
        assert payload["fields"]["financing"]["value"] == "3200万元"
        label = "来源称收到增资款" if expected_phase == "funds_received" else "来源称融资完成"
        assert label in report["markdown"]
