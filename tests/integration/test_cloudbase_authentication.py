from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.auth import (
    AuthTokenSet,
    InvalidAuthenticationFlowError,
    VerificationChallenge,
    VerifiedIdentity,
)
from backend.app.config import CloudBaseAuthPolicy
from backend.app.demo import (
    ALPHA_FUND_ID,
    ALPHA_TENANT_ID,
    ALPHA_USER_ID,
    BETA_TENANT_ID,
    BETA_USER_ID,
    DEMO_SHARED_COMPANY_CREDIT_CODE,
    NO_ACCESS_USER_ID,
)
from backend.app.main import create_app
from backend.app.models import AuthenticationAuditLog, Role, Tenant, User, UserRoleAssignment


class FakeCloudBaseProvider:
    def __init__(self) -> None:
        self.identities = {
            "access-alpha": VerifiedIdentity(
                provider="cloudbase",
                subject="subject-alpha",
                email="alpha-admin@example.invalid",
            ),
            "access-alpha-2": VerifiedIdentity(
                provider="cloudbase",
                subject="subject-alpha",
                email="changed-email@example.invalid",
            ),
            "access-beta": VerifiedIdentity(
                provider="cloudbase",
                subject="subject-beta",
                email="beta-investor@example.invalid",
            ),
            "access-personal": VerifiedIdentity(
                provider="cloudbase",
                subject="subject-personal",
                email="no-access@example.invalid",
            ),
            "access-unknown": VerifiedIdentity(
                provider="cloudbase",
                subject="subject-unknown",
                email="unknown@example.invalid",
            ),
            "access-duplicate": VerifiedIdentity(
                provider="cloudbase",
                subject="subject-duplicate",
                email="duplicate@example.invalid",
            ),
        }
        self.signed_out: list[str] = []

    def send_email_code(self, email: str) -> VerificationChallenge:
        if email != "alpha-admin@example.invalid":
            raise InvalidAuthenticationFlowError
        return VerificationChallenge("verification-alpha", 600)

    def sign_in_with_email_code(self, verification_id: str, verification_code: str) -> AuthTokenSet:
        if (verification_id, verification_code) != ("verification-alpha", "123456"):
            raise InvalidAuthenticationFlowError
        return AuthTokenSet("access-alpha", "refresh-alpha", 7200)

    def refresh_tokens(self, refresh_token: str) -> AuthTokenSet:
        if refresh_token != "refresh-alpha":
            raise InvalidAuthenticationFlowError
        return AuthTokenSet("access-alpha-2", "refresh-alpha-2", 7200)

    def verify_access_token(self, access_token: str) -> VerifiedIdentity:
        try:
            return self.identities[access_token]
        except KeyError as error:
            raise InvalidAuthenticationFlowError from error

    def sign_out(self, access_token: str) -> None:
        self.signed_out.append(access_token)


def _cloudbase_app(migrated_app: FastAPI) -> tuple[FastAPI, FakeCloudBaseProvider]:
    provider = FakeCloudBaseProvider()
    settings = replace(
        migrated_app.state.settings,
        auth_provider="cloudbase",
        cloudbase_auth_policy=CloudBaseAuthPolicy(env_id="demo-env-123"),
    )
    return create_app(settings, identity_provider=provider), provider


