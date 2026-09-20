from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from backend.app.config import WebResearchPolicy
from backend.app.demo import BETA_USER_ID
from backend.app.models import CompanySnapshot, PersonalCompanyRequest, PersonalUsageRecord, utc_now
from tests.integration.test_personal_company_query import (
    ALPHA_HEADERS,
    BETA_HEADERS,
    SHARED_COMPANY_ID,
    _ingest_and_publish_shared_event,
)


def test_visit_returns_existing_content_and_queues_without_external_calls(
    client, migrated_app, monkeypatch
):
    _ingest_and_publish_shared_event(client, migrated_app)
    migrated_app.state.settings = replace(
        migrated_app.state.settings,
        auto_refresh_enabled=True,
        web_research_policy=WebResearchPolicy(
            incremental_research_enabled=True, topic_planning_enabled=True
        ),
    )
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda *_: pytest.fail("external call")
    )
    with migrated_app.state.session_factory() as session:
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == SHARED_COMPANY_ID,
                CompanySnapshot.is_current.is_(True),
            )
        )
        snapshot.last_checked_at = utc_now() - timedelta(days=30)
        session.commit()
    path = f"/api/v1/companies/{SHARED_COMPANY_ID}"
    first = client.get(path, headers=BETA_HEADERS)
    assert first.status_code == 200
    assert first.json()["events"]
    assert first.json()["automatic_refresh"]["status"] == "queued"
    second = client.get(path, headers=BETA_HEADERS)
    assert (
        second.json()["automatic_refresh"]["request_id"]
        == first.json()["automatic_refresh"]["request_id"]
    )
    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PersonalCompanyRequest)) == 1
    assert client.get(path).status_code == 401
    assert (
        client.get(
            "/api/v1/companies/00000000-0000-0000-0000-000000000000", headers=ALPHA_HEADERS
        ).status_code
        == 404
    )


def test_fresh_visit_and_quota_deferred_visit_preserve_content(client, migrated_app):
    _ingest_and_publish_shared_event(client, migrated_app)
    migrated_app.state.settings = replace(
        migrated_app.state.settings,
        auto_refresh_enabled=True,
        web_research_policy=WebResearchPolicy(
            incremental_research_enabled=True, topic_planning_enabled=True
        ),
    )
    path = f"/api/v1/companies/{SHARED_COMPANY_ID}"
    with migrated_app.state.session_factory() as session:
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == SHARED_COMPANY_ID,
                CompanySnapshot.is_current.is_(True),
            )
        )
        snapshot.last_checked_at = utc_now()
        session.commit()
    response = client.get(path, headers=BETA_HEADERS)
    assert response.json()["automatic_refresh"]["status"] == "not_due"
    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PersonalCompanyRequest)) == 0

    policy = replace(migrated_app.state.settings.personal_entitlement_policy, daily_request_limit=1)
    migrated_app.state.settings = replace(
        migrated_app.state.settings, personal_entitlement_policy=policy
    )
    with migrated_app.state.session_factory() as session:
        snapshot = session.scalar(
            select(CompanySnapshot).where(
                CompanySnapshot.company_id == SHARED_COMPANY_ID,
                CompanySnapshot.is_current.is_(True),
            )
        )
        snapshot.last_checked_at = utc_now() - timedelta(days=30)
        session.add(
            PersonalUsageRecord(
                owner_user_id=BETA_USER_ID,
                operation="company_request",
                period_key=utc_now().strftime("%Y-%m"),
                idempotency_key="quota-fixture",
            )
        )
        session.commit()
    limited = client.get(path, headers=BETA_HEADERS)
    assert limited.status_code == 200 and limited.json()["events"] == response.json()["events"]
    assert limited.json()["automatic_refresh"]["status"] == "budget_deferred"
    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PersonalCompanyRequest)) == 0
