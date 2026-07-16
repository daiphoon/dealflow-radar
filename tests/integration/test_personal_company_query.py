from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.demo import (
    ALPHA_FUND_ID,
    ALPHA_TENANT_ID,
    ALPHA_USER_ID,
    BETA_FUND_ID,
    BETA_TENANT_ID,
    BETA_USER_ID,
    DEMO_SHARED_COMPANY_CREDIT_CODE,
    MOCK_SOURCE_ID,
    NO_ACCESS_USER_ID,
    demo_uuid,
)
from backend.app.models import (
    ORGANIZATION_PRIVATE_SCOPE,
    PERSONAL_PRIVATE_SCOPE,
    Company,
    CompanyAlias,
    EntityMention,
    Event,
    EventEvidence,
    RawDocument,
    RefreshJob,
    ReviewQueue,
    UsageLedger,
)
from backend.app.providers import MockResearchProvider

ALPHA_HEADERS = {"X-Demo-User-Id": str(ALPHA_USER_ID)}
BETA_HEADERS = {"X-Demo-User-Id": str(BETA_USER_ID)}
NO_ACCESS_HEADERS = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}
SHARED_COMPANY_ID = demo_uuid("company-示例星河科技一号有限公司")
SHARED_COMPANY_NAME = "示例星河科技一号有限公司"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ingest_and_publish_shared_event(client: TestClient, app: FastAPI) -> UUID:
    ingested = client.post("/api/v1/demo/ingest", headers=ALPHA_HEADERS)
    assert ingested.status_code == 200
    with app.state.session_factory() as session:
        review_id = session.scalar(
            select(ReviewQueue.id)
            .join(Event, Event.id == ReviewQueue.event_id)
            .where(Event.company_id == SHARED_COMPANY_ID)
        )
    assert review_id is not None
    approved = client.post(
        f"/api/v1/reviews/{review_id}/decision",
        headers=ALPHA_HEADERS,
        json={"decision": "approve", "reason": "验证个人安全查询共享事实"},
    )
    assert approved.status_code == 200
    with app.state.session_factory() as session:
        event_id = session.scalar(
            select(Event.id).where(
                Event.company_id == SHARED_COMPANY_ID,
                Event.status == "published",
            )
        )
    assert event_id is not None
    return event_id


def _add_scoped_document(
    app: FastAPI,
    *,
    scope: str,
    owner_user_id: UUID | None,
    owner_tenant_id: UUID | None,
    suffix: str,
    mention_text: str,
    document_identity: str | None = None,
) -> tuple[RawDocument, EntityMention]:
    identity = document_identity or suffix
    with app.state.session_factory() as session:
        document = RawDocument(
            source_id=MOCK_SOURCE_ID,
            visibility_scope=scope,
            owner_user_id=owner_user_id,
            owner_tenant_id=owner_tenant_id,
            external_record_id=f"scope-{identity}",
            canonical_url=f"https://example.invalid/{suffix}",
            title=f"私有测试文档 {suffix}",
            published_at=datetime(2026, 7, 17, tzinfo=UTC),
            observed_at=datetime(2026, 7, 17, tzinfo=UTC),
            content_hash=_sha256(f"content:{identity}"),
            document_dedupe_key=_sha256(f"document:{identity}"),
            license_status="private_test",
            payload={"scope_test": suffix},
        )
        session.add(document)
        session.flush()
        mention = EntityMention(
            raw_document_id=document.id,
            visibility_scope=scope,
            owner_user_id=owner_user_id,
            owner_tenant_id=owner_tenant_id,
            candidate_company_id=SHARED_COMPANY_ID,
            mention_text=mention_text,
            match_rule="scope_test",
            match_confidence=Decimal("1.000"),
            resolution_status="verified",
        )
        session.add(mention)
        session.commit()
        session.refresh(document)
        session.refresh(mention)
        return document, mention


