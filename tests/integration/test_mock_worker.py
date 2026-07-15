from __future__ import annotations

from dataclasses import replace
from datetime import UTC, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.demo import (
    ALPHA_TENANT_ID,
    ALPHA_USER_ID,
    BETA_TENANT_ID,
    BETA_USER_ID,
    demo_uuid,
)
from backend.app.models import CompanySnapshot, Event, RefreshJob, ReviewQueue, UsageLedger, utc_now
from backend.app.providers import MockResearchProvider
from backend.app.worker import run_mock_worker_once

ALPHA_HEADERS = {"X-Demo-User-Id": str(ALPHA_USER_ID)}
BETA_HEADERS = {"X-Demo-User-Id": str(BETA_USER_ID)}
SHARED_COMPANY_ID = demo_uuid("company-示例星河科技一号有限公司")
SECOND_COMPANY_ID = demo_uuid("company-示例星河科技二号有限公司")


def _fail_if_worker_loads_provider(_: MockResearchProvider) -> None:
    raise AssertionError("Mock Worker V1 不得调用 Provider")


def _ingest(client: TestClient) -> None:
    response = client.post("/api/v1/demo/ingest", headers=ALPHA_HEADERS)
    assert response.status_code == 200


def _publish_shared_company(client: TestClient, app: FastAPI) -> None:
    _ingest(client)
    with app.state.session_factory() as session:
        review_id = session.scalar(
            select(ReviewQueue.id)
            .join(Event, Event.id == ReviewQueue.event_id)
            .where(Event.company_id == SHARED_COMPANY_ID)
        )
    assert review_id is not None
    response = client.post(
        f"/api/v1/reviews/{review_id}/decision",
        headers=ALPHA_HEADERS,
        json={"decision": "approve", "reason": "验证 Mock Worker 刷新闭环"},
    )
    assert response.status_code == 200


def _as_utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def test_mock_worker_completes_stale_to_fresh_without_external_calls(
    client: TestClient,
    migrated_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _publish_shared_company(client, migrated_app)
    migrated_app.state.settings = replace(
        migrated_app.state.settings,
        auto_refresh_enabled=True,
    )
    with migrated_app.state.session_factory() as session:
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == SHARED_COMPANY_ID,
                CompanySnapshot.is_current.is_(True),
            )
        )
        assert snapshot is not None
        data_as_of = snapshot.data_as_of
        snapshot.last_checked_at = utc_now() - timedelta(days=15)
        session.commit()

    monkeypatch.setattr(MockResearchProvider, "load", _fail_if_worker_loads_provider)
    stale = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=ALPHA_HEADERS)
    refreshing = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=ALPHA_HEADERS)
    assert stale.json()["freshness_status"] == "stale"
    assert refreshing.json()["freshness_status"] == "refreshing"

    completed_at = utc_now().replace(microsecond=0)
    with migrated_app.state.session_factory() as session:
        result = run_mock_worker_once(
            session,
            ALPHA_TENANT_ID,
            migrated_app.state.settings.refresh_policy,
            now=completed_at,
        )
    assert result.status == "completed"
    assert result.outcome == "no_change"
    assert result.snapshot_updated is True
    assert result.external_calls == 0
    assert result.estimated_cost == Decimal("0")

    with migrated_app.state.session_factory() as session:
        job = session.scalar(select(RefreshJob).where(RefreshJob.id == result.job_id))
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == SHARED_COMPANY_ID,
                CompanySnapshot.is_current.is_(True),
            )
        )
        ledger = session.scalar(select(UsageLedger).where(UsageLedger.operation == "mock_refresh"))
        assert job is not None
        assert job.status == "completed"
        assert job.leased_until is None
        assert _as_utc(job.heartbeat_at) == completed_at
        assert snapshot is not None
        assert snapshot.data_as_of == data_as_of
        assert _as_utc(snapshot.last_checked_at) == completed_at
        assert ledger is not None
        assert ledger.external_calls == 0
        assert ledger.estimated_cost == Decimal("0")
        assert ledger.metrics["outcome"] == "no_change"

    fresh = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=ALPHA_HEADERS)
    assert fresh.json()["freshness_status"] == "fresh"
    with migrated_app.state.session_factory() as session:
        idle = run_mock_worker_once(
            session,
            ALPHA_TENANT_ID,
            migrated_app.state.settings.refresh_policy,
            now=completed_at,
        )
        assert idle.status == "idle"
        assert (
            session.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.operation == "mock_refresh")
            )
            == 1
        )