def test_cloudbase_identity_replaces_forgeable_demo_header(migrated_app: FastAPI) -> None:
    app, _ = _cloudbase_app(migrated_app)
    with TestClient(app) as client:
        demo_only = client.get(
            "/api/v1/auth/me",
            headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
        )
        assert demo_only.status_code == 401

        alpha = client.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": "Bearer access-alpha",
                "X-Demo-User-Id": str(BETA_USER_ID),
            },
        )
        assert alpha.status_code == 200
        assert alpha.json()["user_id"] == str(ALPHA_USER_ID)
        assert alpha.json()["tenant_id"] == str(ALPHA_TENANT_ID)

        beta_reviews = client.get(
            "/api/v1/reviews",
            headers={
                "Authorization": "Bearer access-beta",
                "X-Demo-User-Id": str(ALPHA_USER_ID),
            },
        )
        assert beta_reviews.status_code == 403

    with app.state.session_factory() as session:
        alpha_user = session.get(User, ALPHA_USER_ID)
        beta_user = session.get(User, BETA_USER_ID)
        assert alpha_user is not None
        assert beta_user is not None
        assert (alpha_user.auth_provider, alpha_user.auth_subject) == (
            "cloudbase",
            "subject-alpha",
        )
        assert (beta_user.auth_provider, beta_user.auth_subject) == (
            "cloudbase",
            "subject-beta",
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuthenticationAuditLog)
                .where(AuthenticationAuditLog.event_type == "identity_linked")
            )
            == 2
        )
    app.state.engine.dispose()


def test_subject_mapping_survives_email_change_and_unknown_user_is_denied(
    migrated_app: FastAPI,
) -> None:
    app, _ = _cloudbase_app(migrated_app)
    with TestClient(app) as client:
        first = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer access-alpha"},
        )
        changed = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer access-alpha-2"},
        )
        unknown = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer access-unknown"},
        )

    assert first.status_code == 200
    assert changed.status_code == 200
    assert changed.json()["user_id"] == str(ALPHA_USER_ID)
    assert unknown.status_code == 403
    assert unknown.json()["detail"] == "invitation_required"
    app.state.engine.dispose()


def test_ambiguous_cross_tenant_email_fails_closed(migrated_app: FastAPI) -> None:
    with migrated_app.state.session_factory() as session:
        session.add_all(
            [
                User(
                    id=uuid4(),
                    tenant_id=ALPHA_TENANT_ID,
                    email="duplicate@example.invalid",
                    display_name="重复邮箱甲",
                    status="active",
                ),
                User(
                    id=uuid4(),
                    tenant_id=BETA_TENANT_ID,
                    email="duplicate@example.invalid",
                    display_name="重复邮箱乙",
                    status="active",
                ),
            ]
        )
        session.commit()

    app, _ = _cloudbase_app(migrated_app)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer access-duplicate"},
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "invitation_required"
    app.state.engine.dispose()


def test_email_login_refresh_logout_and_audit(migrated_app: FastAPI) -> None:
    app, provider = _cloudbase_app(migrated_app)
    with TestClient(app) as client:
        challenge = client.post(
            "/api/v1/auth/email/verification",
            json={"email": "alpha-admin@example.invalid"},
        )
        assert challenge.status_code == 200
        assert challenge.json() == {
            "verification_id": "verification-alpha",
            "expires_in": 600,
        }

        login = client.post(
            "/api/v1/auth/email/login",
            json={
                "verification_id": "verification-alpha",
                "verification_code": "123456",
            },
        )
        assert login.status_code == 200
        assert login.json()["access_token"] == "access-alpha"

        refreshed = client.post(
            "/api/v1/auth/token/refresh",
            json={"refresh_token": "refresh-alpha"},
        )
        assert refreshed.status_code == 200
        assert refreshed.json()["access_token"] == "access-alpha-2"

        logout = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": "Bearer access-alpha-2"},
        )
        assert logout.status_code == 204
        assert provider.signed_out == ["access-alpha-2"]

    with app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None and user.last_login_at is not None
        event_types = list(
            session.scalars(
                select(AuthenticationAuditLog.event_type)
                .where(AuthenticationAuditLog.user_id == ALPHA_USER_ID)
                .order_by(AuthenticationAuditLog.created_at, AuthenticationAuditLog.event_type)
            )
        )
        assert sorted(event_types) == sorted(
            ["identity_linked", "session_started", "session_refreshed", "session_ended"]
        )
    app.state.engine.dispose()


