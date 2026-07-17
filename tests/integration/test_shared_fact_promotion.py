from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.demo import (
    ALPHA_TENANT_ID,
    ALPHA_USER_ID,
    BETA_TENANT_ID,
    BETA_USER_ID,
    NO_ACCESS_USER_ID,
    demo_uuid,
)
from backend.app.models import (
    ORGANIZATION_PRIVATE_SCOPE,
    PLATFORM_SHARED_SCOPE,
    SYSTEM_RESTRICTED_SCOPE,
    Company,
    EntityMention,
    Event,
    EventEvidence,
    EventSharingDecision,
    EventSharingDecisionEvidence,
    RawDocument,
    Role,
    Source,
    UserRoleAssignment,
)

SHARED_COMPANY_ID = demo_uuid("company-示例星河科技一号有限公司")
ALPHA_HEADERS = {"X-Demo-User-Id": str(ALPHA_USER_ID)}
NO_ACCESS_HEADERS = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}
BETA_HEADERS = {"X-Demo-User-Id": str(BETA_USER_ID)}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _grant_platform_admin(app: FastAPI) -> None:
    with app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.code == "platform_admin"))
        assert role is not None
        session.add(
            UserRoleAssignment(
                id=uuid4(),
                user_id=ALPHA_USER_ID,
                role_id=role.id,
                scope_id=None,
                valid_until=None,
            )
        )
        session.commit()
    app.state.settings = replace(app.state.settings, review_workbench_enabled=True)


def _add_candidate(
    app: FastAPI,
    *,
    suffix: str,
    owner_tenant_id: UUID = ALPHA_TENANT_ID,
    fingerprint: str | None = None,
    risk_severity: str = "low",
    verification_status: str = "unchecked",
    identity_status: str = "verified",
    include_evidence: bool = True,
) -> tuple[UUID, UUID | None, UUID]:
    with app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        assert company is not None
        source = Source(
            id=uuid4(),
            code=f"sharing-test-{suffix}",
            name=f"晋升测试来源 {suffix}",
            source_quality="A",
            license_status="public",
            base_url="https://example.invalid",
        )
        document = RawDocument(
            id=uuid4(),
            source_id=source.id,
            research_import_id=None,
            visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
            owner_user_id=None,
            owner_tenant_id=owner_tenant_id,
            external_record_id=f"sharing-{suffix}",
            canonical_url=f"https://example.invalid/{suffix}",
            title=f"晋升测试证据 {suffix}",
            published_at=datetime(2026, 7, 16, 9, 0, tzinfo=UTC),
            published_on=None,
            observed_at=datetime(2026, 7, 17, 9, 0, tzinfo=UTC),
            content_hash=_sha256(f"content:{suffix}"),
            document_dedupe_key=_sha256(f"document:{suffix}"),
            license_status="public",
            payload={
                "_source_verification": {
                    "status": verification_status,
                    "checked_at": None,
                    "http_status": 404 if verification_status == "broken" else None,
                    "final_url": None,
                    "reason": "test",
                    "external_calls": 0,
                }
            },
        )
        mention = EntityMention(
            id=uuid4(),
            raw_document_id=document.id,
            visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
            owner_user_id=None,
            owner_tenant_id=owner_tenant_id,
            candidate_company_id=company.id,
            mention_text=company.legal_name,
            match_rule="credit_code_exact",
            match_confidence=Decimal("1.000"),
            resolution_status=identity_status,
        )
        event = Event(
            id=uuid4(),
            company_id=company.id,
            visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
            owner_user_id=None,
            owner_tenant_id=owner_tenant_id,
            event_type="contract_commercial",
            event_subtype="business_update",
            status="candidate",
            direction="positive",
            materiality_score=60,
            risk_severity=risk_severity,
            confidence_score=Decimal("0.900"),
            source_quality="A",
            title=f"候选事件 {suffix}",
            summary=f"证据显示公司完成了一项可复核的业务进展 {suffix}。",
            facts=[{"name": "milestone", "value": suffix, "unit": None}],
            uncertainties=["未披露金额"],
            occurred_at=None,
            published_at=document.published_at,
            published_on=None,
            observed_at=document.observed_at,
            fingerprint_version="manual-v1",
            event_fingerprint=fingerprint or _sha256(f"event:{suffix}"),
            publication_route="unconfirmed_lead",
            publication_policy_version="identity-first-v1",
            publication_reasons=["auto_publish_disabled"],
        )
        session.add(source)
        session.flush()
        session.add(document)
        session.flush()
        session.add_all([mention, event])
        session.flush()
        evidence_id: UUID | None = None
        if include_evidence:
            evidence = EventEvidence(
                id=uuid4(),
                event_id=event.id,
                raw_document_id=document.id,
                source_event_evidence_id=None,
                visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
                owner_user_id=None,
                owner_tenant_id=owner_tenant_id,
                evidence_excerpt=f"可展示的最小证据摘录 {suffix}。",
                span_hash=_sha256(f"excerpt:{suffix}"),
                support_type="supports",
                display_allowed=False,
            )
            session.add(evidence)
            evidence_id = evidence.id
        session.commit()
        return event.id, evidence_id, document.id


