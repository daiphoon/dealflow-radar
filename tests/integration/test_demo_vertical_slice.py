from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.demo import (
    ALPHA_FUND_ID,
    ALPHA_TENANT_ID,
    ALPHA_USER_ID,
    BETA_FUND_ID,
    BETA_USER_ID,
    NO_ACCESS_USER_ID,
    demo_uuid,
)
from backend.app.models import (
    CompanySnapshot,
    EntityMention,
    Event,
    EventEvidence,
    FundAccessGrant,
    RawDocument,
    RefreshJob,
    ReviewQueue,
    UsageLedger,
    utc_now,
)
from backend.app.providers import MockResearchProvider

ALPHA_HEADERS = {"X-Demo-User-Id": str(ALPHA_USER_ID)}
BETA_HEADERS = {"X-Demo-User-Id": str(BETA_USER_ID)}
NO_ACCESS_HEADERS = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}
SHARED_COMPANY_ID = demo_uuid("company-示例星河科技一号有限公司")
SECOND_COMPANY_ID = demo_uuid("company-示例星河科技二号有限公司")


def ingest(client: TestClient) -> dict[str, object]:
    response = client.post("/api/v1/demo/ingest", headers=ALPHA_HEADERS)
    assert response.status_code == 200
    return response.json()


def test_ingest_is_idempotent_and_records_zero_external_cost(
    client: TestClient, migrated_app: FastAPI
) -> None:
    first = ingest(client)
    second = ingest(client)

    assert first == {
        "records_seen": 10,
        "documents_created": 10,
        "events_created": 10,
        "reviews_created": 10,
        "external_calls": 0,
        "estimated_cost": "0",
    }
    assert second["records_seen"] == 10
    assert second["documents_created"] == 0
    assert second["events_created"] == 0
    assert second["reviews_created"] == 0
    assert second["external_calls"] == 0

    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RawDocument)) == 10
        assert session.scalar(select(func.count()).select_from(EntityMention)) == 10
        assert session.scalar(select(func.count()).select_from(Event)) == 10
        assert session.scalar(select(func.count()).select_from(EventEvidence)) == 10
        assert session.scalar(select(func.count()).select_from(ReviewQueue)) == 10
        assert session.scalar(select(func.sum(UsageLedger.external_calls))) == 0
        assert session.scalar(select(func.sum(UsageLedger.estimated_cost))) == Decimal("0")


def test_review_publish_snapshot_and_fund_isolation(
    client: TestClient, migrated_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    ingest(client)

    with migrated_app.state.session_factory() as session:
        session.add(
            FundAccessGrant(
                id=uuid4(),
                user_id=BETA_USER_ID,
                fund_id=ALPHA_FUND_ID,
                permission="read",
            )
        )
        session.commit()

    alpha_companies = client.get("/api/v1/companies", headers=ALPHA_HEADERS)
    beta_companies = client.get("/api/v1/companies", headers=BETA_HEADERS)
    hidden_companies = client.get("/api/v1/companies", headers=NO_ACCESS_HEADERS)
    assert alpha_companies.status_code == 200
    assert len(alpha_companies.json()) == 10
    assert [item["id"] for item in beta_companies.json()] == [str(SHARED_COMPANY_ID)]
    assert hidden_companies.json() == []

    with migrated_app.state.session_factory() as session:
        review_id = session.scalar(
            select(ReviewQueue.id)
            .join(Event, Event.id == ReviewQueue.event_id)
            .where(Event.company_id == SHARED_COMPANY_ID)
        )
    assert review_id is not None

    forbidden = client.post(
        f"/api/v1/reviews/{review_id}/decision",
        headers=BETA_HEADERS,
        json={"decision": "approve", "reason": "不应有权限审核"},
    )
    assert forbidden.status_code == 403

    approved = client.post(
        f"/api/v1/reviews/{review_id}/decision",
        headers=ALPHA_HEADERS,
        json={"decision": "approve", "reason": "身份与证据均已人工核验"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    def fail_if_sync_query_loads_provider(_: MockResearchProvider) -> None:
        raise AssertionError("同步查询不得调用 Provider")

    monkeypatch.setattr(MockResearchProvider, "load", fail_if_sync_query_loads_provider)
    alpha_detail = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=ALPHA_HEADERS)
    beta_detail = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=BETA_HEADERS)
    hidden_detail = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=NO_ACCESS_HEADERS)
    cross_fund_detail = client.get(f"/api/v1/companies/{SECOND_COMPANY_ID}", headers=BETA_HEADERS)

    assert alpha_detail.status_code == 200
    assert beta_detail.status_code == 200
    assert hidden_detail.status_code == 404
    assert cross_fund_detail.status_code == 404
    assert [item["fund_id"] for item in alpha_detail.json()["investments"]] == [str(ALPHA_FUND_ID)]
    assert [item["fund_id"] for item in beta_detail.json()["investments"]] == [str(BETA_FUND_ID)]
    assert len(alpha_detail.json()["events"]) == 1
    assert len(alpha_detail.json()["events"][0]["evidence"]) == 1
    assert alpha_detail.json()["freshness_status"] == "fresh"
    assert alpha_detail.json()["information_gaps"] == ["财务数据：暂无可靠公开数据。"]

    with migrated_app.state.session_factory() as session:
        event = session.scalar(select(Event).where(Event.company_id == SHARED_COMPANY_ID))
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == SHARED_COMPANY_ID,
                CompanySnapshot.is_current.is_(True),
            )
        )
        assert event is not None and event.status == "published"
        assert snapshot is not None
        assert snapshot.summary["published_event_count"] == 1

        beta_grant = session.scalar(
            select(FundAccessGrant).where(
                FundAccessGrant.user_id == BETA_USER_ID,
                FundAccessGrant.fund_id == BETA_FUND_ID,
            )
        )
        assert beta_grant is not None
        beta_grant.valid_until = datetime(2020, 1, 1, tzinfo=UTC)
        session.commit()

    expired_access = client.get("/api/v1/companies", headers=BETA_HEADERS)
    assert expired_access.json() == []


