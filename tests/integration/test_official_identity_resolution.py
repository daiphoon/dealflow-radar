from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.demo import (
    ALPHA_TENANT_ID,
    ALPHA_USER_ID,
    BETA_USER_ID,
    MOCK_SOURCE_ID,
    NO_ACCESS_USER_ID,
)
from backend.app.models import (
    ORGANIZATION_PRIVATE_SCOPE,
    PLATFORM_SHARED_SCOPE,
    Company,
    CompanyAlias,
    CompanySnapshot,
    EntityMention,
    Event,
    EventEvidence,
    OfficialIdentityVerification,
    ResearchImport,
    ReviewQueue,
    Role,
    UsageLedger,
    User,
    UserRoleAssignment,
)
from backend.app.providers import (
    ManualOfficialIdentityImportProvider,
    ManualResearchImportProvider,
)
from backend.app.services import (
    AccessDeniedError,
    _record_former_legal_name,
    import_manual_research,
    import_official_identities,
    resolve_identity_review,
)


def _write_provider(
    tmp_path: Path,
    filename: str,
    payload: dict[str, object],
    provider_type: type[ManualResearchImportProvider] | type[ManualOfficialIdentityImportProvider],
) -> ManualResearchImportProvider | ManualOfficialIdentityImportProvider:
    (tmp_path / filename).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return provider_type(filename, allowed_root=tmp_path)


def _official_payload(
    *,
    query_text: str,
    legal_name: str,
    checked_at: datetime | None = None,
) -> dict[str, object]:
    resolved_checked_at = checked_at or datetime.now(UTC)
    return {
        "schema_version": "1.0",
        "batch_id": "official-identity-batch-001",
        "queried_at": resolved_checked_at.isoformat(),
        "source": {
            "code": "national_enterprise_credit_publicity_system",
            "name": "国家企业信用信息公示系统",
            "base_url": "https://bt.gsxt.gov.cn/",
        },
        "original_query": query_text,
        "target_company_hint": query_text,
        "license_status": "public",
        "records": [
            {
                "external_record_id": "official-record-001",
                "query_text": query_text,
                "legal_name": legal_name,
                "credit_code": "91310000MA1K000006",
                "registered_region": "虚构省甲市",
                "registration_status": "存续",
                "canonical_url": "https://bt.gsxt.gov.cn/",
                "checked_at": resolved_checked_at.isoformat(),
            }
        ],
    }


def test_official_identity_import_is_idempotent_and_enriches_exact_company(
    tmp_path: Path,
    migrated_app: FastAPI,
) -> None:
    legal_name = "示例星河科技一号有限公司"
    provider = _write_provider(
        tmp_path,
        "identity.json",
        _official_payload(query_text=legal_name, legal_name=legal_name),
        ManualOfficialIdentityImportProvider,
    )
    assert isinstance(provider, ManualOfficialIdentityImportProvider)

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        existing_company = session.scalar(select(Company).where(Company.legal_name == legal_name))
        assert existing_company is not None
        existing_company.credit_code = None
        session.commit()
        first = import_official_identities(session, user, provider)
        second = import_official_identities(session, user, provider)

        company = session.scalar(select(Company).where(Company.legal_name == legal_name))
        verification = session.scalar(select(OfficialIdentityVerification))
        ledger = session.scalar(
            select(UsageLedger).where(UsageLedger.operation == "official_identity_import")
        )
        assert first.status == "completed"
        assert first.verifications_created == 1
        assert first.verified_records == 1
        assert first.conflict_records == 0
        assert first.unmatched_records == 0
        assert first.external_calls == 0
        assert second.status == "duplicate"
        assert second.verifications_created == 0
        assert company is not None and company.credit_code == "91310000MA1K000006"
        assert company.identity_status == "verified"
        assert company.identity_verification_basis == "official_government"
        assert company.last_identity_checked_at is not None
        assert verification is not None and verification.company_id == company.id
        assert verification.verification_status == "verified"
        assert verification.verification_basis == "official_government"
        assert verification.match_rule == "official_legal_name_exact_credit_code_enriched"
        assert ledger is not None and ledger.external_calls == 0
        assert session.scalar(select(func.count()).select_from(ResearchImport)) == 1