def _add_private_lead(
    app: FastAPI,
    *,
    scope: str,
    owner_user_id: UUID | None,
    owner_tenant_id: UUID | None,
    suffix: str,
    title: str,
    fingerprint: str | None = None,
    document_identity: str | None = None,
) -> Event:
    document, _ = _add_scoped_document(
        app,
        scope=scope,
        owner_user_id=owner_user_id,
        owner_tenant_id=owner_tenant_id,
        suffix=suffix,
        mention_text=f"{title}专用提及",
        document_identity=document_identity,
    )
    with app.state.session_factory() as session:
        event = Event(
            company_id=SHARED_COMPANY_ID,
            visibility_scope=scope,
            owner_user_id=owner_user_id,
            owner_tenant_id=owner_tenant_id,
            event_type="information_quality",
            event_subtype="scope_test",
            status="candidate",
            direction="neutral",
            materiality_score=10,
            risk_severity="low",
            confidence_score=Decimal("0.800"),
            source_quality="A",
            title=title,
            summary="仅用于验证私有数据作用域。",
            facts=[{"name": "scope", "value": scope, "unit": None}],
            uncertainties=["测试数据"],
            occurred_at=None,
            published_at=None,
            observed_at=datetime(2026, 7, 17, tzinfo=UTC),
            fingerprint_version="scope-test-v1",
            event_fingerprint=fingerprint or _sha256(f"event:{suffix}"),
            publication_route="unconfirmed_lead",
            publication_policy_version="scope-test-v1",
            publication_reasons=["scope_isolation_test"],
        )
        session.add(event)
        session.flush()
        session.add(
            EventEvidence(
                event_id=event.id,
                raw_document_id=document.id,
                visibility_scope=scope,
                owner_user_id=owner_user_id,
                owner_tenant_id=owner_tenant_id,
                evidence_excerpt="私有测试证据。",
                span_hash=_sha256(f"span:{suffix}"),
                support_type="supports",
            )
        )
        session.commit()
        session.refresh(event)
        return event


def test_no_fund_user_searches_shared_company_without_creating_or_leaking(
    client: TestClient,
    migrated_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ingest_and_publish_shared_event(client, migrated_app)
    with migrated_app.state.session_factory() as session:
        session.add_all(
            [
                Company(
                    tenant_id=ALPHA_TENANT_ID,
                    credit_code="PRIVATE-DEMO-CODE",
                    legal_name="机构私有测试公司",
                    registered_region="虚构地区",
                    identity_status="verified",
                    visibility_scope="tenant",
                ),
                Company(
                    tenant_id=None,
                    credit_code="UNRESOLVED-DEMO",
                    legal_name="未核验共享候选公司",
                    registered_region="虚构地区",
                    identity_status="unresolved",
                    visibility_scope="public",
                ),
                CompanyAlias(
                    company_id=SHARED_COMPANY_ID,
                    source_id=MOCK_SOURCE_ID,
                    visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
                    owner_user_id=None,
                    owner_tenant_id=ALPHA_TENANT_ID,
                    alias="机构私有星河代号",
                    normalized_alias="机构私有星河代号",
                    alias_type="internal_code",
                    verification_status="verified",
                ),
                CompanyAlias(
                    company_id=SHARED_COMPANY_ID,
                    source_id=MOCK_SOURCE_ID,
                    visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
                    owner_user_id=None,
                    owner_tenant_id=BETA_TENANT_ID,
                    alias="机构私有星河代号",
                    normalized_alias="机构私有星河代号",
                    alias_type="internal_code",
                    verification_status="verified",
                ),
            ]
        )
        session.commit()
        company_count = session.scalar(select(func.count()).select_from(Company))
        event_count = session.scalar(select(func.count()).select_from(Event))
        usage_count = session.scalar(select(func.count()).select_from(UsageLedger))
        session.add(
            RefreshJob(
                tenant_id=ALPHA_TENANT_ID,
                company_id=SHARED_COMPANY_ID,
                job_type="mock_refresh",
                refresh_reason="private_fund_refresh",
                status="running",
                idempotency_key=_sha256("private-fund-refresh"),
                estimated_cost=Decimal("0"),
                cooldown_until=datetime(2026, 7, 18, tzinfo=UTC),
            )
        )
        session.commit()

    _add_scoped_document(
        migrated_app,
        scope=ORGANIZATION_PRIVATE_SCOPE,
        owner_user_id=None,
        owner_tenant_id=ALPHA_TENANT_ID,
        suffix="private-search-mention",
        mention_text="私有文档中的隐藏公司称呼",
    )

    def fail_if_sync_query_loads_provider(_: MockResearchProvider) -> None:
        raise AssertionError("同步搜索和详情不得调用 Provider")

    monkeypatch.setattr(MockResearchProvider, "load", fail_if_sync_query_loads_provider)
    by_code = client.get(
        "/api/v1/companies/search",
        params={"q": DEMO_SHARED_COMPANY_CREDIT_CODE},
        headers=NO_ACCESS_HEADERS,
    )
    by_name = client.get(
        "/api/v1/companies/search",
        params={"q": SHARED_COMPANY_NAME},
        headers=NO_ACCESS_HEADERS,
    )
    by_alias = client.get(
        "/api/v1/companies/search",
        params={"q": "星河一号"},
        headers=NO_ACCESS_HEADERS,
    )
    expected_id = str(SHARED_COMPANY_ID)
    assert [item["id"] for item in by_code.json()] == [expected_id]
    assert [item["id"] for item in by_name.json()] == [expected_id]
    assert [item["id"] for item in by_alias.json()] == [expected_id]

    for hidden_query in (
        "机构私有测试公司",
        "PRIVATE-DEMO-CODE",
        "未核验共享候选公司",
        "机构私有星河代号",
        "私有文档中的隐藏公司称呼",
        "完全不存在的公司",
    ):
        response = client.get(
            "/api/v1/companies/search",
            params={"q": hidden_query},
            headers=NO_ACCESS_HEADERS,
        )
        assert response.status_code == 200
        assert response.json() == []

    detail = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=NO_ACCESS_HEADERS)
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["credit_code"] == DEMO_SHARED_COMPANY_CREDIT_CODE
    assert payload["investments"] == []
    assert payload["private_events"] == []
    assert payload["unconfirmed_leads"] == []
    assert payload["freshness_status"] == "fresh"
    assert len(payload["events"]) == 1
    assert len(payload["events"][0]["evidence"]) == 1

    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Company)) == company_count
        assert session.scalar(select(func.count()).select_from(Event)) == event_count
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == usage_count
        assert session.scalar(select(func.sum(UsageLedger.external_calls))) == 0
        assert session.scalar(select(func.sum(UsageLedger.input_tokens))) == 0
        assert session.scalar(select(func.sum(UsageLedger.output_tokens))) == 0


