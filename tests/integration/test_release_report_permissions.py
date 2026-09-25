from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models import Event, EventEvidence, PersonalCompanyReport, RawDocument, Source
from tests.integration.test_personal_changes_reports import (
    PERSONAL_HEADERS,
    SHARED_COMPANY_ID,
    _add_shared_event,
)
from tests.integration.test_tender_storage import database as database


@pytest.mark.parametrize(
    "loss",
    [
        "source_license",
        "document_license",
        "invisible_evidence",
        "missing_evidence",
        "all_withdrawn",
    ],
)
def test_history_cannot_emit_markdown_after_evidence_permission_loss(database, loss):
    app = create_app(
        Settings(
            database_url=database.app.url.render_as_string(hide_password=False),
            app_mode="demo",
            external_calls_enabled=False,
            paid_api_calls_enabled=False,
            auto_refresh_enabled=False,
        )
    )
    # Owner prepares a fictitious legacy event; all reads below use the application role.
    owner_app = create_app(
        Settings(
            database_url=database.owner.url.render_as_string(hide_password=False),
            app_mode="demo",
            external_calls_enabled=False,
            paid_api_calls_enabled=False,
            auto_refresh_enabled=False,
        )
    )
    try:
        event_id = _add_shared_event(owner_app, "release-" + loss)
        with TestClient(app) as client:
            path = f"/api/v1/me/companies/{SHARED_COMPANY_ID}/reports"
            first = client.post(path, headers=PERSONAL_HEADERS, json={"idempotency_key": "c" * 64})
            assert first.status_code == 200
            saved = first.json()
            assert str(event_id) in saved["source_event_ids"]
            with owner_app.state.session_factory() as session:
                evidence = session.scalar(
                    select(EventEvidence).where(EventEvidence.event_id == event_id)
                )
                document = session.get(RawDocument, evidence.raw_document_id)
                if loss == "source_license":
                    session.get(Source, document.source_id).license_status = "restricted"
                elif loss == "document_license":
                    document.license_status = "restricted"
                elif loss == "invisible_evidence":
                    evidence.visibility_scope = "personal_private"
                    from backend.app.demo import BETA_USER_ID

                    evidence.owner_user_id = BETA_USER_ID
                elif loss == "missing_evidence":
                    session.execute(delete(EventEvidence).where(EventEvidence.event_id == event_id))
                else:
                    evidence.display_allowed = False
                    evidence.display_license_status = "restricted"
                    session.get(Event, event_id).status = "retracted"
                session.commit()
            result = client.get("/api/v1/me/reports/" + saved["id"], headers=PERSONAL_HEADERS)
            assert result.status_code == 200
            assert result.json()["history_status"] == "restricted"
            assert result.json()["markdown"] != saved["markdown"]
            with owner_app.state.session_factory() as session:
                assert (
                    session.get(PersonalCompanyReport, UUID(saved["id"])).markdown
                    == saved["markdown"]
                )
    finally:
        app.state.engine.dispose()
        owner_app.state.engine.dispose()


def test_hidden_one_of_multiple_sources_cannot_leave_old_citation_online(client, migrated_app):
    first_id = _add_shared_event(migrated_app, "multi-source-a")
    second_id = _add_shared_event(migrated_app, "multi-source-b")
    with migrated_app.state.session_factory() as session:
        extra = session.scalar(select(EventEvidence).where(EventEvidence.event_id == second_id))
        extra.event_id = first_id
        session.delete(session.get(Event, second_id))
        session.commit()
    path = f"/api/v1/me/companies/{SHARED_COMPANY_ID}/reports"
    saved = client.post(path, headers=PERSONAL_HEADERS, json={"idempotency_key": "d" * 64}).json()
    with migrated_app.state.session_factory() as session:
        evidence = session.scalar(select(EventEvidence).where(EventEvidence.event_id == first_id))
        session.delete(evidence)
        session.commit()
    response = client.get("/api/v1/me/reports/" + saved["id"], headers=PERSONAL_HEADERS)
    assert response.json()["history_status"] == "restricted"
