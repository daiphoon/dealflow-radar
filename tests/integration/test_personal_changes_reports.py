from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.config import PersonalEntitlementPolicy
from backend.app.demo import (
    ALPHA_TENANT_ID,
    BETA_USER_ID,
    NO_ACCESS_USER_ID,
    demo_uuid,
)
from backend.app.models import (
    ORGANIZATION_PRIVATE_SCOPE,
    PLATFORM_SHARED_SCOPE,
    Company,
    CompanySnapshot,
    Event,
    EventEvidence,
    PersonalCompanyReport,
    PersonalCompanyViewState,
    PersonalEventViewReceipt,
    PersonalUsageRecord,
    RawDocument,
    Source,
    UsageLedger,
)

PERSONAL_HEADERS = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}
BETA_HEADERS = {"X-Demo-User-Id": str(BETA_USER_ID)}
SHARED_COMPANY_ID = demo_uuid("company-示例星河科技一号有限公司")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _set_report_limit(app: FastAPI, limit: int) -> None:
    current = app.state.settings.personal_entitlement_policy
    app.state.settings = replace(
        app.state.settings,
        personal_entitlement_policy=PersonalEntitlementPolicy(
            monthly_search_limit=current.monthly_search_limit,
            watchlist_company_limit=current.watchlist_company_limit,
            monthly_report_limit=limit,
            monthly_request_limit=current.monthly_request_limit,
            request_cooldown_hours=current.request_cooldown_hours,
        ),
    )


def _add_shared_event(app: FastAPI, suffix: str) -> UUID:
    with app.state.session_factory() as session:
        observed_at = datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
        source = Source(
            id=uuid4(),
            code=f"personal-report-{suffix}",
            name="示例官方来源",
            source_quality="A",
            license_status="public",
            base_url="https://example.com",
        )
        session.add(source)
        session.flush()
        document = RawDocument(
            id=uuid4(),
            source_id=source.id,
            research_import_id=None,
            candidate_document_id=None,
            owner_user_id=None,
            owner_tenant_id=None,
            visibility_scope=PLATFORM_SHARED_SCOPE,
            external_record_id=f"personal-report-{suffix}",
            canonical_url=f"https://example.com/shared/{suffix}",
            title=f"示例来源标题 {suffix}",
            published_at=observed_at,
            published_on=None,
            observed_at=observed_at,
            content_hash=_sha256(f"shared-document:{suffix}"),
            document_dedupe_key=_sha256(f"shared-document-dedupe:{suffix}"),
            license_status="public",
            payload={
                "_source_verification": {
                    "status": "healthy",
                    "http_status": 200,
                    "checked_at": observed_at.isoformat(),
                    "final_url": f"https://example.com/shared/{suffix}",
                }
            },
        )
        session.add(document)
        session.flush()
        event = Event(
            id=uuid4(),
            company_id=SHARED_COMPANY_ID,
            visibility_scope=PLATFORM_SHARED_SCOPE,
            owner_user_id=None,
            owner_tenant_id=None,
            event_type="product_technology",
            event_subtype="verified_milestone",
            status="published",
            direction="positive",
            materiality_score=55,
            risk_severity="low",
            confidence_score=Decimal("0.910"),
            source_quality="A",
            title=f"已审核共享进展 {suffix}",
            summary=f"公开证据支持公司完成一项可复核进展 {suffix}。",
            facts=[{"name": "milestone", "value": suffix, "unit": None}],
            uncertainties=["未披露金额"],
            occurred_at=observed_at,
            published_at=observed_at,
            published_on=None,
            observed_at=observed_at,
            fingerprint_version="personal-report-v1",
            event_fingerprint=_sha256(f"shared-event:{suffix}"),
            publication_route="manual_shared_promotion",
            publication_policy_version="controlled-sharing-v1",
            publication_reasons=["platform_admin_approved"],
        )
        session.add(event)
        session.flush()
        session.add(
            EventEvidence(
                id=uuid4(),
                event_id=event.id,
                raw_document_id=document.id,
                source_event_evidence_id=None,
                visibility_scope=PLATFORM_SHARED_SCOPE,
                owner_user_id=None,
                owner_tenant_id=None,
                evidence_excerpt=f"允许展示的最小证据摘录 {suffix}。",
                span_hash=_sha256(f"shared-evidence:{suffix}"),
                support_type="supports",
                display_allowed=False,
            )
        )
        session.commit()
        return event.id


