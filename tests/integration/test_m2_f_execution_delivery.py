"""固定研究参考参数、同库正式读取和隔离投影；无项目外部调用。"""

import hashlib
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from backend.app.config import WebResearchPolicy
from backend.app.demo import ALPHA_USER_ID
from backend.app.models import CompanyResearchJob, User
from backend.app.research_extraction import extraction_payload
from backend.app.web_research_service import (
    prepare_pending_research_requests,
    run_web_research_worker_once,
)
from backend.app.web_search import MockSearchProvider
from scripts.research_validation_contract import accept_state, export_state
from tests.integration.test_personal_changes_reports import BETA_HEADERS, SHARED_COMPANY_ID
from tests.integration.test_report_lifecycle_recovery import apps as apps
from tests.integration.test_report_lifecycle_recovery import database as database
from tests.integration.test_truthful_research_delivery import _queue

PERSONAL_HEADERS = {"X-Demo-User-Id": str(ALPHA_USER_ID)}


@pytest.mark.parametrize(
    "scenario",
    [
        "complete",
        "partial",
        "failed",
        "not_run",
        "deferred",
        "network_before",
        "network_after",
        "access_controlled",
        "rate_limited",
    ],
)
def test_same_database_real_request_report_and_reload_agree_without_business_side_effects(
    apps, scenario
):
    from backend.app.research_completion import STATUS_LABELS, completion_projection

    owner, app = apps
    _queue(owner)
    with owner.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        prepare_pending_research_requests(
            session,
            user,
            WebResearchPolicy(incremental_research_enabled=True, topic_planning_enabled=True),
        )
        job = session.scalar(select(CompanyResearchJob))
        coverage = dict(job.coverage)
        groups = {
            code: {
                **row,
                "status": "completed",
                "providers": {"baidu": {"status": "completed"}},
                "attempted_at": "2026-09-30T01:00:00+00:00",
                "checked_at": "2026-09-30T01:01:00+00:00",
                "subject_results": 0,
            }
            for code, row in coverage["search_groups"].items()
        }
        if scenario in {"not_run", "network_before"}:
            groups = {
                code: {
                    "topic_category": row["topic_category"],
                    "status": "pending",
                    "providers": {},
                }
                for code, row in groups.items()
            }
        if scenario == "failed":
            groups = {
                code: {**row, "status": "failed", "providers": {"baidu": {"status": "failed"}}}
                for code, row in groups.items()
            }
        if scenario == "deferred":
            groups = {code: {**row, "status": "budget_deferred"} for code, row in groups.items()}
        coverage["search_groups"] = groups
        first_code = next(iter(groups))
        first_category = groups[first_code]["topic_category"]
        if scenario in {"partial", "network_after", "access_controlled", "rate_limited"}:
            coverage["candidates"] = [
                {
                    "url": "https://example.invalid/fixture",
                    "coverage_category": first_category,
                    "query_kind": first_code,
                }
            ]
            groups[first_code]["subject_results"] = 1
        if scenario in {"network_after", "access_controlled", "rate_limited"}:
            coverage["documents"] = [
                {
                    "url": "https://example.invalid/fixture",
                    "coverage_category": first_category,
                    "status": "failed",
                    "error_code": "timeout" if scenario == "network_after" else scenario,
                    "attempted_at": "2026-09-30T01:02:00+00:00",
                }
            ]
        if scenario == "network_before":
            coverage["network_preflight"] = {"status": "execution_environment_non_public_dns"}
        coverage["previous_successful_checks"] = {first_category: "2026-09-15T01:00:00+00:00"}
        job.coverage = coverage
        job.status = (
            "completed"
            if scenario == "complete"
            else "queued"
            if scenario == "not_run"
            else "partial"
            if scenario in {"partial", "deferred"}
            else "failed"
        )
        session.commit()
        expected = completion_projection(coverage)
        context = {
            "company_key": str(job.company_id),
            "scope": coverage["research_scope"],
            "synthetic_user": str(ALPHA_USER_ID),
            "synthetic_tenant": "fixture",
        }
        source = {
            "job_id": str(job.id),
            "main": "fixture",
            "input_sha256": "fixture",
            "config_sha256": "fixture",
        }
        projection = export_state(
            coverage, context=context, source=source, attempt_at="2026-09-30T01:00:00+00:00"
        )
        accepted, reused = accept_state(projection, context=context, source=source)
        assert not reused
        assert completion_projection(accepted) == expected
        assert accept_state(projection, context=context, source=source, current=projection)[1]
        if scenario != "complete":
            assert expected["categories"][0]["last_successful_check_at"] is not None

    def protected_digest():
        tables = (
            "refresh_jobs",
            "company_research_jobs",
            "usage_ledger",
            "personal_watchlist_items",
            "personal_event_view_receipts",
            "event_observations",
            "event_facts",
            "event_fact_supports",
        )
        with owner.state.engine.connect() as connection:
            data = {
                name: sorted(
                    [
                        json.dumps(dict(row), sort_keys=True, default=str)
                        for row in connection.execute(
                            text('SELECT * FROM "' + name + '"')
                        ).mappings()
                    ]
                )
                for name in tables
            }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    before = protected_digest()
    with TestClient(app) as client:
        request = client.get("/api/v1/me/company-requests", headers=PERSONAL_HEADERS).json()[0]
        assert request["research_result"]["completion"]["status"] == expected["status"]
        from pydantic import TypeAdapter

        assert request["research_result"]["completion"]["categories"] == json.loads(
            TypeAdapter(list).dump_json(expected["categories"])
        )
        response = client.post(
            f"/api/v1/me/companies/{SHARED_COMPANY_ID}/reports",
            headers=PERSONAL_HEADERS,
            json={"idempotency_key": "a" * 64},
        )
        assert response.status_code == 200, response.text
        report = response.json()
        assert STATUS_LABELS[expected["status"]] in report["markdown"]
        for _ in range(2):
            reloaded = client.get("/api/v1/me/reports/" + report["id"], headers=PERSONAL_HEADERS)
            assert reloaded.json()["markdown"] == report["markdown"]
        assert (
            client.get("/api/v1/me/reports/" + report["id"], headers=BETA_HEADERS).status_code
            == 404
        )
    assert protected_digest() == before