def test_official_identity_conflict_is_recorded_without_silent_master_data_change(
    tmp_path: Path,
    migrated_app: FastAPI,
) -> None:
    legal_name = "示例星河科技一号有限公司"
    provider = _write_provider(
        tmp_path,
        "identity-conflict.json",
        _official_payload(query_text=legal_name, legal_name="官方记录中的另一主体"),
        ManualOfficialIdentityImportProvider,
    )
    assert isinstance(provider, ManualOfficialIdentityImportProvider)

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        company = session.scalar(select(Company).where(Company.legal_name == legal_name))
        assert user is not None and company is not None
        company.credit_code = "91310000MA1K000006"
        session.commit()

        result = import_official_identities(session, user, provider)

        verification = session.scalar(select(OfficialIdentityVerification))
        assert result.status == "completed_with_unresolved"
        assert result.verified_records == 0
        assert result.conflict_records == 1
        assert verification is not None and verification.company_id == company.id
        assert verification.verification_status == "conflict"
        assert "legal_name" in verification.match_rule
        assert company.legal_name == legal_name
        assert company.last_identity_checked_at is not None


@pytest.mark.parametrize("basis", ["official_government", "exchange_disclosure"])
def test_identity_selection_reroutes_original_record_without_external_call(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
    migrated_app: FastAPI,
    basis: str,
) -> None:
    query_text = "星河技术品牌"
    legal_name = "示例星河科技一号有限公司"
    official_legal_name = "示例星河科技一号（更名）有限公司"
    manual_payload = copy.deepcopy(manual_import_payload)
    records = manual_payload["records"]
    assert isinstance(records, list)
    records[0]["company_identity_evidence"]["legal_name"] = query_text
    records[0]["company_identity_evidence"]["official_website"] = "https://brand.example.com/"
    manual_provider = _write_provider(
        tmp_path,
        "manual-unresolved.json",
        manual_payload,
        ManualResearchImportProvider,
    )
    identity_payload = _official_payload(query_text=legal_name, legal_name=official_legal_name)
    if basis == "exchange_disclosure":
        identity_payload.update(
            verification_basis=basis,
            review_reason="人工确认虚构交易所披露中的名称、代码和注册地区",
            source={
                "code": "demo_exchange_disclosure",
                "name": "交易所披露示例（虚构测试资料）",
                "base_url": "https://www.hkexnews.hk/",
            },
        )
        identity_payload["records"][0].update(
            canonical_url="https://www.hkexnews.hk/listedco/listconews/sehk/2026/0101/demo.pdf",
            data_updated_at=identity_payload["queried_at"],
            identity_fields_confirmed=True,
            evidence_excerpt=f"{official_legal_name}，91310000MA1K000006，虚构省甲市",
            evidence_locator="虚构测试第 1 页",
        )
    identity_provider = _write_provider(
        tmp_path,
        "identity.json",
        identity_payload,
        ManualOfficialIdentityImportProvider,
    )
    assert isinstance(manual_provider, ManualResearchImportProvider)
    assert isinstance(identity_provider, ManualOfficialIdentityImportProvider)

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        if basis == "exchange_disclosure":
            role = session.scalar(select(Role).where(Role.code == "platform_admin"))
            assignment = UserRoleAssignment(user_id=user.id, role_id=role.id)
            session.add(assignment)
            session.flush()
            platform_assignment_id = assignment.id
        company = session.scalar(select(Company).where(Company.legal_name == legal_name))
        assert company is not None
        company.credit_code = "91310000MA1K000006"
        company.official_website = "https://brand.example.com"
        session.commit()
        manual_result = import_manual_research(session, user, manual_provider)
        identity_result = import_official_identities(session, user, identity_provider)
        review = session.scalar(select(ReviewQueue))
        verification = session.scalar(select(OfficialIdentityVerification))
        assert manual_result.identity_review_records == 1
        assert manual_result.events_created == 0
        assert identity_result.conflict_records == 1
        assert review is not None and verification is not None
        review_id = review.id
        verification_id = verification.id

    disabled_settings = migrated_app.state.settings
    with TestClient(migrated_app) as client:
        disabled = client.post(
            f"/api/v1/reviews/{review_id}/identity-resolution",
            headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
            json={
                "verification_id": str(verification_id),
                "reason": "代码、工商全称和注册地均与官方记录一致",
            },
        )
        assert disabled.status_code == 404

        migrated_app.state.settings = replace(
            disabled_settings,
            review_workbench_enabled=True,
        )
        workbench = client.get(
            "/api/v1/reviews/workbench",
            headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
        )
        assert workbench.status_code == 200
        candidate = workbench.json()[0]["identity_candidates"][0]
        assert candidate["verification_id"] == str(verification_id)
        assert candidate["credit_code"] == "91310000MA1K000006"
        assert candidate["verification_status"] == "conflict"
        assert candidate["verification_basis"] == basis

        if basis == "exchange_disclosure":
            with migrated_app.state.session_factory() as session:
                assignment = session.get(UserRoleAssignment, platform_assignment_id)
                assignment.valid_until = datetime.now(UTC) - timedelta(days=1)
                session.commit()
            denied = client.post(
                f"/api/v1/reviews/{review_id}/identity-resolution",
                headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
                json={"verification_id": str(verification_id), "reason": "无有效平台授权"},
            )
            assert denied.status_code == 403
            with migrated_app.state.session_factory() as session:
                assignment = session.get(UserRoleAssignment, platform_assignment_id)
                assignment.valid_until = None
                session.commit()

        resolved = client.post(
            f"/api/v1/reviews/{review_id}/identity-resolution",
            headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
            json={
                "verification_id": str(verification_id),
                "reason": "代码、工商全称和注册地均与官方记录一致",
            },
        )
        assert resolved.status_code == 200
        assert resolved.json()["publication_route"] == "unconfirmed_lead"
        assert resolved.json()["review_status"] == "approved"
        resolved_company_id = resolved.json()["company_id"]

        for headers in (
            {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)},
            {"X-Demo-User-Id": str(BETA_USER_ID)},
        ):
            for query in (legal_name, official_legal_name, "91310000MA1K000006"):
                search = client.get(
                    "/api/v1/companies/search",
                    headers=headers,
                    params={"q": query},
                )
                assert search.status_code == 200
                assert [item["id"] for item in search.json()] == [resolved_company_id]

        repeated = client.post(
            f"/api/v1/reviews/{review_id}/identity-resolution",
            headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
            json={
                "verification_id": str(verification_id),
                "reason": "重复决定应被拒绝",
            },
        )
        assert repeated.status_code == 403

        cross_tenant = client.post(
            f"/api/v1/reviews/{review_id}/identity-resolution",
            headers={"X-Demo-User-Id": str(BETA_USER_ID)},
            json={
                "verification_id": str(verification_id),
                "reason": "跨租户应被拒绝",
            },
        )
        assert cross_tenant.status_code == 403

    with migrated_app.state.session_factory() as session:
        review = session.get(ReviewQueue, review_id)
        mention = session.scalar(select(EntityMention))
        event = session.scalar(select(Event))
        assert mention is not None
        company = session.scalar(select(Company).where(Company.id == mention.candidate_company_id))
        former_name = session.scalar(
            select(CompanyAlias).where(CompanyAlias.alias_type == "former_legal_name")
        )
        manual_batch = session.scalar(
            select(ResearchImport).where(ResearchImport.batch_id == "manual-batch-001")
        )
        ledger = session.scalar(
            select(UsageLedger).where(UsageLedger.operation == "identity_review_resolution")
        )
        assert review is not None and review.decision == "resolve_identity"
        assert mention.resolution_status == "verified"
        assert mention.candidate_company_id is not None
        assert company is not None and company.legal_name == official_legal_name
        assert company.identity_verification_basis == basis
        assert former_name is not None and former_name.alias == legal_name
        assert former_name.verification_status == "verified"
        assert former_name.visibility_scope == PLATFORM_SHARED_SCOPE
        assert former_name.owner_user_id is None
        assert former_name.owner_tenant_id is None
        assert event is not None and event.status == "candidate"
        assert event.publication_route == "unconfirmed_lead"
        assert "source_url_unchecked" in event.publication_reasons
        assert manual_batch is not None and manual_batch.resolved_count == 1
        assert manual_batch.unresolved_count == 0
        assert manual_batch.identity_review_count == 0
        assert manual_batch.unconfirmed_count == 1
        assert ledger is not None and ledger.external_calls == 0
        assert session.scalar(select(func.count()).select_from(EventEvidence)) == 1
        assert session.scalar(select(func.count()).select_from(CompanySnapshot)) == 0