def test_private_leads_documents_evidence_and_fund_overlays_are_isolated(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    shared_event_id = _ingest_and_publish_shared_event(client, migrated_app)
    _add_private_lead(
        migrated_app,
        scope=PERSONAL_PRIVATE_SCOPE,
        owner_user_id=NO_ACCESS_USER_ID,
        owner_tenant_id=None,
        suffix="personal-no-access",
        title="个人私有线索",
    )
    shared_private_fingerprint = _sha256("same-private-fact-two-organizations")
    _add_private_lead(
        migrated_app,
        scope=ORGANIZATION_PRIVATE_SCOPE,
        owner_user_id=None,
        owner_tenant_id=ALPHA_TENANT_ID,
        suffix="organization-alpha",
        title="机构甲私有线索",
        fingerprint=shared_private_fingerprint,
        document_identity="same-private-document-two-organizations",
    )
    _add_private_lead(
        migrated_app,
        scope=ORGANIZATION_PRIVATE_SCOPE,
        owner_user_id=None,
        owner_tenant_id=BETA_TENANT_ID,
        suffix="organization-beta",
        title="机构乙私有线索",
        fingerprint=shared_private_fingerprint,
        document_identity="same-private-document-two-organizations",
    )
    private_document, _ = _add_scoped_document(
        migrated_app,
        scope=ORGANIZATION_PRIVATE_SCOPE,
        owner_user_id=None,
        owner_tenant_id=ALPHA_TENANT_ID,
        suffix="private-evidence-on-shared-event",
        mention_text="机构甲内部证据提及",
    )
    with migrated_app.state.session_factory() as session:
        session.add(
            EventEvidence(
                event_id=shared_event_id,
                raw_document_id=private_document.id,
                visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
                owner_user_id=None,
                owner_tenant_id=ALPHA_TENANT_ID,
                evidence_excerpt="机构甲私有证据不得跨租户公开。",
                span_hash=_sha256("private-evidence-on-shared-event"),
                support_type="supports",
            )
        )
        session.commit()

    alpha = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=ALPHA_HEADERS)
    beta = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=BETA_HEADERS)
    personal = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=NO_ACCESS_HEADERS)
    assert alpha.status_code == beta.status_code == personal.status_code == 200

    alpha_payload = alpha.json()
    beta_payload = beta.json()
    personal_payload = personal.json()
    assert [item["fund_id"] for item in alpha_payload["investments"]] == [str(ALPHA_FUND_ID)]
    assert [item["fund_id"] for item in beta_payload["investments"]] == [str(BETA_FUND_ID)]
    assert personal_payload["investments"] == []

    assert [item["title"] for item in alpha_payload["unconfirmed_leads"]] == ["机构甲私有线索"]
    assert [item["title"] for item in beta_payload["unconfirmed_leads"]] == ["机构乙私有线索"]
    assert [item["title"] for item in personal_payload["unconfirmed_leads"]] == ["个人私有线索"]
    assert len(alpha_payload["events"][0]["evidence"]) == 2
    assert len(beta_payload["events"][0]["evidence"]) == 1
    assert len(personal_payload["events"][0]["evidence"]) == 1

    with migrated_app.state.session_factory() as session:
        scoped_duplicates = list(
            session.scalars(
                select(Event).where(Event.event_fingerprint == shared_private_fingerprint)
            )
        )
        assert len(scoped_duplicates) == 2
        assert {item.owner_tenant_id for item in scoped_duplicates} == {
            ALPHA_TENANT_ID,
            BETA_TENANT_ID,
        }
