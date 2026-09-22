from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.config import InvestorAnalysisPolicy, Settings
from backend.app.database import build_session_factory
from backend.app.demo import ALPHA_USER_ID, BETA_USER_ID, NO_ACCESS_USER_ID
from backend.app.investor_analysis import run_investor_analysis_worker_once
from backend.app.investor_analysis_schema import InvestorChangeAnalysisOutput, LLMProviderResult
from backend.app.main import create_app
from backend.app.models import (
    Event,
    EventEvidence,
    EventFact,
    EventObservation,
    EventSharingDecision,
    InvestorChangeAnalysis,
    RawDocument,
    Role,
    Source,
    UserRoleAssignment,
)
from backend.app.providers import ManualResearchImportProvider
from backend.app.services import import_manual_research
from tests.integration.test_tender_storage import (
    CASES,
    COMPANY_ID,
    _actor,
    _candidate,
    _persist,
    _source_document,
)
from tests.integration.test_tender_storage import (
    database as database,
)


class MockTenderAnalysis:
    code, model = "mock_tender_analysis", "fixture"

    def __init__(self):
        self.calls, self.requests = 0, []

    def analyze_investor_change(self, request):
        self.calls += 1
        self.requests.append(request)
        return LLMProviderResult(
            analysis=InvestorChangeAnalysisOutput(
                schema_version="investor-change-analysis-v1",
                headline="中标公告已核实",
                before_value=request.before_value,
                after_value=request.after_value,
                what_changed="公告所述字段已核实。",
                why_it_matters="可以继续关注项目后续履约情况。",
                potential_impacts=[],
                uncertainties=["中标不代表已确认收入。"],
                evidence_ids=[request.evidence[0].evidence_id],
                confidence=0.8,
                follow_up_items=[],
                impact_direction="uncertain",
                disclaimer="模型辅助解读，不构成投资建议。",
            ),
            external_calls=0,
            input_tokens=120,
            output_tokens=80,
            estimated_cost=Decimal("0"),
        )


@pytest.fixture
def client(database):
    with Session(database.owner) as session:
        role = session.scalar(select(Role).where(Role.code == "platform_admin"))
        session.add(UserRoleAssignment(user_id=ALPHA_USER_ID, role_id=role.id))
        session.commit()
    app = create_app(
        Settings(
            database_url=database.app.url.render_as_string(hide_password=False),
            app_mode="demo",
            external_calls_enabled=False,
            paid_api_calls_enabled=False,
            auto_refresh_enabled=False,
            review_workbench_enabled=True,
            tender_events_enabled=True,
        )
    )
    with TestClient(app) as result:
        yield result
    app.state.engine.dispose()


def candidate(database, case_id="award"):
    value = _candidate(database, _source_document(database, case_id, owner_id=ALPHA_USER_ID))
    return _persist(database, value, ALPHA_USER_ID)


def promote(client, database, result):
    with Session(database.owner) as session:
        observation = session.get(EventObservation, result.observation_id)
        ids = [item["evidence_id"] for item in observation.candidate_payload["field_links"]]
    return client.post(
        f"/api/v1/events/{result.event_id}/sharing/promotion",
        headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
        json={
            "title": "示例公司中标公告（虚构验收）",
            "summary": "所选版本仅说明公告披露的中标信息，不代表已履约或收入。",
            "reason": "虚构样本人工核实流程验收",
            "evidence_ids": ids,
            "observation_id": str(result.observation_id),
            "confirm_evidence_support": True,
            "confirm_unchecked_links": True,
        },
    )


def detail(client, user_id=NO_ACCESS_USER_ID):
    response = client.get(
        f"/api/v1/companies/{COMPANY_ID}", headers={"X-Demo-User-Id": str(user_id)}
    )
    assert response.status_code == 200, response.text
    return response.json()


def report(client):
    response = client.post(
        f"/api/v1/me/companies/{COMPANY_ID}/reports",
        headers={"X-Demo-User-Id": str(NO_ACCESS_USER_ID)},
        json={"idempotency_key": hashlib.sha256(uuid4().bytes).hexdigest()},
    )
    assert response.status_code == 200, response.text
    return response.json()


def worker(database, provider):
    with build_session_factory(database.app)() as session:
        return run_investor_analysis_worker_once(
            session, _actor(session, ALPHA_USER_ID), provider, InvestorAnalysisPolicy()
        )


