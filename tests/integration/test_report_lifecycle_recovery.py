"""提交后独立读取及复用的行为合约；同一矩阵在 SQLite/非 owner PostgreSQL 上执行。"""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models import (
    Event,
    EventEvidence,
    PersonalCompanyReport,
    PersonalReportRequest,
    PersonalUsageRecord,
    RawDocument,
    Source,
)
from tests.integration.test_personal_changes_reports import (
    BETA_HEADERS,
    PERSONAL_HEADERS,
    SHARED_COMPANY_ID,
    _add_shared_event,
    _set_report_limit,
)
from tests.integration.test_tender_storage import database as database


@pytest.fixture
def apps(database):
    settings = dict(
        app_mode="demo",
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
    )
    owner = create_app(
        Settings(database_url=database.owner.url.render_as_string(hide_password=False), **settings)
    )
    app = create_app(
        Settings(database_url=database.app.url.render_as_string(hide_password=False), **settings)
    )
    yield owner, app
    owner.state.engine.dispose()
    app.state.engine.dispose()


def evidence(owner, suffix, representation="legacy", license="synthetic_demo"):
    event_id = _add_shared_event(owner, suffix)
    with owner.state.session_factory() as session:
        row = session.scalar(select(EventEvidence).where(EventEvidence.event_id == event_id))
        doc = session.get(RawDocument, row.raw_document_id)
        source = session.get(Source, doc.source_id)
        source.name = "虚构来源 " + suffix
        doc.license_status = source.license_status = license
        if representation == "display":
            row.display_allowed = True
            row.display_license_status = license
            row.display_source_name = source.name
            row.display_source_quality = source.source_quality
            row.display_title = doc.title
            row.display_canonical_url = doc.canonical_url
            row.display_observed_at = doc.observed_at
            row.display_url_health_status = "healthy"
            row.display_detail_payload = (
                {
                    "schema_version": "demo-evidence-v1",
                    "synthetic_demo": True,
                    "before": "虚构前值",
                    "after": "虚构后值",
                }
                if license == "synthetic_demo"
                else {}
            )
        session.commit()
        return event_id, row.id


def counts(owner):
    with owner.state.session_factory() as s:
        return tuple(
            s.scalar(select(func.count()).select_from(m))
            for m in (PersonalCompanyReport, PersonalReportRequest, PersonalUsageRecord)
        )


def post(client, key="a", **kwargs):
    return client.post(
        f"/api/v1/me/companies/{SHARED_COMPANY_ID}/reports",
        headers=PERSONAL_HEADERS,
        json={"idempotency_key": key * 64, **kwargs},
    )


@pytest.mark.parametrize(
    "representation,license",
    [
        ("legacy", "synthetic_demo"),
        ("display", "synthetic_demo"),
        ("mixed", "synthetic_demo"),
        ("legacy", "public"),
        ("display", "public"),
        ("legacy", "permission_confirmed"),
        ("display", "permission_confirmed"),
        ("empty", "public"),
        ("filtered", "public"),
    ],
)
def test_committed_lifecycle(apps, representation, license):
    owner, app = apps
    _set_report_limit(app, 1)
    expected = []
    if representation == "filtered":
        excluded, _ = evidence(owner, "filtered", "legacy", "public")
        with owner.state.session_factory() as session:
            session.get(Event, excluded).fingerprint_version = "tender-v1"
            session.commit()
    elif representation != "empty":
        for r in ["legacy", "display"] if representation == "mixed" else [representation]:
            expected.append(evidence(owner, r + license, r, license)[0])
    before = counts(owner)
    with TestClient(app) as client:
        created = post(client)
        assert created.status_code == 200, created.text
        saved = created.json()
        assert saved["history_status"] == "historical_snapshot"
        if representation in {"empty", "filtered"}:
            assert saved["source_event_count"] == 0
            assert "暂无已审核的重要信息" in saved["markdown"]
        for event_id in expected:
            assert str(event_id) in saved["source_event_ids"]
        # Each HTTP request uses a new session after the previous request committed.
        fetched = client.get("/api/v1/me/reports/" + saved["id"], headers=PERSONAL_HEADERS).json()
        assert fetched["history_status"] == "historical_snapshot"
        assert fetched["markdown"] == saved["markdown"]
        if license == "synthetic_demo":
            assert "虚构演示" in fetched["markdown"]
        for key in ["a", "b", "b"]:
            repeated = post(client, key).json()
            assert repeated["id"] == saved["id"]
            assert repeated["reused"] and repeated["markdown"] == saved["markdown"]
            assert repeated["history_status"] == "historical_snapshot"
        assert (
            client.get("/api/v1/me/reports/" + saved["id"], headers=BETA_HEADERS).status_code == 404
        )
        quota = post(client, "c", archive_new_timepoint=True)
        assert quota.status_code == 429
    after = counts(owner)
    assert after == (before[0] + 1, before[1] + 1, before[2] + 1)
    with owner.state.session_factory() as session:
        stored = session.get(PersonalCompanyReport, UUID(saved["id"]))
        assert stored.markdown == saved["markdown"] and stored.content_hash == saved["content_hash"]