def test_tenant_owned_company_former_name_remains_organization_private(
    migrated_app: FastAPI,
) -> None:
    with migrated_app.state.session_factory() as session:
        company = Company(
            tenant_id=ALPHA_TENANT_ID,
            credit_code="PRIVATE-FORMER-NAME-CODE",
            legal_name="机构私有公司新名称",
            registered_region="虚构地区",
            identity_status="verified",
            visibility_scope="tenant",
        )
        session.add(company)
        session.flush()

        _record_former_legal_name(
            session,
            company,
            MOCK_SOURCE_ID,
            "机构私有公司旧名称",
            ALPHA_TENANT_ID,
        )
        session.commit()

        alias = session.scalar(select(CompanyAlias).where(CompanyAlias.company_id == company.id))
        assert alias is not None
        assert alias.visibility_scope == ORGANIZATION_PRIVATE_SCOPE
        assert alias.owner_user_id is None
        assert alias.owner_tenant_id == ALPHA_TENANT_ID


def test_identity_selection_excludes_retired_commercial_verification(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
    migrated_app: FastAPI,
) -> None:
    query_text = "历史商业身份候选"
    legal_name = "示例星河科技一号有限公司"
    records = manual_import_payload["records"]
    assert isinstance(records, list)
    records[0]["company_identity_evidence"]["legal_name"] = query_text
    manual_provider = _write_provider(
        tmp_path,
        "manual-retired-commercial.json",
        manual_import_payload,
        ManualResearchImportProvider,
    )
    identity_provider = _write_provider(
        tmp_path,
        "identity-retired-commercial.json",
        _official_payload(query_text=query_text, legal_name=legal_name),
        ManualOfficialIdentityImportProvider,
    )

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        manual_result = import_manual_research(session, user, manual_provider)
        identity_result = import_official_identities(session, user, identity_provider)
        review = session.scalar(select(ReviewQueue))
        verification = session.scalar(select(OfficialIdentityVerification))
        assert manual_result.identity_review_records == 1
        assert identity_result.verified_records == 1
        assert review is not None and verification is not None
        verification.verification_basis = "licensed_business_data"
        session.commit()
        review_id = review.id
        verification_id = verification.id

    disabled_settings = migrated_app.state.settings
    migrated_app.state.settings = replace(disabled_settings, review_workbench_enabled=True)
    try:
        with TestClient(migrated_app) as client:
            workbench = client.get(
                "/api/v1/reviews/workbench",
                headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
            )
            assert workbench.status_code == 200
            assert workbench.json()[0]["identity_candidates"] == []

            resolved = client.post(
                f"/api/v1/reviews/{review_id}/identity-resolution",
                headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
                json={
                    "verification_id": str(verification_id),
                    "reason": "已退役商业依据不能恢复身份",
                },
            )
            assert resolved.status_code == 403
    finally:
        migrated_app.state.settings = disabled_settings