def test_mock_worker_preserves_unknown_when_no_snapshot_exists(
    client: TestClient,
    migrated_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ingest(client)
    migrated_app.state.settings = replace(
        migrated_app.state.settings,
        auto_refresh_enabled=True,
    )
    monkeypatch.setattr(MockResearchProvider, "load", _fail_if_worker_loads_provider)

    unknown = client.get(f"/api/v1/companies/{SECOND_COMPANY_ID}", headers=ALPHA_HEADERS)
    assert unknown.json()["freshness_status"] == "unknown"
    with migrated_app.state.session_factory() as session:
        result = run_mock_worker_once(
            session,
            ALPHA_TENANT_ID,
            migrated_app.state.settings.refresh_policy,
        )
    assert result.status == "completed"
    assert result.outcome == "no_snapshot"
    assert result.snapshot_updated is False

    still_unknown = client.get(f"/api/v1/companies/{SECOND_COMPANY_ID}", headers=ALPHA_HEADERS)
    assert still_unknown.json()["freshness_status"] == "unknown"
    with migrated_app.state.session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(CompanySnapshot)
                .where(CompanySnapshot.company_id == SECOND_COMPANY_ID)
            )
            == 0
        )
        assert session.scalar(select(func.count()).select_from(RefreshJob)) == 1


def test_mock_worker_recovers_expired_lease_without_crossing_tenants(
    client: TestClient,
    migrated_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ingest(client)
    alpha = client.post(
        f"/api/v1/companies/{SHARED_COMPANY_ID}/refresh?dry_run=false",
        headers=ALPHA_HEADERS,
    )
    beta = client.post(
        f"/api/v1/companies/{SHARED_COMPANY_ID}/refresh?dry_run=false",
        headers=BETA_HEADERS,
    )
    assert alpha.json()["status"] == "queued"
    assert beta.json()["status"] == "queued"
    monkeypatch.setattr(MockResearchProvider, "load", _fail_if_worker_loads_provider)

    recovered_at = utc_now().replace(microsecond=0)
    with migrated_app.state.session_factory() as session:
        alpha_job = session.scalar(
            select(RefreshJob).where(RefreshJob.tenant_id == ALPHA_TENANT_ID)
        )
        assert alpha_job is not None
        alpha_job.status = "running"
        alpha_job.heartbeat_at = recovered_at - timedelta(minutes=2)
        alpha_job.leased_until = recovered_at - timedelta(seconds=1)
        session.commit()

        result = run_mock_worker_once(
            session,
            ALPHA_TENANT_ID,
            migrated_app.state.settings.refresh_policy,
            now=recovered_at,
        )
        assert result.job_id == alpha_job.id

    with migrated_app.state.session_factory() as session:
        alpha_job = session.scalar(
            select(RefreshJob).where(RefreshJob.tenant_id == ALPHA_TENANT_ID)
        )
        beta_job = session.scalar(select(RefreshJob).where(RefreshJob.tenant_id == BETA_TENANT_ID))
        assert alpha_job is not None and alpha_job.status == "completed"
        assert beta_job is not None and beta_job.status == "queued"
        assert (
            session.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.operation == "mock_refresh")
            )
            == 1
        )


def test_mock_worker_core_rejects_non_demo_tenant(migrated_app: FastAPI) -> None:
    with migrated_app.state.session_factory() as session:
        with pytest.raises(RuntimeError, match="fixed fictional Demo tenant"):
            run_mock_worker_once(
                session,
                uuid4(),
                migrated_app.state.settings.refresh_policy,
            )
