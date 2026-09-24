from sqlalchemy import select

from backend.app.models import Event, PersonalEventViewReceipt
from tests.integration.test_personal_changes_reports import (
    PERSONAL_HEADERS,
    SHARED_COMPANY_ID,
    _add_shared_event,
)


def test_rendered_v1_does_not_acknowledge_concurrent_v2(client, migrated_app):
    event_id = _add_shared_event(migrated_app, "semantic-concurrent")
    path = f"/api/v1/me/companies/{SHARED_COMPANY_ID}/view"
    assert client.post(path, headers=PERSONAL_HEADERS).status_code == 200
    with migrated_app.state.session_factory() as session:
        receipt = session.scalar(
            select(PersonalEventViewReceipt).where(PersonalEventViewReceipt.event_id == event_id)
        )
        v1 = receipt.semantic_version
        event = session.get(Event, event_id)
        event.risk_severity = "high"
        session.commit()
    current = client.post(
        path, headers=PERSONAL_HEADERS, json={"rendered_versions": {str(event_id): v1}}
    )
    assert current.status_code == 200
    assert str(event_id) in {v["id"] for v in current.json()["new_events"]}
    again = client.post(path, headers=PERSONAL_HEADERS).json()
    assert str(event_id) in {v["id"] for v in again["new_events"]}
    assert client.post(path, headers=PERSONAL_HEADERS).json()["new_events"] == []


def test_same_content_reuses_report_and_reuse_key_remains_idempotent(client, migrated_app):
    event_id = _add_shared_event(migrated_app, "semantic-report")
    path = f"/api/v1/me/companies/{SHARED_COMPANY_ID}/reports"
    one = client.post(path, headers=PERSONAL_HEADERS, json={"idempotency_key": "1" * 64})
    two = client.post(path, headers=PERSONAL_HEADERS, json={"idempotency_key": "2" * 64})
    assert one.status_code == two.status_code == 200
    assert two.json()["reused"] and one.json()["id"] == two.json()["id"]
    with migrated_app.state.session_factory() as session:
        session.get(Event, event_id).risk_severity = "high"
        session.commit()
    repeated = client.post(path, headers=PERSONAL_HEADERS, json={"idempotency_key": "2" * 64})
    assert repeated.json()["id"] == one.json()["id"]
    changed = client.post(path, headers=PERSONAL_HEADERS, json={"idempotency_key": "3" * 64})
    assert changed.json()["id"] != one.json()["id"]
    assert client.get("/api/v1/me/usage", headers=PERSONAL_HEADERS).json()["reports"]["used"] == 2


def test_explicit_archive_and_restricted_history(client, migrated_app):
    from backend.app.models import EventEvidence

    event_id = _add_shared_event(migrated_app, "semantic-restrict")
    path = f"/api/v1/me/companies/{SHARED_COMPANY_ID}/reports"
    first = client.post(path, headers=PERSONAL_HEADERS, json={"idempotency_key": "4" * 64}).json()
    archived = client.post(
        path,
        headers=PERSONAL_HEADERS,
        json={"idempotency_key": "5" * 64, "archive_new_timepoint": True},
    ).json()
    assert archived["id"] != first["id"]
    normal = client.get("/api/v1/me/reports/" + first["id"], headers=PERSONAL_HEADERS).json()
    assert normal["history_status"] == "historical_snapshot"
    assert normal["markdown"] == first["markdown"]
    assert "工商主体身份：待核验" in normal["markdown"]
    with migrated_app.state.session_factory() as session:
        for row in session.scalars(select(EventEvidence).where(EventEvidence.event_id == event_id)):
            row.display_allowed = False
            row.display_license_status = "restricted"
        session.commit()
    result = client.get("/api/v1/me/reports/" + first["id"], headers=PERSONAL_HEADERS)
    assert result.status_code == 200
    assert result.json()["history_status"] == "restricted"
    with migrated_app.state.session_factory() as session:
        from uuid import UUID

        from backend.app.models import PersonalCompanyReport

        assert session.get(PersonalCompanyReport, UUID(first["id"])).markdown == first["markdown"]


def test_legacy_receipt_migration_establishes_baseline_without_changing_first_seen(
    tmp_path, monkeypatch
):
    from uuid import uuid4

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from backend.app.config import Settings
    from backend.app.demo import NO_ACCESS_USER_ID
    from backend.app.main import create_app
    from backend.app.models import User
    from backend.app.personal_features import _unseen_shared_events
    from backend.app.providers import MockResearchProvider
    from backend.app.services import seed_demo_entities

    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "0035")
    app = create_app(
        Settings(
            database_url=url,
            app_mode="demo",
            external_calls_enabled=False,
            paid_api_calls_enabled=False,
            auto_refresh_enabled=False,
        )
    )
    try:
        with app.state.session_factory() as session:
            seed_demo_entities(session, MockResearchProvider().load())
            session.commit()
        event_id = _add_shared_event(app, "legacy-baseline")
        with app.state.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO personal_event_view_receipts "
                    "(id,owner_user_id,event_id,first_seen_at) VALUES (:id,:owner,:event,:seen)"
                ),
                {
                    "id": uuid4().hex,
                    "owner": NO_ACCESS_USER_ID.hex,
                    "event": event_id.hex,
                    "seen": "2026-01-01 00:00:00",
                },
            )
        command.upgrade(config, "head")
        with app.state.session_factory() as session:
            receipt = session.scalar(select(PersonalEventViewReceipt))
            assert receipt.semantic_version and receipt.first_seen_at.year == 2026
            assert receipt.first_seen_at.month == 1
            user = session.get(User, NO_ACCESS_USER_ID)
            assert event_id not in {
                e.id for e in _unseen_shared_events(session, user, SHARED_COMPANY_ID)
            }
            session.get(Event, event_id).risk_severity = "high"
            session.commit()
            assert event_id in {
                e.id for e in _unseen_shared_events(session, user, SHARED_COMPANY_ID)
            }
    finally:
        app.state.engine.dispose()


def test_company_and_report_keep_large_fixture_without_silent_truncation(client, migrated_app):
    expected = {_add_shared_event(migrated_app, f"volume-{n}") for n in range(100)}
    response = client.get(f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=PERSONAL_HEADERS)
    assert response.status_code == 200
    projected = response.json()["events"]
    assert {str(value) for value in expected} <= {event["id"] for event in projected}
    versions = {event["id"]: event["semantic_version"] for event in projected}
    viewed = client.post(
        f"/api/v1/me/companies/{SHARED_COMPANY_ID}/view",
        headers=PERSONAL_HEADERS,
        json={"rendered_versions": versions},
    )
    assert viewed.status_code == 200
    report = client.post(
        f"/api/v1/me/companies/{SHARED_COMPANY_ID}/reports",
        headers=PERSONAL_HEADERS,
        json={"idempotency_key": "6" * 64},
    )
    assert report.status_code == 200
    assert {str(value) for value in expected} <= set(report.json()["source_event_ids"])