def test_reviewed_tender_details_versions_reports_and_zero_query_calls(database, client):
    original = candidate(database)
    assert detail(client)["events"] == []
    own = detail(client, ALPHA_USER_ID)["unconfirmed_leads"][0]
    assert own["display_kind"] == "unconfirmed" and own["occurred_on"] == "2026-08-03"
    assert own["tender_observations"][0]["confirmed"] is False
    response = promote(client, database, original)
    assert response.status_code == 200, response.text
    shared_id = UUID(response.json()["shared_event_id"])
    provider = MockTenderAnalysis()
    assert worker(database, provider).outcome == "completed"
    for _ in range(3):
        current = detail(client)["events"][0]
        assert current["display_kind"] == "confirmed_change"
        assert current["occurred_at"] is None and current["occurred_on"] == "2026-08-03"
        assert current["analysis"] is not None
        assert str(original.observation_id) not in json.dumps(current)
        assert str(original.event_id) not in json.dumps(current)
    before_report = report(client)
    assert "12345000" in before_report["markdown"]
    assert current["fact_version"] in before_report["markdown"]
    assert provider.calls == 1
    reprint = candidate(database, "reprint")
    assert promote(client, database, reprint).status_code == 200
    assert worker(database, provider).status == "idle"
    assert provider.calls == 1
    correction = candidate(database, "correction")
    assert detail(client)["events"][0]["fact_version"] == current["fact_version"]
    assert promote(client, database, correction).status_code == 200
    revised = detail(client)["events"][0]
    assert revised["fact_version"] != current["fact_version"]
    assert revised["analysis"] is None
    assert {item["value"] for item in revised["facts"] if item["name"] == "中标金额"} == {
        "12000000"
    }
    assert len({item["fact_version"] for item in revised["tender_observations"]}) == 2
    worker(database, provider)
    assert provider.calls == 2
    assert "12345000" in provider.requests[-1].before_value
    assert "12000000" in provider.requests[-1].after_value
    quotes = "\n".join(item.excerpt for item in provider.requests[-1].evidence)
    assert "1,234.50 万元" in quotes and "1,200.00 万元" in quotes
    # 旧版本缓存即使较晚写回，也不能盖住已经存在的当前版本解读。
    with Session(database.owner) as session:
        cached = session.scalar(
            select(InvestorChangeAnalysis).order_by(InvestorChangeAnalysis.created_at)
        )
        values = {
            column.name: getattr(cached, column.name)
            for column in InvestorChangeAnalysis.__table__.columns
        }
        values.update(
            id=uuid4(),
            input_hash="e" * 64,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(InvestorChangeAnalysis(**values))
        session.commit()
    assert "12000000" in detail(client)["events"][0]["analysis"]["after_value"]
    after_report = report(client)
    assert "12000000" in after_report["markdown"] and "12345000" not in after_report["markdown"]
    old = client.get(
        f"/api/v1/me/reports/{before_report['id']}",
        headers={"X-Demo-User-Id": str(NO_ACCESS_USER_ID)},
    )
    assert old.status_code == 200 and old.json()["markdown"] == before_report["markdown"]
    with Session(database.owner) as session:
        active_evidence_id = UUID(
            next(x for x in revised["tender_observations"] if x["is_current"])["evidence_ids"][0]
        )
        session.get(EventEvidence, active_evidence_id).display_allowed = False
        session.commit()
    withdrawn = detail(client)
    assert withdrawn["events"] == []
    assert withdrawn["platform_unconfirmed_leads"][0]["analysis"] is None
    assert withdrawn["platform_unconfirmed_leads"][0]["facts"] == []
    assert all(
        item["occurred_on"] is None and item["facts"] == []
        for item in withdrawn["platform_unconfirmed_leads"][0]["tender_observations"]
        if not item["evidence_available"]
    )
    assert "12000000" not in report(client)["markdown"]
    assert worker(database, provider).status == "idle" and provider.calls == 2
    with Session(database.owner) as session:
        assert session.get(Event, shared_id).status == "published"
        assert session.scalar(select(func.count()).select_from(EventObservation)) == 3


def test_tender_switch_scope_and_incomplete_promotion_gates(database, client):
    value = candidate(database, "unknown_date")
    assert promote(client, database, value).status_code == 422
    own = detail(client, ALPHA_USER_ID)["unconfirmed_leads"][0]
    assert (
        own["occurred_on"] is None and own["tender_observations"][0]["date_precision"] == "unknown"
    )
    other = detail(client, BETA_USER_ID)
    assert other["unconfirmed_leads"] == [] and other["events"] == []
    original = candidate(database, "other_project")
    client.app.state.settings = replace(client.app.state.settings, tender_events_enabled=False)
    assert promote(client, database, original).status_code == 422
    assert detail(client)["events"] == []
    with Session(database.owner) as session:
        assert session.scalar(select(func.count()).select_from(InvestorChangeAnalysis)) == 0


def test_existing_manual_import_is_the_opt_in_offline_entry(database, tmp_path, client):
    from backend.app.demo import ALPHA_TENANT_ID

    records = []
    for name in ("award", "reprint"):
        raw = CASES[name]["document"]
        records.append(
            {
                "external_record_id": name,
                "company_identity_evidence": CASES[name]["subject"] | {},
                "source_code": f"tender_{name}",
                "source_name": "虚构中标来源",
                "canonical_url": raw["canonical_url"],
                "source_published_at": raw["published_at"],
                "title": raw["title"],
                "evidence_excerpt": raw["body"],
                "event_type": "contract_commercial",
                "event_subtype": "tender_notice",
                "direction": "unknown",
                "materiality_score": 70,
                "risk_severity": "low",
                "confidence_score": 0.8,
                "source_quality": "B",
                "facts": [{"name": "公告类型", "value": "中标公告", "unit": None}],
                "uncertainties": [],
                "requires_human_review": True,
            }
        )
        records[-1]["company_identity_evidence"] = {
            "legal_name": CASES[name]["subject"]["legal_name"],
            "credit_code": CASES[name]["subject"]["credit_code"],
        }
    payload = {
        "schema_version": "1.0",
        "batch_id": "tender-fixture",
        "queried_at": datetime.now(UTC).isoformat(),
        "research_tool": "manual",
        "agent_name": "虚构验收",
        "original_query": "离线中标样本",
        "target_company_hint": CASES["award"]["subject"]["legal_name"],
        "license_status": "permission_confirmed",
        "records": records,
    }
    path = tmp_path / "tender.json"
    path.write_text(json.dumps(payload, ensure_ascii=False))
    with Session(database.app) as session:
        actor = _actor(session, ALPHA_USER_ID)
        result = import_manual_research(
            session,
            actor,
            ManualResearchImportProvider(path, allowed_root=tmp_path),
            tender_events_enabled=True,
        )
        assert (
            result.events_created == 1
            and result.documents_created == 2
            and result.external_calls == 0
        )
    with Session(database.owner) as session:
        event = session.scalar(select(Event))
        assert (
            event.owner_tenant_id == ALPHA_TENANT_ID
            and event.visibility_scope == "organization_private"
        )
        assert event.fingerprint_version == "tender-v1" and event.status == "candidate"
        assert session.scalar(select(func.count()).select_from(RawDocument)) == 2
        assert session.scalar(select(func.count()).select_from(EventObservation)) == 2

    with Session(database.owner) as session:
        observation = session.scalar(select(EventObservation).order_by(EventObservation.created_at))
        imported = SimpleNamespace(event_id=observation.event_id, observation_id=observation.id)
    assert promote(client, database, imported).status_code == 200
    assert detail(client)["events"][0]["display_kind"] == "confirmed_change"


def view(client):
    response = client.post(
        f"/api/v1/me/companies/{COMPANY_ID}/view",
        headers={"X-Demo-User-Id": str(NO_ACCESS_USER_ID)},
    )
    assert response.status_code == 200, response.text
    return response.json()["new_events"]


def test_correction_notifies_once_reprint_does_not_and_retraction_removes_current(database, client):
    initial = candidate(database)
    response = promote(client, database, initial)
    assert response.status_code == 200
    shared_id = response.json()["shared_event_id"]
    assert [event["id"] for event in view(client)] == [shared_id]
    assert view(client) == []
    assert promote(client, database, candidate(database, "reprint")).status_code == 200
    assert view(client) == []
    assert promote(client, database, candidate(database, "correction")).status_code == 200
    assert [event["id"] for event in view(client)] == [shared_id]
    assert view(client) == []
    old_report = report(client)
    response = client.post(
        f"/api/v1/shared-events/{shared_id}/retraction",
        headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
        json={"reason": "虚构验收撤回此共享事实"},
    )
    assert response.status_code == 200, response.text
    assert view(client) == [] and detail(client)["events"] == []
    assert "12000000" not in report(client)["markdown"]
    saved = client.get(
        f"/api/v1/me/reports/{old_report['id']}", headers={"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}
    )
    assert saved.json()["markdown"] == old_report["markdown"]


@pytest.mark.parametrize("tamper", ["quote", "field", "source_license", "document_license"])
def test_review_revalidates_field_evidence_and_current_permissions(database, client, tamper):
    result = candidate(database)
    with Session(database.owner) as session:
        observation = session.get(EventObservation, result.observation_id)
        link = observation.candidate_payload["field_links"][0]
        if tamper == "quote":
            evidence = session.get(EventEvidence, UUID(link["evidence_id"]))
            evidence.evidence_excerpt = "与原文不符的摘录"
            evidence.span_hash = hashlib.sha256(evidence.evidence_excerpt.encode()).hexdigest()
        elif tamper == "field":
            from backend.app.fact_support import fact_key

            fact = session.get(EventFact, UUID(link["fact_id"]))
            fact.value = "被篡改的字段"
            fact.fact_key = fact_key(fact.name, fact.value, fact.unit)
        else:
            document = session.get(RawDocument, observation.raw_document_id)
            row = (
                session.get(Source, document.source_id) if tamper == "source_license" else document
            )
            row.license_status = "restricted"
        session.commit()
    assert promote(client, database, result).status_code == 422
    assert detail(client)["events"] == []
    with Session(database.owner) as session:
        assert session.scalar(select(func.count()).select_from(EventSharingDecision)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventEvidence)
                .where(EventEvidence.display_allowed.is_(True))
            )
            == 0
        )