@pytest.mark.parametrize(
    "loss",
    [
        "source",
        "document",
        "unknown",
        "missing_license",
        "fake_demo",
        "missing_key",
        "missing_metadata",
        "display_unknown",
        "revoked",
    ],
)
def test_initial_undeliverable_report_has_no_success_or_charge(apps, loss):
    owner, app = apps
    _, eid = evidence(
        owner,
        loss,
        "display"
        if loss in {"fake_demo", "missing_key", "missing_metadata", "display_unknown", "revoked"}
        else "legacy",
    )
    with owner.state.session_factory() as s:
        e = s.get(EventEvidence, eid)
        d = s.get(RawDocument, e.raw_document_id)
        if loss == "source":
            s.get(Source, d.source_id).license_status = "restricted"
        elif loss == "document":
            d.license_status = "restricted"
        elif loss == "unknown":
            d.license_status = "unknown"
        elif loss == "missing_license":
            e.display_allowed = True
            e.display_license_status = None
        elif loss == "display_unknown":
            e.display_license_status = "unknown"
        elif loss == "missing_metadata":
            e.display_source_name = None
        elif loss == "fake_demo":
            d.license_status = "unknown"
        elif loss == "missing_key":
            e.display_detail_payload = {}
        else:
            e.display_license_status = "restricted"
        s.commit()
    before = counts(owner)
    with TestClient(app) as client:
        result = post(client)
        assert result.status_code == 403, result.text
        assert result.json()["detail"] == "report_content_restricted"
    assert counts(owner) == before


@pytest.mark.parametrize(
    "loss", ["source", "document", "hidden", "deleted", "revoked", "corrected", "retracted"]
)
def test_history_and_retries_recheck_permission_without_rewriting(apps, loss):
    owner, app = apps
    event_id, eid = evidence(owner, "kept", "legacy", "public")
    # Two different sources on a single event; one surviving source is insufficient.
    other_event, other_eid = evidence(owner, "other", "display", "synthetic_demo")
    with owner.state.session_factory() as s:
        s.get(EventEvidence, other_eid).event_id = event_id
        s.delete(s.get(Event, other_event))
        s.commit()
    with TestClient(app) as client:
        saved = post(client).json()
        before = counts(owner)
        with owner.state.session_factory() as s:
            e = s.get(EventEvidence, eid)
            d = s.get(RawDocument, e.raw_document_id)
            if loss == "source":
                s.get(Source, d.source_id).license_status = "restricted"
            elif loss == "document":
                d.license_status = "restricted"
            elif loss == "hidden":
                e.visibility_scope = "personal_private"
                e.owner_user_id = UUID(BETA_HEADERS["X-Demo-User-Id"])
            elif loss == "deleted":
                s.delete(e)
            elif loss == "revoked":
                e.display_license_status = "restricted"
            else:
                s.get(Event, event_id).status = loss
            s.commit()
        expected_status = "stale" if loss in {"corrected", "retracted"} else "restricted"
        for response in [
            client.get("/api/v1/me/reports/" + saved["id"], headers=PERSONAL_HEADERS),
            post(client),
        ]:
            data = response.json()
            assert data["history_status"] == expected_status
            assert (data["markdown"] == saved["markdown"]) == (expected_status == "stale")
        if loss in {"source", "document", "revoked"}:
            different_key = post(client, "b")
            assert different_key.status_code == 403 or (
                different_key.status_code == 200
                and different_key.json()["history_status"] == "restricted"
            )
        assert counts(owner) == before
        with owner.state.session_factory() as s:
            stored = s.get(PersonalCompanyReport, UUID(saved["id"]))
            assert (
                stored.markdown == saved["markdown"]
                and stored.content_hash == saved["content_hash"]
            )


def test_concurrent_same_key_charges_once(apps, database):
    if not database.postgres:
        pytest.skip("row-lock concurrency requires PostgreSQL")
    owner, app = apps
    evidence(owner, "concurrent", "display")
    _set_report_limit(app, 1)
    before = counts(owner)

    def generate(_):
        with TestClient(app) as client:
            response = post(client)
            assert response.status_code == 200, response.text
            assert response.json()["history_status"] == "historical_snapshot"
            return response.json()["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(set(pool.map(generate, range(4)))) == 1
    after = counts(owner)
    assert after == (before[0] + 1, before[1], before[2] + 1)