def _promote(
    client: TestClient,
    event_id: UUID,
    evidence_id: UUID,
    *,
    unchecked: bool = True,
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/events/{event_id}/sharing/promotion",
        headers=ALPHA_HEADERS,
        json={
            "title": "经人工核验的业务进展",
            "summary": "来源显示该公司完成一项业务进展，具体金额未披露。",
            "reason": "已核对主体、谨慎表述和证据摘录。",
            "evidence_ids": [str(evidence_id)],
            "confirm_evidence_support": True,
            "confirm_unchecked_links": unchecked,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_controlled_promotion_deduplication_isolation_and_retraction(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    shared_fingerprint = _sha256("same-fact-across-organizations")
    alpha_event_id, alpha_evidence_id, alpha_document_id = _add_candidate(
        migrated_app,
        suffix="alpha",
        fingerprint=shared_fingerprint,
    )
    assert alpha_evidence_id is not None

    with migrated_app.state.session_factory() as session:
        private_evidence = session.get(EventEvidence, alpha_evidence_id)
        assert private_evidence is not None
        private_document = session.get(RawDocument, private_evidence.raw_document_id)
        assert private_document is not None
        restricted_document = RawDocument(
            id=uuid4(),
            source_id=private_document.source_id,
            research_import_id=None,
            visibility_scope=SYSTEM_RESTRICTED_SCOPE,
            owner_user_id=None,
            owner_tenant_id=None,
            external_record_id="sharing-system-restricted",
            canonical_url="https://example.invalid/system-restricted",
            title="系统受限证据",
            published_at=datetime(2026, 7, 16, 9, 0, tzinfo=UTC),
            published_on=None,
            observed_at=datetime(2026, 7, 17, 9, 0, tzinfo=UTC),
            content_hash=_sha256("content:system-restricted"),
            document_dedupe_key=_sha256("document:system-restricted"),
            license_status="public",
            payload={},
        )
        session.add(restricted_document)
        session.flush()
        session.add(
            EventEvidence(
                id=uuid4(),
                event_id=alpha_event_id,
                raw_document_id=restricted_document.id,
                source_event_evidence_id=None,
                visibility_scope=SYSTEM_RESTRICTED_SCOPE,
                owner_user_id=None,
                owner_tenant_id=None,
                evidence_excerpt="平台管理员也不应通过工作台看到系统受限证据。",
                span_hash=_sha256("excerpt:system-restricted"),
                support_type="supports",
                display_allowed=False,
            )
        )
        session.commit()

    candidates = client.get("/api/v1/sharing-candidates", headers=ALPHA_HEADERS)
    assert candidates.status_code == 200
    alpha_candidate = next(
        item for item in candidates.json() if item["source_event_id"] == str(alpha_event_id)
    )
    assert [item["id"] for item in alpha_candidate["event"]["evidence"]] == [str(alpha_evidence_id)]

    result = _promote(client, alpha_event_id, alpha_evidence_id)
    shared_event_id = UUID(str(result["shared_event_id"]))
    assert shared_event_id != alpha_event_id
    assert result["reused_shared_event"] is False

    repeated = _promote(client, alpha_event_id, alpha_evidence_id)
    assert repeated["shared_event_id"] == str(shared_event_id)
    assert repeated["reused_shared_event"] is True

    beta_event_id, beta_evidence_id, _ = _add_candidate(
        migrated_app,
        suffix="beta",
        owner_tenant_id=BETA_TENANT_ID,
        fingerprint=shared_fingerprint,
    )
    assert beta_evidence_id is not None
    beta_result = _promote(client, beta_event_id, beta_evidence_id)
    assert beta_result["shared_event_id"] == str(shared_event_id)
    assert beta_result["reused_shared_event"] is True

    conflicting_rejection = client.post(
        f"/api/v1/events/{alpha_event_id}/sharing/rejection",
        headers=ALPHA_HEADERS,
        json={"reason": "已晋升的同一来源不得再记录为拒绝。"},
    )
    assert conflicting_rejection.status_code == 422

    personal_detail = client.get(
        f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=NO_ACCESS_HEADERS
    )
    assert personal_detail.status_code == 200
    personal_payload = personal_detail.json()
    promoted = [item for item in personal_payload["events"] if item["id"] == str(shared_event_id)]
    assert len(promoted) == 1
    assert personal_payload["investments"] == []
    assert personal_payload["private_events"] == []
    assert personal_payload["unconfirmed_leads"] == []
    assert len(promoted[0]["evidence"]) == 2
    assert all(item["url_health_status"] == "unchecked" for item in promoted[0]["evidence"])
    assert all(item["link_display_allowed"] is True for item in promoted[0]["evidence"])

    other_organization = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=BETA_HEADERS)
    assert other_organization.status_code == 200
    assert any(item["id"] == str(shared_event_id) for item in other_organization.json()["events"])
    assert all(
        item["id"] != str(alpha_event_id) for item in other_organization.json()["unconfirmed_leads"]
    )

    institution_detail = client.get(
        f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=ALPHA_HEADERS
    ).json()
    assert any(
        item["id"] == str(alpha_event_id) for item in institution_detail["unconfirmed_leads"]
    )
    assert institution_detail["investments"]

    with migrated_app.state.session_factory() as session:
        source_event = session.get(Event, alpha_event_id)
        source_document = session.get(RawDocument, alpha_document_id)
        assert source_event is not None
        assert source_document is not None
        assert source_event.visibility_scope == ORGANIZATION_PRIVATE_SCOPE
        assert source_event.owner_tenant_id == ALPHA_TENANT_ID
        assert source_event.status == "candidate"
        assert source_document.visibility_scope == ORGANIZATION_PRIVATE_SCOPE
        assert source_document.owner_tenant_id == ALPHA_TENANT_ID
        shared_event = session.get(Event, shared_event_id)
        assert shared_event is not None
        assert shared_event.visibility_scope == PLATFORM_SHARED_SCOPE
        assert shared_event.owner_user_id is None
        assert shared_event.owner_tenant_id is None
        shared_evidence = list(
            session.scalars(select(EventEvidence).where(EventEvidence.event_id == shared_event_id))
        )
        assert {item.source_event_evidence_id for item in shared_evidence} == {
            alpha_evidence_id,
            beta_evidence_id,
        }
        assert all(item.raw_document_id is None for item in shared_evidence)
        assert session.scalar(select(func.count()).select_from(EventSharingDecision)) == 2
        assert session.scalar(select(func.count()).select_from(EventSharingDecisionEvidence)) == 2

    retracted = client.post(
        f"/api/v1/shared-events/{shared_event_id}/retraction",
        headers=ALPHA_HEADERS,
        json={"reason": "回归测试撤回，保留全部审计与私有底稿。"},
    )
    assert retracted.status_code == 200
    assert retracted.json()["shared_event_status"] == "retracted"
    after_retraction = client.get(
        f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=NO_ACCESS_HEADERS
    ).json()
    assert all(item["id"] != str(shared_event_id) for item in after_retraction["events"])
    with migrated_app.state.session_factory() as session:
        assert session.get(Event, alpha_event_id) is not None
        assert session.get(RawDocument, alpha_document_id) is not None
        assert session.get(Event, shared_event_id).status == "retracted"
        decisions = list(session.scalars(select(EventSharingDecision)))
        assert [decision.action for decision in decisions] == ["promote", "promote", "retract"]
        assert all(decision.reason for decision in decisions)
        assert all(decision.policy_version == "controlled-promotion-v1" for decision in decisions)


def test_promotion_eligibility_and_rejection_guards(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _grant_platform_admin(migrated_app)
    cases = [
        ("ambiguous", "low", "unchecked", "unresolved", True),
        ("serious", "critical", "unchecked", "verified", True),
        ("broken", "low", "broken", "verified", True),
        ("no-evidence", "low", "unchecked", "verified", False),
    ]
    created: dict[str, tuple[UUID, UUID | None]] = {}
    for suffix, risk, verification, identity, include_evidence in cases:
        event_id, evidence_id, _ = _add_candidate(
            migrated_app,
            suffix=suffix,
            risk_severity=risk,
            verification_status=verification,
            identity_status=identity,
            include_evidence=include_evidence,
        )
        created[suffix] = (event_id, evidence_id)

    for suffix in ("ambiguous", "serious", "broken"):
        event_id, evidence_id = created[suffix]
        assert evidence_id is not None
        response = client.post(
            f"/api/v1/events/{event_id}/sharing/promotion",
            headers=ALPHA_HEADERS,
            json={
                "title": "不应晋升的测试事件",
                "summary": "该记录应当被资格闸门拦截。",
                "reason": "验证拦截规则。",
                "evidence_ids": [str(evidence_id)],
                "confirm_evidence_support": True,
                "confirm_unchecked_links": True,
            },
        )
        assert response.status_code == 422

    no_evidence_event, _ = created["no-evidence"]
    empty = client.post(
        f"/api/v1/events/{no_evidence_event}/sharing/promotion",
        headers=ALPHA_HEADERS,
        json={
            "title": "没有证据的事件",
            "summary": "不应在无证据时晋升。",
            "reason": "验证无证据拦截。",
            "evidence_ids": [],
            "confirm_evidence_support": True,
            "confirm_unchecked_links": True,
        },
    )
    assert empty.status_code == 422

    unchecked_event, unchecked_evidence, _ = _add_candidate(
        migrated_app,
        suffix="unchecked-confirmation",
    )
    assert unchecked_evidence is not None
    unchecked = client.post(
        f"/api/v1/events/{unchecked_event}/sharing/promotion",
        headers=ALPHA_HEADERS,
        json={
            "title": "未确认链接检查的事件",
            "summary": "需要管理员显式确认未自动验证链接。",
            "reason": "验证未检查链接闸门。",
            "evidence_ids": [str(unchecked_evidence)],
            "confirm_evidence_support": True,
            "confirm_unchecked_links": False,
        },
    )
    assert unchecked.status_code == 422

    rejected = client.post(
        f"/api/v1/events/{unchecked_event}/sharing/rejection",
        headers=ALPHA_HEADERS,
        json={"reason": "本次不晋升，保留原私有候选。"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["action"] == "reject"
    rejected_promotion = client.post(
        f"/api/v1/events/{unchecked_event}/sharing/promotion",
        headers=ALPHA_HEADERS,
        json={
            "title": "已拒绝的事件",
            "summary": "已拒绝候选不得在没有重新开启流程时晋升。",
            "reason": "验证晋升和拒绝互斥。",
            "evidence_ids": [str(unchecked_evidence)],
            "confirm_evidence_support": True,
            "confirm_unchecked_links": True,
        },
    )
    assert rejected_promotion.status_code == 422
    with migrated_app.state.session_factory() as session:
        source_event = session.get(Event, unchecked_event)
        assert source_event is not None
        assert source_event.status == "candidate"
        assert source_event.visibility_scope == ORGANIZATION_PRIVATE_SCOPE
