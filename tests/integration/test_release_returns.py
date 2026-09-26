from datetime import timedelta
from uuid import uuid4

from backend.app.config import WatchlistMonitorPolicy
from backend.app.demo import NO_ACCESS_USER_ID
from backend.app.models import CompanyResearchJob, CompanyWatchSchedule, Event, User, utc_now
from backend.app.watchlist_monitoring import company_semantic_version, record_outcome
from tests.integration.test_personal_changes_reports import (
    PERSONAL_HEADERS,
    SHARED_COMPANY_ID,
    _add_shared_event,
)


def test_equivalent_amount_keeps_read_receipt_and_watch_no_change_count(client, migrated_app):
    event_id = _add_shared_event(migrated_app, "release-amount")
    with migrated_app.state.session_factory() as session:
        event = session.get(Event, event_id)
        event.facts = [{"name": "融资金额", "value": "1亿元", "unit": None}]
        session.commit()
        user = session.get(User, NO_ACCESS_USER_ID)
        before = company_semantic_version(session, user, SHARED_COMPANY_ID)
    path = f"/api/v1/me/companies/{SHARED_COMPANY_ID}/view"
    assert client.post(path, headers=PERSONAL_HEADERS).status_code == 200
    with migrated_app.state.session_factory() as session:
        event = session.get(Event, event_id)
        event.facts = [{"name": "融资金额", "value": "1.00亿元", "unit": None}]
        now = utc_now()
        state = CompanyWatchSchedule(
            company_id=SHARED_COMPANY_ID,
            consecutive_no_change_runs=3,
            policy_version="release-fixture",
            next_check_at=now,
            last_successful_check_at=now - timedelta(days=7),
        )
        job = CompanyResearchJob(
            id=uuid4(),
            company_id=SHARED_COMPANY_ID,
            created_by_user_id=NO_ACCESS_USER_ID,
            trigger_type="watchlist",
            status="completed",
            current_stage="completed",
            policy_version="release-fixture",
            heartbeat_at=now,
            coverage={
                "semantic_before": before,
                "search_groups": {
                    "business_capital": {
                        "status": "completed",
                        "checked_at": now.isoformat(),
                        "providers": {"mock": {"status": "completed"}},
                    }
                },
            },
        )
        session.add_all([state, job])
        session.commit()
        user = session.get(User, NO_ACCESS_USER_ID)
        record_outcome(session, user, job, WatchlistMonitorPolicy())
        assert state.consecutive_no_change_runs == 4
    assert client.post(path, headers=PERSONAL_HEADERS).json()["new_events"] == []
    with migrated_app.state.session_factory() as session:
        session.get(Event, event_id).facts = [{"name": "融资金额", "value": "2亿元", "unit": None}]
        session.commit()
    assert str(event_id) in {
        e["id"] for e in client.post(path, headers=PERSONAL_HEADERS).json()["new_events"]
    }


def test_duplicate_source_does_not_make_unread_but_report_preserves_source_list(
    client, migrated_app
):
    from sqlalchemy import select

    from backend.app.models import EventEvidence

    event_id = _add_shared_event(migrated_app, "report-old-source")
    path = f"/api/v1/me/companies/{SHARED_COMPANY_ID}"
    client.post(path + "/view", headers=PERSONAL_HEADERS)
    first = client.post(
        path + "/reports", headers=PERSONAL_HEADERS, json={"idempotency_key": "a" * 64}
    ).json()
    duplicate_id = _add_shared_event(migrated_app, "report-new-source")
    with migrated_app.state.session_factory() as session:
        evidence = session.scalar(
            select(EventEvidence).where(EventEvidence.event_id == duplicate_id)
        )
        evidence.event_id = event_id
        session.delete(session.get(Event, duplicate_id))
        session.commit()
    assert client.post(path + "/view", headers=PERSONAL_HEADERS).json()["new_events"] == []
    second = client.post(
        path + "/reports", headers=PERSONAL_HEADERS, json={"idempotency_key": "b" * 64}
    ).json()
    assert first["id"] != second["id"]
    assert "report-new-source" in second["markdown"]