def test_uninvited_email_verification_does_not_disclose_account_existence(
    migrated_app: FastAPI,
) -> None:
    app, _ = _cloudbase_app(migrated_app)
    with TestClient(app) as client:
        invited = client.post(
            "/api/v1/auth/email/verification",
            json={"email": "alpha-admin@example.invalid"},
        )
        uninvited = client.post(
            "/api/v1/auth/email/verification",
            json={"email": "unknown@example.invalid"},
        )
    assert invited.status_code == 200
    assert uninvited.status_code == 200
    assert invited.json()["expires_in"] == uninvited.json()["expires_in"] == 600
    assert len(uninvited.json()["verification_id"]) >= 8
    app.state.engine.dispose()


def test_inactive_local_tenant_is_denied(migrated_app: FastAPI) -> None:
    with migrated_app.state.session_factory() as session:
        tenant = session.get(Tenant, BETA_TENANT_ID)
        assert tenant is not None
        tenant.status = "inactive"
        session.commit()

    app, _ = _cloudbase_app(migrated_app)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer access-beta"},
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "invitation_required"
    app.state.engine.dispose()


def test_cloudbase_identity_preserves_four_local_authorization_personas(
    migrated_app: FastAPI,
) -> None:
    platform_user_id = uuid4()
    with migrated_app.state.session_factory() as session:
        platform_role = session.scalar(select(Role).where(Role.code == "platform_admin"))
        assert platform_role is not None
        session.add(
            User(
                id=platform_user_id,
                tenant_id=ALPHA_TENANT_ID,
                email="platform-admin@example.invalid",
                display_name="CloudBase 平台管理员测试",
                status="active",
            )
        )
        session.add(
            UserRoleAssignment(
                id=uuid4(),
                user_id=platform_user_id,
                role_id=platform_role.id,
                scope_id=None,
                valid_until=None,
            )
        )
        session.commit()

    app, provider = _cloudbase_app(migrated_app)
    provider.identities["access-platform"] = VerifiedIdentity(
        provider="cloudbase",
        subject="subject-platform",
        email="platform-admin@example.invalid",
    )
    with TestClient(app) as client:
        personal_search = client.get(
            "/api/v1/companies/search",
            params={"q": DEMO_SHARED_COMPANY_CREDIT_CODE},
            headers={"Authorization": "Bearer access-personal"},
        )
        assert personal_search.status_code == 200
        assert len(personal_search.json()) == 1
        shared_company_id = personal_search.json()[0]["id"]
        personal_detail = client.get(
            f"/api/v1/companies/{shared_company_id}",
            headers={"Authorization": "Bearer access-personal"},
        )
        assert personal_detail.status_code == 200
        assert personal_detail.json()["investments"] == []

        fund_companies = client.get(
            "/api/v1/companies",
            headers={"Authorization": "Bearer access-alpha"},
        )
        assert fund_companies.status_code == 200
        assert fund_companies.json()
        fund_detail = client.get(
            f"/api/v1/companies/{fund_companies.json()[0]['id']}",
            headers={"Authorization": "Bearer access-alpha"},
        )
        assert fund_detail.status_code == 200
        assert any(
            item["fund_id"] == str(ALPHA_FUND_ID) for item in fund_detail.json()["investments"]
        )

        other_tenant = client.get(
            "/api/v1/companies",
            headers={"Authorization": "Bearer access-beta"},
        )
        assert other_tenant.status_code == 200
        assert other_tenant.json()
        assert (
            client.get(
                "/api/v1/reviews",
                headers={"Authorization": "Bearer access-beta"},
            ).status_code
            == 403
        )

        platform_sources = client.get(
            "/api/v1/trusted-sources",
            headers={"Authorization": "Bearer access-platform"},
        )
        assert platform_sources.status_code == 200

    with app.state.session_factory() as session:
        assert session.get(User, NO_ACCESS_USER_ID).auth_subject == "subject-personal"
        assert session.get(User, platform_user_id).auth_subject == "subject-platform"
    app.state.engine.dispose()
