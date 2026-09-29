"""正式报告入口的 14 种状态；每个案例同时走 SQLite 和非 owner PostgreSQL。"""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, select

from backend.app.demo import ALPHA_USER_ID, BETA_USER_ID, NO_ACCESS_USER_ID
from backend.app.models import (
    CompanyResearchJob,
    Event,
    EventEvidence,
    PersonalCompanyRequest,
    RawDocument,
    Source,
)
from tests.integration.test_personal_changes_reports import PERSONAL_HEADERS, SHARED_COMPANY_ID
from tests.integration.test_report_lifecycle_recovery import apps as apps
from tests.integration.test_report_lifecycle_recovery import database as database
from tests.integration.test_report_lifecycle_recovery import evidence, post
from tests.unit.test_research_completion import completed


def business_digest(owner):
    metadata = MetaData()
    metadata.reflect(bind=owner.state.engine)
    excluded = {"personal_company_reports", "personal_report_requests", "personal_usage_records"}
    with owner.state.engine.connect() as connection:
        return {
            table.name: hashlib.sha256(
                json.dumps(
                    sorted(
                        [dict(row._mapping) for row in connection.execute(select(table))],
                        key=lambda row: json.dumps(row, default=str, sort_keys=True),
                    ),
                    default=str,
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            for table in metadata.sorted_tables
            if table.name not in excluded
        }


@pytest.mark.parametrize(
    "case",
    [
        "complete_findings",
        "complete_empty",
        "partial",
        "network_fail",
        "fetch_fail",
        "not_run",
        "deferred",
        "historical",
        "authorized_lead",
        "unauthorized_lead",
        "withdrawn_lead",
        "manual_initial",
        "manual_failed_research",
        "mixed",
    ],
)
def test_truthful_report_lifecycle_and_readonly_reload(apps, case):
    owner, app = apps
    coverage = completed()
    coverage["category_coverage_version"] = "category-source-checks-v1"
    coverage["reference_at"] = "2026-09-29T00:00:00+00:00"
    coverage["event_window_days"] = 365
    status = "completed"
    if case in {"partial", "not_run", "manual_initial", "manual_failed_research", "deferred"}:
        for state in list(coverage["search_groups"].values())[2 if case == "partial" else 0 :]:
            state.update(
                status="budget_deferred" if case == "deferred" else "pending", providers={}
            )
        status = "budget_deferred" if case == "deferred" else "failed"
    if case in {"network_fail", "manual_failed_research"}:
        coverage["network_preflight"] = {"status": "execution_environment_non_public_dns"}
        for group in coverage["search_groups"].values():
            group.update(status="pending", providers={}, attempted_at=None, checked_at=None)
        status = "failed"
    if case == "fetch_fail":
        coverage["documents"] = [
            {
                "url": "https://example.invalid/fail",
                "status": "failed",
                "coverage_category": "product_technology",
                "error_code": "dynamic_rendering_required",
            }
        ]
        status = "failed"
    event_ids = []
    if case in {
        "complete_findings",
        "historical",
        "manual_initial",
        "manual_failed_research",
        "mixed",
    }:
        event_id, _ = evidence(owner, case, "legacy", "synthetic_demo")
        event_ids.append(event_id)
        with owner.state.session_factory() as session:
            event = session.get(Event, event_id)
            event.title = "虚构历史资料" if case in {"historical", "mixed"} else "虚构已确认资料"
            event.occurred_at = datetime(
                2024 if case in {"historical", "mixed"} else 2026, 7, 1, tzinfo=UTC
            )
            session.commit()
    if case == "mixed":
        event_ids.append(evidence(owner, "recent", "legacy", "synthetic_demo")[0])
    if case in {"authorized_lead", "unauthorized_lead", "withdrawn_lead", "mixed"}:
        event_id, evidence_id = evidence(owner, "lead-" + case, "legacy", "synthetic_demo")
        with owner.state.session_factory() as session:
            event = session.get(Event, event_id)
            event.title, event.summary = "虚构待核事项", "虚构未确认的技术进展，缺少独立证明。"
            event.status, event.publication_route = "candidate", "unconfirmed_lead"
            event.uncertainties = ["缺少一手证据及可靠日期"]
            row = session.get(EventEvidence, evidence_id)
            document = session.get(RawDocument, row.raw_document_id)
            if case == "unauthorized_lead":
                for item in (event, row, document):
                    item.visibility_scope, item.owner_user_id = "personal_private", BETA_USER_ID
            if case == "withdrawn_lead":
                session.get(Source, document.source_id).license_status = "withdrawn"
            session.commit()
        if case not in {"unauthorized_lead", "withdrawn_lead"}:
            event_ids.append(event_id)
    with owner.state.session_factory() as session:
        job = CompanyResearchJob(
            company_id=SHARED_COMPANY_ID,
            created_by_user_id=ALPHA_USER_ID,
            status=status,
            current_stage="completed",
            policy_version="bounded-web-v3",
            coverage=coverage,
        )
        session.add(job)
        session.flush()
        session.add(
            PersonalCompanyRequest(
                owner_user_id=NO_ACCESS_USER_ID,
                request_type="refresh",
                company_id=SHARED_COMPANY_ID,
                target_key="truthful-fixture-" + case,
                status=status,
                research_job_id=job.id,
            )
        )
        session.commit()
    before = business_digest(owner)
    with TestClient(app) as client:
        response = post(client)
        assert response.status_code == 200, response.text
        saved = response.json()
        markdown = saved["markdown"]
        assert "本次研究状态与范围" in markdown
        assert set(saved["source_event_ids"]) == {str(value) for value in event_ids}
        if case in {
            "partial",
            "network_fail",
            "fetch_fail",
            "not_run",
            "deferred",
            "manual_initial",
            "manual_failed_research",
        }:
            assert "本次研究未完成" in markdown
        if case in {"network_fail", "manual_failed_research"}:
            assert "环境预检失败" in markdown and "联网研究未开始" in markdown
        if case in {"not_run", "manual_initial"}:
            assert "尚未开始" in markdown and "未检查" in markdown
        if case == "fetch_fail":
            assert "正文不可获取" in markdown and "搜索线索不等于取得正文" in markdown
        if case == "deferred":
            assert "预算暂缓" in markdown
        if case in {"historical", "mixed"}:
            assert "历史资料（窗口外" in markdown
            assert markdown.index("历史资料（窗口外") < markdown.index("虚构历史资料")
        if case in {"authorized_lead", "mixed"}:
            assert "虚构待核事项" in markdown and "candidate / unconfirmed" in markdown
            assert "缺少一手证据" in markdown
        if case in {"unauthorized_lead", "withdrawn_lead"}:
            assert "虚构待核事项" not in markdown
        if case.startswith("complete_"):
            assert "状态：已完成本轮有限检查" in markdown
        for key in ("a", "b"):
            repeated = post(client, key).json()
            assert repeated["id"] == saved["id"] and repeated["reused"]
        for _ in range(2):
            reloaded = client.get(
                "/api/v1/me/reports/" + saved["id"], headers=PERSONAL_HEADERS
            ).json()
            assert reloaded["markdown"] == markdown and reloaded["history_status"] != "restricted"
        assert client.get("/api/v1/me/reports/" + saved["id"]).status_code == 401
        assert (
            client.get(
                "/api/v1/me/reports/" + saved["id"], headers={"X-Demo-User-Id": str(BETA_USER_ID)}
            ).status_code
            == 404
        )
    assert business_digest(owner) == before
    artifact_dir = os.environ.get("M2_R_REPORT_ARTIFACT_DIR")
    if (
        artifact_dir
        and owner.state.engine.dialect.name == "sqlite"
        and case in {"complete_findings", "complete_empty", "partial", "network_fail", "mixed"}
    ):
        target = Path(artifact_dir)
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        (target / f"{case}.md").write_text(markdown, encoding="utf-8")
        (target / f"{case}.json").write_text(
            json.dumps(saved, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )


def test_saved_lead_body_stops_after_retraction_without_changing_history(apps):
    owner, app = apps
    event_id, _ = evidence(owner, "lead-withdrawal", "legacy", "synthetic_demo")
    with owner.state.session_factory() as session:
        event = session.get(Event, event_id)
        event.status, event.publication_route = "candidate", "unconfirmed_lead"
        event.title = "虚构待核撤回样本"
        session.commit()
    with TestClient(app) as client:
        saved = post(client).json()
        assert "虚构待核撤回样本" in saved["markdown"]
        with owner.state.session_factory() as session:
            session.get(Event, event_id).status = "retracted"
            session.commit()
        body = client.get("/api/v1/me/reports/" + saved["id"], headers=PERSONAL_HEADERS).json()
        assert body["history_status"] == "restricted"
        assert "虚构待核撤回样本" not in body["markdown"]