def test_identity_selection_rejects_stale_official_candidate(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
    migrated_app: FastAPI,
) -> None:
    query_text = "已过期的身份查询"
    legal_name = "示例星河科技一号有限公司"
    records = manual_import_payload["records"]
    assert isinstance(records, list)
    records[0]["company_identity_evidence"]["legal_name"] = query_text
    manual_provider = _write_provider(
        tmp_path,
        "manual-stale.json",
        manual_import_payload,
        ManualResearchImportProvider,
    )
    identity_provider = _write_provider(
        tmp_path,
        "identity-stale.json",
        _official_payload(
            query_text=query_text,
            legal_name=legal_name,
            checked_at=datetime.now(UTC) - timedelta(days=31),
        ),
        ManualOfficialIdentityImportProvider,
    )
    assert isinstance(manual_provider, ManualResearchImportProvider)
    assert isinstance(identity_provider, ManualOfficialIdentityImportProvider)

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        import_manual_research(session, user, manual_provider)
        import_official_identities(session, user, identity_provider)
        review = session.scalar(select(ReviewQueue))
        verification = session.scalar(select(OfficialIdentityVerification))
        assert review is not None and verification is not None

        with pytest.raises(AccessDeniedError, match="stale"):
            resolve_identity_review(
                session,
                review.id,
                user,
                verification.id,
                "尝试选择已过期的官方核验",
            )

        assert review.status == "pending"
        assert session.scalar(select(func.count()).select_from(Event)) == 0
