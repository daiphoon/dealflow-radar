from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.config import PersonalEntitlementPolicy
from backend.app.demo import (
    ALPHA_USER_ID,
    BETA_USER_ID,
    NO_ACCESS_USER_ID,
    demo_uuid,
)
from backend.app.models import (
    Company,
    PersonalCompanyRequest,
    PersonalUsageRecord,
    PersonalWatchlistItem,
    Role,
    UsageLedger,
    UserRoleAssignment,
)

PERSONAL_HEADERS = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}
ALPHA_HEADERS = {"X-Demo-User-Id": str(ALPHA_USER_ID)}
BETA_HEADERS = {"X-Demo-User-Id": str(BETA_USER_ID)}
SHARED_COMPANY_ID = demo_uuid("company-示例星河科技一号有限公司")


def _set_limits(app: FastAPI, *, searches: int = 2, watchlist: int = 1, requests: int = 2) -> None:
    app.state.settings = replace(
        app.state.settings,
        personal_entitlement_policy=PersonalEntitlementPolicy(
            monthly_search_limit=searches,
            watchlist_company_limit=watchlist,
            monthly_report_limit=1,
            monthly_request_limit=requests,
            request_cooldown_hours=24,
        ),
    )


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


def test_search_and_watchlist_limits_are_server_enforced_and_user_private(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _set_limits(migrated_app)
    with migrated_app.state.session_factory() as session:
        second_shared = Company(
            id=uuid4(),
            tenant_id=None,
            credit_code="91310000MA1K000099",
            legal_name="示例共享关注上限公司",
            registered_region="虚构省测试市",
            identity_status="verified",
            visibility_scope="public",
        )
        private_company = Company(
            id=uuid4(),
            tenant_id=demo_uuid("tenant-alpha"),
            credit_code="PRIVATE-WATCHLIST-TEST",
            legal_name="机构私有关注测试公司",
            registered_region="虚构省测试市",
            identity_status="verified",
            visibility_scope="tenant",
        )
        session.add_all([second_shared, private_company])
        session.commit()
        second_shared_id = second_shared.id
        private_company_id = private_company.id
        usage_ledger_before = session.scalar(select(func.count()).select_from(UsageLedger))

    for query in ("示例星河科技一号有限公司", "91310000MA1K000006"):
        response = client.get(
            "/api/v1/companies/search",
            params={"q": query},
            headers=PERSONAL_HEADERS,
        )
        assert response.status_code == 200
        assert [item["id"] for item in response.json()] == [str(SHARED_COMPANY_ID)]
    blocked_search = client.get(
        "/api/v1/companies/search",
        params={"q": "星河一号"},
        headers=PERSONAL_HEADERS,
    )
    assert blocked_search.status_code == 429
    assert blocked_search.json()["detail"] == {
        "code": "personal_usage_limit_reached",
        "feature": "company_search",
        "limit": 2,
    }

    added = client.post(f"/api/v1/me/watchlist/{SHARED_COMPANY_ID}", headers=PERSONAL_HEADERS)
    duplicate = client.post(f"/api/v1/me/watchlist/{SHARED_COMPANY_ID}", headers=PERSONAL_HEADERS)
    assert added.status_code == duplicate.status_code == 200
    assert added.json()["id"] == duplicate.json()["id"]
    assert added.json()["legal_name"] == "示例星河科技一号有限公司"

    limit_reached = client.post(
        f"/api/v1/me/watchlist/{second_shared_id}", headers=PERSONAL_HEADERS
    )
    assert limit_reached.status_code == 429
    hidden = client.post(f"/api/v1/me/watchlist/{private_company_id}", headers=PERSONAL_HEADERS)
    assert hidden.status_code == 404
    assert client.get("/api/v1/me/watchlist", headers=BETA_HEADERS).json() == []

    usage = client.get("/api/v1/me/usage", headers=PERSONAL_HEADERS)
    assert usage.status_code == 200
    assert usage.json()["searches"] == {"used": 2, "limit": 2, "remaining": 0}
    assert usage.json()["watchlist_companies"] == {
        "used": 1,
        "limit": 1,
        "remaining": 0,
    }
    assert usage.json()["reports"] == {"used": 0, "limit": 1, "remaining": 1}

    removed = client.delete(f"/api/v1/me/watchlist/{SHARED_COMPANY_ID}", headers=PERSONAL_HEADERS)
    assert removed.status_code == 204
    assert client.get("/api/v1/me/watchlist", headers=PERSONAL_HEADERS).json() == []

    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PersonalWatchlistItem)) == 0
        assert session.scalar(select(func.count()).select_from(PersonalUsageRecord)) == 2
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == usage_ledger_before


