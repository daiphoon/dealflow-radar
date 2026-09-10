from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.orm import Session

from backend.app.models import (
    PLATFORM_SHARED_SCOPE,
    SYSTEM_RESTRICTED_SCOPE,
    Company,
    Event,
    EventEvidence,
    OfficialIdentityVerification,
    PersonalCompanyRequest,
    RawDocument,
    Source,
    Tenant,
    User,
)

ROOT = Path(__file__).resolve().parents[2]
LEGACY_SOURCE_CODE = "tianyancha_licensed_business_data"


def _event(company_id, suffix: str) -> Event:
    return Event(
        company_id=company_id,
        owner_user_id=None,
        owner_tenant_id=None,
        visibility_scope=PLATFORM_SHARED_SCOPE,
        event_type="information_quality",
        event_subtype="provider_retirement_test",
        status="published",
        direction="neutral",
        materiality_score=20,
        risk_severity="none",
        confidence_score=Decimal("0.900"),
        source_quality="A",
        title=f"迁移验证事件 {suffix}",
        summary="用于验证旧来源退役时的数据保留和展示撤下。",
        facts=[],
        uncertainties=[],
        occurred_at=None,
        published_at=None,
        observed_at=datetime.now(UTC),
        fingerprint_version="1",
        event_fingerprint=suffix.ljust(64, "0"),
        publication_route="controlled_promotion",
        publication_policy_version="test-v1",
        publication_reasons=["preexisting_reason"],
    )


def _document(source_id, suffix: str) -> RawDocument:
    return RawDocument(
        source_id=source_id,
        research_import_id=None,
        candidate_document_id=None,
        owner_user_id=None,
        owner_tenant_id=None,
        visibility_scope=SYSTEM_RESTRICTED_SCOPE,
        external_record_id=f"retirement-{suffix}",
        canonical_url=f"https://example.invalid/{suffix}",
        title=f"迁移验证资料 {suffix}",
        published_at=None,
        observed_at=datetime.now(UTC),
        content_hash=suffix.ljust(64, "1"),
        document_dedupe_key=suffix.ljust(64, "2"),
        license_status="permission_confirmed",
        payload={"retained_for_audit": True},
    )