def _add_private_candidate(app: FastAPI, suffix: str) -> UUID:
    with app.state.session_factory() as session:
        observed_at = datetime(2026, 7, 21, 4, 30, tzinfo=UTC)
        event = Event(
            id=uuid4(),
            company_id=SHARED_COMPANY_ID,
            visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
            owner_user_id=None,
            owner_tenant_id=ALPHA_TENANT_ID,
            event_type="legal_compliance",
            event_subtype="private_candidate",
            status="candidate",
            direction="negative",
            materiality_score=80,
            risk_severity="high",
            confidence_score=Decimal("0.500"),
            source_quality="C",
            title=f"机构私有未确认线索 {suffix}",
            summary=f"该内容仅供来源机构内部判断 {suffix}。",
            facts=[],
            uncertainties=["证据不足"],
            occurred_at=None,
            published_at=None,
            published_on=None,
            observed_at=observed_at,
            fingerprint_version="personal-report-v1",
            event_fingerprint=_sha256(f"private-event:{suffix}"),
            publication_route="unconfirmed_lead",
            publication_policy_version="identity-first-v1",
            publication_reasons=["private_candidate"],
        )
        session.add(event)
        session.commit()
        return event.id


def test_personal_change_receipts_are_independent_and_exclude_private_candidates(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    baseline_event_id = _add_shared_event(migrated_app, "baseline")
    first = client.post(
        f"/api/v1/me/companies/{SHARED_COMPANY_ID}/view",
        headers=PERSONAL_HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["first_view"] is True
    assert first.json()["previous_viewed_at"] is None
    assert first.json()["new_events"] == []

    new_event_id = _add_shared_event(migrated_app, "after-first-view")
    private_event_id = _add_private_candidate(migrated_app, "must-not-leak")
    second = client.post(
        f"/api/v1/me/companies/{SHARED_COMPANY_ID}/view",
        headers=PERSONAL_HEADERS,
    )
    assert second.status_code == 200
    assert second.json()["first_view"] is False
    assert [item["id"] for item in second.json()["new_events"]] == [str(new_event_id)]
    assert "must-not-leak" not in second.text

    no_repeat = client.post(
        f"/api/v1/me/companies/{SHARED_COMPANY_ID}/view",
        headers=PERSONAL_HEADERS,
    )
    assert no_repeat.status_code == 200
    assert no_repeat.json()["new_events"] == []

    beta_first = client.post(
        f"/api/v1/me/companies/{SHARED_COMPANY_ID}/view",
        headers=BETA_HEADERS,
    )
    assert beta_first.status_code == 200
    assert beta_first.json()["first_view"] is True
    shared_after_both = _add_shared_event(migrated_app, "after-both-users")
    for headers in (PERSONAL_HEADERS, BETA_HEADERS):
        response = client.post(
            f"/api/v1/me/companies/{SHARED_COMPANY_ID}/view",
            headers=headers,
        )
        assert [item["id"] for item in response.json()["new_events"]] == [str(shared_after_both)]

    with migrated_app.state.session_factory() as session:
        receipts = set(
            session.scalars(
                select(PersonalEventViewReceipt.event_id).where(
                    PersonalEventViewReceipt.owner_user_id == NO_ACCESS_USER_ID
                )
            )
        )
        assert {baseline_event_id, new_event_id, shared_after_both} <= receipts
        assert private_event_id not in receipts
        assert session.scalar(select(func.count()).select_from(PersonalCompanyViewState)) == 2


def test_fixed_report_is_idempotent_private_and_server_limited(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _set_report_limit(migrated_app, 1)
    shared_event_id = _add_shared_event(migrated_app, "report-visible")
    _add_private_candidate(migrated_app, "report-hidden")
    with migrated_app.state.session_factory() as session:
        usage_ledger_before = session.scalar(select(func.count()).select_from(UsageLedger))
        company = session.get(Company, SHARED_COMPANY_ID)
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == SHARED_COMPANY_ID,
                CompanySnapshot.is_current.is_(True),
                CompanySnapshot.visibility_scope == PLATFORM_SHARED_SCOPE,
            )
        )
        assert company is not None
        if snapshot is None:
            snapshot = CompanySnapshot(
                company_id=SHARED_COMPANY_ID,
                owner_user_id=None,
                owner_tenant_id=None,
                visibility_scope=PLATFORM_SHARED_SCOPE,
                snapshot_version=1,
                is_current=True,
                data_as_of=None,
                last_checked_at=datetime(2020, 1, 1, tzinfo=UTC),
                freshness_status="fresh",
                summary={},
                information_gaps=["财务数据：暂无可靠公开数据。"],
            )
            session.add(snapshot)
        original_company_name = company.legal_name
        snapshot.last_checked_at = datetime(2020, 1, 1, tzinfo=UTC)
        snapshot.freshness_status = "fresh"
        session.commit()

    payload = {"idempotency_key": "a" * 64}
    created = client.post(
        f"/api/v1/me/companies/{SHARED_COMPANY_ID}/reports",
        headers=PERSONAL_HEADERS,
        json=payload,
    )
    repeated = client.post(
        f"/api/v1/me/companies/{SHARED_COMPANY_ID}/reports",
        headers=PERSONAL_HEADERS,
        json=payload,
    )
    assert created.status_code == repeated.status_code == 200
    assert created.json()["id"] == repeated.json()["id"]
    assert created.json()["reused"] is False
    assert repeated.json()["reused"] is True
    assert str(shared_event_id) in created.json()["source_event_ids"]
    markdown = created.json()["markdown"]
    assert "已审核共享进展 report-visible" in markdown
    assert "示例官方来源" in markdown
    assert "report-hidden" not in markdown
    assert "该内容仅供来源机构内部判断" not in markdown
    assert "投资金额" not in markdown
    assert "不含投资建议、机构私有数据或未确认线索" in markdown
    assert "- 数据新鲜度：stale" in markdown

    report_id = created.json()["id"]
    with migrated_app.state.session_factory() as session:
        company = session.get(Company, SHARED_COMPANY_ID)
        assert company is not None
        company.legal_name = "后续更名的示例公司"
        session.commit()
    own_report = client.get(f"/api/v1/me/reports/{report_id}", headers=PERSONAL_HEADERS)
    other_user = client.get(f"/api/v1/me/reports/{report_id}", headers=BETA_HEADERS)
    assert own_report.status_code == 200
    assert own_report.json()["company_legal_name"] == original_company_name
    assert own_report.json()["title"] == created.json()["title"]
    assert other_user.status_code == 404
    report_list = client.get("/api/v1/me/reports", headers=PERSONAL_HEADERS)
    assert [item["id"] for item in report_list.json()] == [report_id]
    assert client.get("/api/v1/me/reports", headers=BETA_HEADERS).json() == []

    limited = client.post(
        f"/api/v1/me/companies/{SHARED_COMPANY_ID}/reports",
        headers=PERSONAL_HEADERS,
        json={"idempotency_key": "b" * 64},
    )
    assert limited.status_code == 429
    assert limited.json()["detail"]["feature"] == "company_report"
    usage = client.get("/api/v1/me/usage", headers=PERSONAL_HEADERS).json()
    assert usage["reports"] == {"used": 1, "limit": 1, "remaining": 0}

    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PersonalCompanyReport)) == 1
        report_usage = session.scalar(
            select(func.count())
            .select_from(PersonalUsageRecord)
            .where(PersonalUsageRecord.operation == "company_report")
        )
        assert report_usage == 1
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == usage_ledger_before

    settings = migrated_app.state.settings
    assert settings.external_calls_enabled is False
    assert settings.paid_api_calls_enabled is False
    assert settings.auto_refresh_enabled is False
    assert settings.publication_policy.enabled is False