def test_inclusion_and_refresh_requests_are_queued_deduplicated_and_admin_reviewable(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    _set_limits(migrated_app, requests=2)
    with migrated_app.state.session_factory() as session:
        company_count = session.scalar(select(func.count()).select_from(Company))

    inclusion_payload = {
        "company_name": "尚未收录的示例真实主体",
        "credit_code": "91310000TESTM40001",
    }
    created = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=PERSONAL_HEADERS,
        json=inclusion_payload,
    )
    duplicate = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=PERSONAL_HEADERS,
        json=inclusion_payload,
    )
    assert created.status_code == duplicate.status_code == 200
    assert created.json()["request_type"] == "inclusion"
    assert created.json()["status"] == "pending"
    assert created.json()["reused"] is False
    assert duplicate.json()["id"] == created.json()["id"]
    assert duplicate.json()["reused"] is True

    refresh = client.post(
        f"/api/v1/me/company-requests/refresh/{SHARED_COMPANY_ID}",
        headers=PERSONAL_HEADERS,
    )
    refresh_duplicate = client.post(
        f"/api/v1/me/company-requests/refresh/{SHARED_COMPANY_ID}",
        headers=PERSONAL_HEADERS,
    )
    assert refresh.status_code == refresh_duplicate.status_code == 200
    assert refresh.json()["request_type"] == "refresh"
    assert refresh_duplicate.json()["reused"] is True

    limited = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=PERSONAL_HEADERS,
        json={"company_name": "第三家待收录公司"},
    )
    assert limited.status_code == 429
    assert limited.json()["detail"]["feature"] == "company_request"

    existing_company = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=BETA_HEADERS,
        json={"credit_code": "91310000MA1K000006"},
    )
    assert existing_company.status_code == 409
    assert existing_company.json()["detail"] == "company already available"
    existing_alias = client.post(
        "/api/v1/me/company-requests/inclusion",
        headers=BETA_HEADERS,
        json={"company_name": "星河一号"},
    )
    assert existing_alias.status_code == 409
    assert existing_alias.json()["detail"] == "company already available"

    own_requests = client.get("/api/v1/me/company-requests", headers=PERSONAL_HEADERS)
    assert own_requests.status_code == 200
    assert {item["request_type"] for item in own_requests.json()} == {"inclusion", "refresh"}
    assert client.get("/api/v1/me/company-requests", headers=BETA_HEADERS).json() == []
    assert client.get("/api/v1/platform/company-requests", headers=ALPHA_HEADERS).status_code == 403

    _grant_platform_admin(migrated_app)
    admin_queue = client.get("/api/v1/platform/company-requests", headers=ALPHA_HEADERS)
    assert admin_queue.status_code == 200
    assert {item["id"] for item in admin_queue.json()} == {
        created.json()["id"],
        refresh.json()["id"],
    }
    reviewed = client.patch(
        f"/api/v1/platform/company-requests/{created.json()['id']}",
        headers=ALPHA_HEADERS,
        json={"status": "completed", "reason": "已完成受控人工收录处理"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "completed"
    assert reviewed.json()["reviewed_by_id"] == str(ALPHA_USER_ID)
    closed_again = client.patch(
        f"/api/v1/platform/company-requests/{created.json()['id']}",
        headers=ALPHA_HEADERS,
        json={"status": "rejected", "reason": "尝试重复关闭请求"},
    )
    assert closed_again.status_code == 409

    with migrated_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Company)) == company_count
        assert session.scalar(select(func.count()).select_from(PersonalCompanyRequest)) == 2
        request_usage_count = session.scalar(
            select(func.count())
            .select_from(PersonalUsageRecord)
            .where(PersonalUsageRecord.operation == "company_request")
        )
        assert request_usage_count == 2

    settings = migrated_app.state.settings
    assert settings.external_calls_enabled is False
    assert settings.paid_api_calls_enabled is False
    assert settings.auto_refresh_enabled is False
    assert settings.publication_policy.enabled is False