def test_failed_analysis_does_not_repeat_on_page_visits_or_worker_polling(database, client):
    from backend.app.providers import LLMProviderError

    class FailedAnalysis(MockTenderAnalysis):
        def analyze_investor_change(self, request):
            self.calls += 1
            raise LLMProviderError("fixture_failed")

    assert promote(client, database, candidate(database)).status_code == 200
    provider = FailedAnalysis()
    assert worker(database, provider).outcome == "provider_failed"
    for _ in range(3):
        assert detail(client)["events"][0]["analysis"] is None
        assert worker(database, provider).status == "idle"
    assert provider.calls == 1


def test_review_migration_binds_the_right_event_and_guards_history(database, client):
    result = candidate(database)
    assert promote(client, database, result).status_code == 200
    other = candidate(database, "other_project")
    with Session(database.owner) as session:
        decision = session.scalar(select(EventSharingDecision))
        values = {
            column.name: getattr(decision, column.name)
            for column in EventSharingDecision.__table__.columns
        }
        values.update(
            id=uuid4(),
            source_observation_id=other.observation_id,
            idempotency_key=hashlib.sha256(uuid4().bytes).hexdigest(),
        )
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(EventSharingDecision(**values))
            session.flush()
        # 历史单事件决定的唯一约束仍生效；新列为空不获得重复审核能力。
        values.update(
            id=uuid4(),
            source_observation_id=None,
            idempotency_key=hashlib.sha256(uuid4().bytes).hexdigest(),
        )
        session.add(EventSharingDecision(**values))
        session.flush()
        values.update(id=uuid4(), idempotency_key=hashlib.sha256(uuid4().bytes).hexdigest())
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(EventSharingDecision(**values))
            session.flush()
        session.rollback()
    with pytest.raises(RuntimeError, match="Event observations exist"):
        command.downgrade(Config("alembic.ini"), "0028")
    with Session(database.owner) as session:
        assert session.scalar(select(func.count()).select_from(EventSharingDecision)) == 1


