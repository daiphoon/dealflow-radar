from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.demo import ALPHA_TENANT_ID, ALPHA_USER_ID, BETA_USER_ID, NO_ACCESS_USER_ID
from backend.app.models import (
    Company,
    CompanyResearchJob,
    Event,
    OfficialIdentityVerification,
    PersonalCompanyRequest,
    RawDocument,
    ResearchImport,
    Role,
    UsageLedger,
    User,
    UserRoleAssignment,
)
from backend.app.personal_features import approve_platform_company_request_for_research
from backend.app.providers import ManualOfficialIdentityImportProvider
from backend.app.services import AccessDeniedError, import_official_identities


def _provider(tmp_path: Path, **record_overrides: object) -> ManualOfficialIdentityImportProvider:
    payload = json.loads(Path("data/sample/exchange_identity_import.json").read_text())
    payload["records"][0].update(record_overrides)
    path = tmp_path / "exchange.json"
    path.write_text(json.dumps(payload, ensure_ascii=False))
    return ManualOfficialIdentityImportProvider(path, allowed_root=tmp_path)


def _grant_admin(session: Session, *, expired: bool = False) -> None:
    role = session.scalar(select(Role).where(Role.code == "platform_admin"))
    session.add(
        UserRoleAssignment(
            user_id=ALPHA_USER_ID,
            role_id=role.id,
            valid_until=datetime.now(UTC) - timedelta(days=1) if expired else None,
        )
    )
    session.commit()


def test_exchange_import_preserves_evidence_and_queues_existing_request(
    tmp_path: Path, migrated_app: FastAPI
) -> None:
    provider = _provider(tmp_path)
    with migrated_app.state.session_factory() as session:
        _grant_admin(session)
        user = session.get(User, ALPHA_USER_ID)
        company = session.scalar(
            select(Company).where(Company.legal_name == "示例星河科技一号有限公司")
        )
        company.tenant_id = None
        company.visibility_scope = "public"
        company.identity_status = "unresolved"
        company.credit_code = "91310000MA1K000006"
        request = PersonalCompanyRequest(
            owner_user_id=NO_ACCESS_USER_ID,
            request_type="inclusion",
            requested_name=company.legal_name,
            requested_credit_code=company.credit_code,
            target_key=f"exchange-test:{company.credit_code}",
        )
        session.add(request)
        session.commit()
        company_id = company.id
        count_before = session.scalar(select(func.count()).select_from(Company))
        events_before = session.scalar(select(func.count()).select_from(Event))
        jobs_before = session.scalar(select(func.count()).select_from(CompanyResearchJob))
        first = import_official_identities(session, user, provider)
        second = import_official_identities(session, user, provider)
        assert session.scalar(select(func.count()).select_from(CompanyResearchJob)) == jobs_before
        verification = session.scalar(select(OfficialIdentityVerification))
        document = session.get(RawDocument, verification.raw_document_id)
        research_import = session.get(ResearchImport, document.research_import_id)
        assert first.verified_records == 1
        assert first.external_calls == 0
        assert second.status == "duplicate"
        assert company.identity_verification_basis == "exchange_disclosure"
        assert company.identity_status == "verified"
        assert verification.verification_basis == "exchange_disclosure"
        assert verification.match_rule == "exchange_credit_code_exact"
        assert document.owner_tenant_id == ALPHA_TENANT_ID
        assert document.visibility_scope == "organization_private"
        assert document.payload["identity_fields_confirmed"] is True
        assert document.payload["evidence_locator"]
        audit = document.payload["manual_review"]
        assert audit["reviewed_by"] == str(ALPHA_USER_ID)
        assert audit["reviewed_at"] and audit["reason"]
        assert audit["policy_version"] == "exchange-identity-v1"
        assert research_import.imported_by == ALPHA_USER_ID
        result = approve_platform_company_request_for_research(
            session, user, request.id, company_id=company.id, reason="交易所披露字段已人工核对"
        )
        assert result.status == "research_queued"
        assert session.scalar(select(func.count()).select_from(Company)) == count_before
        assert session.scalar(select(func.count()).select_from(Event)) == events_before
        ledger = session.scalar(select(UsageLedger))
        assert ledger.external_calls == ledger.input_tokens == ledger.output_tokens == 0

    with TestClient(migrated_app) as client:
        for viewer in (NO_ACCESS_USER_ID, BETA_USER_ID):
            response = client.get(
                f"/api/v1/companies/{company_id}", headers={"X-Demo-User-Id": str(viewer)}
            )
            assert response.status_code == 200
            assert response.json()["identity_verification_basis"] == "exchange_disclosure"
            assert "manual_review" not in response.text
            assert "虚构示例第 1 页" not in response.text


@pytest.mark.parametrize("scenario", ["institution_only", "expired_platform", "inactive"])
def test_exchange_import_requires_active_platform_admin(
    tmp_path: Path, migrated_app: FastAPI, scenario: str
) -> None:
    provider = _provider(tmp_path)
    with migrated_app.state.session_factory() as session:
        if scenario != "institution_only":
            _grant_admin(session, expired=scenario == "expired_platform")
        user = session.get(User, ALPHA_USER_ID)
        if scenario == "inactive":
            user.status = "inactive"
            session.commit()
        with pytest.raises(AccessDeniedError):
            import_official_identities(session, user, provider)
        assert session.scalar(select(func.count()).select_from(OfficialIdentityVerification)) == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"legal_name": "另一家虚构公司"},
        {"registered_region": "另一个省乙市"},
        {"credit_code": "91310000MA1K000019", "evidence_excerpt": "代码 91310000MA1K000019"},
    ],
)
def test_exchange_identity_conflicts_never_silently_overwrite_company(
    tmp_path: Path, migrated_app: FastAPI, overrides: dict
) -> None:
    provider = _provider(tmp_path, **overrides)
    with migrated_app.state.session_factory() as session:
        _grant_admin(session)
        user = session.get(User, ALPHA_USER_ID)
        company = session.scalar(
            select(Company).where(Company.legal_name == "示例星河科技一号有限公司")
        )
        company.credit_code = "91310000MA1K000006"
        company.identity_status = "unresolved"
        session.commit()
        original = (company.legal_name, company.credit_code, company.registered_region)
        result = import_official_identities(session, user, provider)
        assert result.verified_records == 0
        assert result.conflict_records == 1
        assert (company.legal_name, company.credit_code, company.registered_region) == original
        assert company.identity_status == "unresolved"


def test_unmatched_exchange_record_does_not_create_company_or_research_job(
    tmp_path: Path, migrated_app: FastAPI
) -> None:
    provider = _provider(
        tmp_path,
        legal_name="完全不同的虚构公司",
        credit_code="91310000MA1K999995",
        evidence_excerpt="完全不同的虚构公司，91310000MA1K999995，虚构省甲市",
    )
    with migrated_app.state.session_factory() as session:
        _grant_admin(session)
        user = session.get(User, ALPHA_USER_ID)
        companies_before = session.scalar(select(func.count()).select_from(Company))
        jobs_before = session.scalar(select(func.count()).select_from(CompanyResearchJob))
        result = import_official_identities(session, user, provider)
        assert result.unmatched_records == 1
        verification = session.scalar(select(OfficialIdentityVerification))
        assert verification.company_id is None
        assert session.scalar(select(func.count()).select_from(Company)) == companies_before
        assert session.scalar(select(func.count()).select_from(CompanyResearchJob)) == jobs_before