def test_refresh_dry_run_and_duplicate_job_merge(client: TestClient, migrated_app: FastAPI) -> None:
    ingest(client)

    dry_run = client.post(
        f"/api/v1/companies/{SHARED_COMPANY_ID}/refresh",
        headers=ALPHA_HEADERS,
    )
    queued = client.post(
        f"/api/v1/companies/{SHARED_COMPANY_ID}/refresh?dry_run=false",
        headers=ALPHA_HEADERS,
    )
    merged = client.post(
        f"/api/v1/companies/{SHARED_COMPANY_ID}/refresh?dry_run=false",
        headers=ALPHA_HEADERS,
    )
    second_tenant = client.post(
        f"/api/v1/companies/{SHARED_COMPANY_ID}/refresh?dry_run=false",
        headers=BETA_HEADERS,
    )
    hidden = client.post(
        f"/api/v1/companies/{SHARED_COMPANY_ID}/refresh?dry_run=false",
        headers=NO_ACCESS_HEADERS,
    )

    assert dry_run.json()["status"] == "dry_run"
    assert dry_run.json()["estimated_cost"] == "0"
    assert queued.json()["status"] == "queued"
    assert merged.json() == {**queued.json(), "status": "merged"}
    assert second_tenant.json()["status"] == "queued"
    assert second_tenant.json()["job_id"] != queued.json()["job_id"]
    assert hidden.status_code == 404
    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RefreshJob)) == 2
        job = session.scalar(select(RefreshJob).where(RefreshJob.tenant_id == ALPHA_TENANT_ID))
        assert job is not None
        assert job.estimated_cost == Decimal("0")
        job.status = "completed"
        session.commit()

    cooldown_merge = client.post(
        f"/api/v1/companies/{SHARED_COMPANY_ID}/refresh?dry_run=false",
        headers=ALPHA_HEADERS,
    )
    assert cooldown_merge.json() == {**queued.json(), "status": "merged"}


def test_detail_query_uses_ttl_and_enqueues_only_when_enabled(
    client: TestClient,
    migrated_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingest(client)
    with migrated_app.state.session_factory() as session:
        review_id = session.scalar(
            select(ReviewQueue.id)
            .join(Event, Event.id == ReviewQueue.event_id)
            .where(Event.company_id == SHARED_COMPANY_ID)
        )
    assert review_id is not None
    approved = client.post(
        f"/api/v1/reviews/{review_id}/decision",
        headers=ALPHA_HEADERS,
        json={"decision": "approve", "reason": "验证按需刷新缓存规则"},
    )
    assert approved.status_code == 200

    def fail_if_sync_query_loads_provider(_: MockResearchProvider) -> None:
        raise AssertionError("同步查询不得调用 Provider")

    monkeypatch.setattr(MockResearchProvider, "load", fail_if_sync_query_loads_provider)
    migrated_app.state.settings = replace(
        migrated_app.state.settings,
        auto_refresh_enabled=True,
    )

    fresh_detail = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=ALPHA_HEADERS)
    assert fresh_detail.status_code == 200
    assert fresh_detail.json()["freshness_status"] == "fresh"
    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RefreshJob)) == 0
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == SHARED_COMPANY_ID,
                CompanySnapshot.is_current.is_(True),
            )
        )
        assert snapshot is not None
        snapshot.last_checked_at = utc_now() - timedelta(days=15)
        session.commit()

    stale_list = client.get("/api/v1/companies", headers=ALPHA_HEADERS)
    stale_item = next(item for item in stale_list.json() if item["id"] == str(SHARED_COMPANY_ID))
    assert stale_item["freshness_status"] == "stale"
    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RefreshJob)) == 0

    migrated_app.state.settings = replace(
        migrated_app.state.settings,
        auto_refresh_enabled=False,
    )
    disabled_detail = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=ALPHA_HEADERS)
    assert disabled_detail.json()["freshness_status"] == "stale"
    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RefreshJob)) == 0

    migrated_app.state.settings = replace(
        migrated_app.state.settings,
        auto_refresh_enabled=True,
    )
    queued_detail = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=ALPHA_HEADERS)
    assert queued_detail.json()["freshness_status"] == "stale"
    with migrated_app.state.session_factory() as session:
        jobs = list(session.scalars(select(RefreshJob)))
        assert len(jobs) == 1
        assert jobs[0].refresh_reason == "stale_query"
        assert jobs[0].estimated_cost == Decimal("0")
        assert jobs[0].cooldown_until is not None

    merged_detail = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=ALPHA_HEADERS)
    assert merged_detail.json()["freshness_status"] == "refreshing"
    refreshing_list = client.get("/api/v1/companies", headers=ALPHA_HEADERS)
    refreshing_item = next(
        item for item in refreshing_list.json() if item["id"] == str(SHARED_COMPANY_ID)
    )
    assert refreshing_item["freshness_status"] == "refreshing"
    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RefreshJob)) == 1


def test_demo_auth_and_role_boundaries(client: TestClient) -> None:
    assert client.get("/api/v1/companies").status_code == 401
    assert (
        client.get("/api/v1/companies", headers={"X-Demo-User-Id": "not-a-uuid"}).status_code == 401
    )
    assert client.post("/api/v1/demo/ingest", headers=BETA_HEADERS).status_code == 403
    assert client.get("/api/v1/reviews", headers=BETA_HEADERS).status_code == 403