def test_retirement_migration_hides_legacy_only_results_and_preserves_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'provider-retirement.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "0021")
    engine = create_engine(database_url)

    provider_company = Company(
        tenant_id=None,
        credit_code="91310000MA1K000071",
        legal_name="示例旧来源公司有限公司",
        registered_region="虚构省甲市",
        identity_status="verified",
        identity_verification_basis="licensed_business_data",
        visibility_scope="public",
    )
    independent_company = Company(
        tenant_id=None,
        credit_code="91310000MA1K00008X",
        legal_name="示例独立来源公司有限公司",
        registered_region="虚构省乙市",
        identity_status="verified",
        identity_verification_basis="official_government",
        visibility_scope="public",
    )
    mixed_identity_company = Company(
        tenant_id=None,
        credit_code="91310000MA1K000099",
        legal_name="示例双依据公司有限公司",
        registered_region="虚构省丙市",
        identity_status="verified",
        identity_verification_basis="licensed_business_data",
        visibility_scope="public",
    )
    legacy_source = Source(
        code=LEGACY_SOURCE_CODE,
        name="历史授权数据源",
        source_quality="A",
        license_status="permission_confirmed",
        base_url=None,
    )
    independent_source = Source(
        code="official_public_web",
        name="政府公开来源",
        source_quality="A",
        license_status="public",
        base_url="https://www.gov.cn/",
    )
    tenant = Tenant(name="迁移测试租户")
    with Session(engine) as session:
        session.add_all(
            [
                provider_company,
                independent_company,
                mixed_identity_company,
                legacy_source,
                independent_source,
            ]
        )
        session.flush()
        legacy_document = _document(legacy_source.id, "legacy")
        independent_document = _document(independent_source.id, "independent")
        legacy_event = _event(provider_company.id, "legacy-event")
        mixed_event = _event(provider_company.id, "mixed-event")
        session.add_all([legacy_document, independent_document, legacy_event, mixed_event, tenant])
        session.flush()
        session.add(
            OfficialIdentityVerification(
                tenant_id=tenant.id,
                company_id=mixed_identity_company.id,
                raw_document_id=independent_document.id,
                query_text=mixed_identity_company.legal_name,
                legal_name=mixed_identity_company.legal_name,
                credit_code=mixed_identity_company.credit_code,
                registered_region=mixed_identity_company.registered_region,
                registration_status="存续",
                verification_status="verified",
                verification_basis="official_government",
                match_rule="official_credit_code_exact",
                checked_at=datetime.now(UTC),
            )
        )
        user = User(
            tenant_id=tenant.id,
            email="retirement@example.invalid",
            display_name="迁移测试用户",
        )
        session.add(user)
        session.flush()
        # This fixture deliberately targets 0021, before later job columns existed.
        jobs = Table("company_research_jobs", MetaData(), autoload_with=session.connection())
        job_id = uuid4()
        session.execute(
            jobs.insert().values(
                id=job_id.hex,
                company_id=provider_company.id.hex,
                created_by_user_id=user.id.hex,
                status="completed",
                current_stage="completed",
                policy_version="on-demand-research-v1",
                coverage={},
                external_calls=0,
                input_tokens=0,
                output_tokens=0,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        request = PersonalCompanyRequest(
            owner_user_id=user.id,
            request_type="inclusion",
            company_id=provider_company.id,
            requested_name=provider_company.legal_name,
            requested_credit_code=provider_company.credit_code,
            target_key=f"retirement:{provider_company.id}",
            status="completed",
            research_job_id=job_id,
            provider_company_id="historical-provider-record",
        )
        session.add_all(
            [
                request,
                EventEvidence(
                    event_id=legacy_event.id,
                    raw_document_id=legacy_document.id,
                    source_event_evidence_id=None,
                    owner_user_id=None,
                    owner_tenant_id=None,
                    visibility_scope=PLATFORM_SHARED_SCOPE,
                    evidence_excerpt="旧来源单独支持。",
                    span_hash="legacy-evidence".ljust(64, "3"),
                    display_allowed=True,
                ),
                EventEvidence(
                    event_id=mixed_event.id,
                    raw_document_id=legacy_document.id,
                    source_event_evidence_id=None,
                    owner_user_id=None,
                    owner_tenant_id=None,
                    visibility_scope=PLATFORM_SHARED_SCOPE,
                    evidence_excerpt="旧来源支持。",
                    span_hash="mixed-legacy".ljust(64, "4"),
                    display_allowed=True,
                ),
                EventEvidence(
                    event_id=mixed_event.id,
                    raw_document_id=independent_document.id,
                    source_event_evidence_id=None,
                    owner_user_id=None,
                    owner_tenant_id=None,
                    visibility_scope=PLATFORM_SHARED_SCOPE,
                    evidence_excerpt="独立公开来源支持。",
                    span_hash="mixed-independent".ljust(64, "5"),
                    display_allowed=True,
                ),
            ]
        )
        session.commit()
        provider_company_id = provider_company.id
        independent_company_id = independent_company.id
        mixed_identity_company_id = mixed_identity_company.id
        legacy_event_id = legacy_event.id
        mixed_event_id = mixed_event.id
        request_id = request.id
        legacy_document_id = legacy_document.id

    command.upgrade(config, "0022")
    with Session(engine) as session:
        assert session.get(Company, provider_company_id).identity_status == "unresolved"
        assert session.get(Company, independent_company_id).identity_status == "verified"
        mixed_identity = session.get(Company, mixed_identity_company_id)
        assert mixed_identity.identity_status == "verified"
        assert mixed_identity.identity_verification_basis == "official_government"
        retired_event = session.get(Event, legacy_event_id)
        assert retired_event.status == "retracted"
        assert "legacy_provider_retired" in retired_event.publication_reasons
        assert session.get(Event, mixed_event_id).status == "published"
        legacy_evidence = session.scalar(
            select(EventEvidence).where(EventEvidence.event_id == legacy_event_id)
        )
        mixed_evidence = list(
            session.scalars(select(EventEvidence).where(EventEvidence.event_id == mixed_event_id))
        )
        assert legacy_evidence is not None and legacy_evidence.display_allowed is False
        assert sorted(item.display_allowed for item in mixed_evidence) == [False, True]
        parked_request = session.get(PersonalCompanyRequest, request_id)
        assert parked_request.status == "failed"
        assert parked_request.research_job_id is None
        assert parked_request.last_error_code == "legacy_provider_retired"
        assert session.scalar(select(jobs.c.status).where(jobs.c.id == job_id.hex)) == "completed"
        assert session.scalar(select(RawDocument).where(RawDocument.id == legacy_document_id))
        assert session.scalar(select(Source).where(Source.code == LEGACY_SOURCE_CODE))

    command.downgrade(config, "0021")
    with Session(engine) as session:
        assert session.get(Company, provider_company_id).identity_status == "unresolved"
        assert session.get(Event, legacy_event_id).status == "retracted"
    engine.dispose()