def test_tender_analysis_rls_keeps_queue_and_writes_admin_only(database, client):
    if not database.postgres:
        pytest.skip("PostgreSQL row policies do not apply to SQLite")
    from sqlalchemy.exc import DBAPIError

    from backend.app.investor_analysis import enqueue_pending_investor_analyses

    response = promote(client, database, candidate(database))
    assert response.status_code == 200
    shared_id = UUID(response.json()["shared_event_id"])
    with build_session_factory(database.app)() as session:
        _actor(session, ALPHA_USER_ID)
        enqueue_pending_investor_analyses(session, InvestorAnalysisPolicy())
    with Session(database.app) as session:
        _actor(session, NO_ACCESS_USER_ID)
        assert session.scalar(select(func.count()).select_from(InvestorChangeAnalysis)) == 0
        with pytest.raises(DBAPIError), session.begin_nested():
            session.add(
                InvestorChangeAnalysis(
                    event_id=shared_id,
                    visibility_scope="platform_shared",
                    status="pending",
                    schema_version="investor-change-analysis-v1",
                    prompt_version="investor-change-zh-v1",
                    input_hash="f" * 64,
                    evidence_ids=[],
                )
            )
            session.flush()
    assert worker(database, MockTenderAnalysis()).outcome == "completed"
    with Session(database.app) as session:
        _actor(session, NO_ACCESS_USER_ID)
        assert session.scalar(select(func.count()).select_from(InvestorChangeAnalysis)) == 1