def test_frozen_reference_flows_into_real_job_plan_and_model_context(migrated_app):
    _queue(migrated_app)
    reference = datetime.fromisoformat("2026-09-29T00:00:00+08:00")
    policy = WebResearchPolicy(incremental_research_enabled=True, topic_planning_enabled=True)
    providers = {code: MockSearchProvider(code) for code in ("baidu", "bocha")}
    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        prepare_pending_research_requests(session, user, policy, reference_at=reference)
        run_web_research_worker_once(session, user, providers, policy)
        job = session.scalar(select(CompanyResearchJob))
        assert job.coverage["reference_at"] == reference.isoformat()
        plans = [g["query_plan"] for g in job.coverage["search_groups"].values()]
        assert all(p["reference_at"] == reference.isoformat() for p in plans)
        import json
        from types import SimpleNamespace

        subject = SimpleNamespace(
            legal_name="示例公司",
            aliases=(),
            reference_at=job.coverage["reference_at"],
            event_window_days=365,
        )
        payload = extraction_payload(subject, "示例公司发布新产品。", policy)
        context = json.loads(payload["messages"][1]["content"])
        assert context["reference_at"] == reference.isoformat()
        assert context["event_window_days"] == 365


def test_bound_driver_rejects_reference_drift_before_real_job_creation(migrated_app, tmp_path):
    from pathlib import Path

    from scripts.research_validation_contract import prepare_bound_jobs, runtime_binding

    _queue(migrated_app)
    identity = tmp_path / "identity.json"
    identity.write_text(
        json.dumps(
            {
                "company_order": [
                    {"stable_company_key": "fixture", "legal_name": "示例公司", "safe_aliases": []}
                ]
            }
        )
    )
    reference = datetime.fromisoformat("2026-09-29T00:00:00+08:00")
    policy = WebResearchPolicy(incremental_research_enabled=True, topic_planning_enabled=True)
    frozen = runtime_binding(Path.cwd(), identity, policy, reference)
    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        with pytest.raises(ValueError, match="before_metered"):
            prepare_bound_jobs(
                session,
                user,
                policy,
                frozen,
                Path.cwd(),
                identity,
                datetime.fromisoformat("2026-09-30T00:00:00+08:00"),
            )
        assert session.scalar(select(CompanyResearchJob)) is None
        prepare_bound_jobs(session, user, policy, frozen, Path.cwd(), identity, reference)
        assert (
            session.scalar(select(CompanyResearchJob)).coverage["reference_at"]
            == reference.isoformat()
        )
