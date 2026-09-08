from copy import deepcopy
from uuid import uuid4

from sqlalchemy import func, select

from backend.app.demo import ALPHA_TENANT_ID, BETA_USER_ID, NO_ACCESS_USER_ID, demo_uuid
from backend.app.models import (
    Company,
    CompanyResearchJob,
    Event,
    PersonalCompanyRequest,
    UsageLedger,
)

COMPANY_ID = demo_uuid("company-示例星河科技一号有限公司")
HEADERS = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}


def _event(*, status="candidate", route="unconfirmed_lead", scope="platform_shared", **owners):
    return Event(
        company_id=COMPANY_ID,
        event_type="product_technology",
        event_subtype="test",
        status=status,
        publication_route=route,
        visibility_scope=scope,
        title="虚构测试条目",
        summary="不包含真实公司信息",
        direction="neutral",
        materiality_score=60,
        risk_severity="low",
        confidence_score="0.8",
        source_quality="A",
        event_fingerprint=uuid4().hex,
        **owners,
    )


def test_request_current_content_matches_details_without_rewriting_history(client, migrated_app):
    coverage = {
        "completed_at": "2026-09-01T02:53:33+00:00",
        "stats": {"search_calls": 2, "quality_gate_passed": 0},
        "modules": {"product_technology": "no_data", "information_quality": "completed"},
        "documents": [{"status": "failed", "error_code": "robots_disallowed"}],
    }
    original = deepcopy(coverage)
    with migrated_app.state.session_factory() as session:
        job = CompanyResearchJob(
            company_id=COMPANY_ID,
            created_by_user_id=NO_ACCESS_USER_ID,
            status="completed",
            policy_version="bounded-web-v3",
            coverage=coverage,
        )
        session.add(job)
        session.flush()
        job_id = job.id
        for index in range(2):
            session.add(
                PersonalCompanyRequest(
                    owner_user_id=NO_ACCESS_USER_ID,
                    company_id=COMPANY_ID,
                    research_job_id=job_id,
                    request_type="refresh",
                    target_key=f"current-content-{index}",
                    status="completed",
                )
            )
        session.commit()
        ledger_before = session.scalar(select(func.count()).select_from(UsageLedger))

    def read(expected):
        response = client.get("/api/v1/me/company-requests", headers=HEADERS)
        assert response.status_code == 200
        outputs = response.json()
        assert len(outputs) == 2
        for output in outputs:
            assert output["current_company_content"] == expected
            assert output["research_result"]["outcome"] == "no_usable_evidence"
            assert output["research_modules"] == original["modules"]
            assert output["research_result"]["finished_at"].startswith("2026-09-01T02:53:33")
        detail = client.get(f"/api/v1/companies/{COMPANY_ID}", headers=HEADERS).json()
        assert expected["unconfirmed_leads"] == len(detail["platform_unconfirmed_leads"])
        changes = sum(
            event["publication_route"] == "deterministic_change" for event in detail["events"]
        )
        assert expected["confirmed_changes"] == changes
        assert expected["baseline_facts"] == len(detail["events"]) - changes
        assert (
            client.get(
                "/api/v1/me/company-requests", headers={"X-Demo-User-Id": str(BETA_USER_ID)}
            ).json()
            == []
        )
        with migrated_app.state.session_factory() as session:
            assert session.get(CompanyResearchJob, job_id).coverage == original
            assert session.scalar(select(func.count()).select_from(UsageLedger)) == ledger_before

    read({"confirmed_changes": 0, "baseline_facts": 0, "unconfirmed_leads": 0})
    with migrated_app.state.session_factory() as session:
        lead = _event()
        session.add_all(
            [
                lead,
                _event(status="published", route="deterministic_change"),
                _event(status="published", route="manual_shared_promotion"),
                _event(status="rejected"),
                _event(status="retracted"),
                _event(route="other_candidate"),
                _event(scope="personal_private", owner_user_id=NO_ACCESS_USER_ID),
                _event(scope="personal_private", owner_user_id=BETA_USER_ID),
                _event(scope="organization_private", owner_tenant_id=ALPHA_TENANT_ID),
                _event(scope="system_restricted"),
            ]
        )
        session.commit()
        lead_id = lead.id
    for _ in range(2):
        read({"confirmed_changes": 1, "baseline_facts": 1, "unconfirmed_leads": 1})
    with migrated_app.state.session_factory() as session:
        session.get(Event, lead_id).status = "retracted"
        session.commit()
    read({"confirmed_changes": 1, "baseline_facts": 1, "unconfirmed_leads": 0})


def test_request_counts_do_not_disclose_non_catalog_companies(client, migrated_app):
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, COMPANY_ID)
        company.identity_status = "unresolved"
        session.add(_event())
        session.add(
            PersonalCompanyRequest(
                owner_user_id=NO_ACCESS_USER_ID,
                company_id=COMPANY_ID,
                request_type="refresh",
                target_key="non-catalog-counts",
                status="completed",
            )
        )
        session.commit()
    response = client.get("/api/v1/me/company-requests", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()[0]["current_company_content"] is None
    assert client.get(f"/api/v1/companies/{COMPANY_ID}", headers=HEADERS).status_code == 404
    assert client.get("/api/v1/me/company-requests").status_code == 401
