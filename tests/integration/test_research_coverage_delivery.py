from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.config import PersonalEntitlementPolicy, Settings
from backend.app.database import build_session_factory, set_request_context
from backend.app.demo import ALPHA_USER_ID, BETA_USER_ID, NO_ACCESS_USER_ID
from backend.app.main import create_app
from backend.app.models import CompanyResearchJob, UsageLedger, User
from backend.app.personal_features import create_refresh_request
from tests.integration.test_bounded_web_research import (
    SHARED_COMPANY_ID,
    RecordingFetcherFactory,
    _drain,
    _grant_platform_admin,
    _providers,
)
from tests.integration.test_tender_storage import database as database


def test_worker_coverage_and_owner_only_reading_under_database_permissions(database):
    app = create_app(
        Settings(
            database_url=database.app.url.render_as_string(hide_password=False),
            app_mode="demo",
            external_calls_enabled=False,
            paid_api_calls_enabled=False,
            auto_refresh_enabled=False,
        )
    )
    # Fixture setup uses the owner; all worker/API operations use the application role.
    application_factory = app.state.session_factory
    app.state.session_factory = build_session_factory(database.owner)
    _grant_platform_admin(app)
    app.state.session_factory = application_factory
    try:
        with application_factory() as session:
            owner = session.get(User, NO_ACCESS_USER_ID)
            set_request_context(session, owner.id, owner.tenant_id)
            create_refresh_request(session, owner, PersonalEntitlementPolicy(), SHARED_COMPANY_ID)
        primary, fallback = _providers()
        fetcher = RecordingFetcherFactory()
        assert _drain(app, {"baidu": primary, "bocha": fallback}, fetcher)[-1] == "idle"
        calls = (len(primary.calls), len(fallback.calls), len(fetcher.requests))
        assert calls == (2, 0, 4)
        with build_session_factory(database.owner)() as session:
            original = session.scalar(select(CompanyResearchJob)).coverage
            ledger_before = session.scalar(select(func.sum(UsageLedger.external_calls)))
        with TestClient(app) as client:
            for _ in range(2):
                response = client.get(
                    f"/api/v1/companies/{SHARED_COMPANY_ID}",
                    headers={
                        "X-Demo-User-Id": str(NO_ACCESS_USER_ID),
                    },
                )
                assert response.status_code == 200
                body = response.json()
                rows = body["personal_research_result"]["category_coverage"]
                assert len(rows) == 9
                assert (
                    next(row for row in rows if row["category"] == "contract_commercial")["status"]
                    == "evidence_obtained"
                )
                serialized = str(body["personal_research_result"])
                assert "news.example.com" not in serialized
                assert "document_id" not in serialized and "source_routes" not in serialized
                assert body["investments"] == []
            requests = client.get(
                "/api/v1/me/company-requests",
                headers={
                    "X-Demo-User-Id": str(NO_ACCESS_USER_ID),
                },
            ).json()
            assert requests[0]["research_result"]["category_coverage"] == rows
            for user_id in (ALPHA_USER_ID, BETA_USER_ID):
                other = client.get(
                    f"/api/v1/companies/{SHARED_COMPANY_ID}",
                    headers={
                        "X-Demo-User-Id": str(user_id),
                    },
                )
                assert other.status_code == 200
                assert other.json()["personal_research_result"] is None
        assert (len(primary.calls), len(fallback.calls), len(fetcher.requests)) == calls
        with build_session_factory(database.owner)() as session:
            assert session.scalar(select(CompanyResearchJob)).coverage == original
            assert session.scalar(select(func.sum(UsageLedger.external_calls))) == ledger_before
    finally:
        app.state.engine.dispose()
