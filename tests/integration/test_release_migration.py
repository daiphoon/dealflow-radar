"""Populated 0035 rehearsal; optional immutable runtime images execute the same test."""

import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.database import build_engine, set_request_context
from backend.app.demo import BETA_TENANT_ID, BETA_USER_ID, NO_ACCESS_USER_ID
from backend.app.main import create_app
from backend.app.models import Event, PersonalEventViewReceipt, PersonalReportRequest
from backend.app.providers import MockResearchProvider
from backend.app.services import seed_demo_entities
from scripts.bootstrap_local_database import bootstrap_application_role
from tests.integration import test_curated_import as curated
from tests.integration.test_incremental_research import initial
from tests.integration.test_personal_changes_reports import (
    PERSONAL_HEADERS,
    SHARED_COMPANY_ID,
    _add_shared_event,
)
from tests.integration.test_research_matter_storage import ingest


def runtime(image, url, *args):
    assert image.startswith("sha256:"), "rehearsal requires an immutable local image ID"
    internal = make_url(url).set(host="database", port=5432).render_as_string(hide_password=False)
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--network",
            "dealflow-closeout-fixture",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--memory=512m",
            "--pids-limit=128",
            "-e",
            f"DATABASE_URL={internal}",
            "-e",
            "APP_MODE=demo",
            "-e",
            "EXTERNAL_CALLS_ENABLED=false",
            "-e",
            "PAID_API_CALLS_ENABLED=false",
            "-e",
            "AUTO_REFRESH_ENABLED=false",
            "--entrypoint",
            args[0],
            image,
            *args[1:],
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    return result.stdout


def snapshot(engine):
    result = {}
    with engine.connect() as connection:
        for table in inspect(engine).get_table_names():
            if table in {"alembic_version", "personal_report_requests"}:
                continue
            # Only 0036's explicitly added columns are omitted from the comparison.
            rows = connection.execute(
                text(
                    f"SELECT to_jsonb(t) - 'semantic_version' - 'input_fingerprint' "
                    f'FROM "{table}" t'
                )
            ).scalars()
            encoded = sorted(json.dumps(r, sort_keys=True, default=str) for r in rows)
            result[table] = (len(encoded), hashlib.sha256("\n".join(encoded).encode()).hexdigest())
    return result


def test_populated_postgres_0035_upgrade_and_guarded_rollback(tmp_path, monkeypatch):
    admin_url = os.getenv("DATABASE_ADMIN_URL")
    if not admin_url or make_url(admin_url).host not in {"localhost", "127.0.0.1"}:
        pytest.skip("requires an explicitly configured local disposable PostgreSQL")
    name, role = "release_" + uuid4().hex, "release_" + uuid4().hex[:16]
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    owner = build_engine(url)
    app = create_app(
        Settings(
            database_url=url,
            app_mode="demo",
            external_calls_enabled=False,
            paid_api_calls_enabled=False,
            auto_refresh_enabled=False,
        )
    )
    app_engine = None
    try:
        command.upgrade(Config("alembic.ini"), "0035")
        with Session(owner) as s:
            seed_demo_entities(s, MockResearchProvider().load())
            s.commit()
        curated.curator(SimpleNamespace(owner=owner))
        with Session(owner, expire_on_commit=False) as s:
            user, company = initial(s, tmp_path)
            for n, body in enumerate(
                [
                    "示例山海完成近一亿元A轮融资，由示例机构参与投资。",
                    "示例山海完成近两亿元A轮融资。",
                    "示例山海通过港交所聆讯。",
                ]
            ):
                event, _, _, quality = ingest(s, user, company, body, f"https://example.com/r{n}")
                assert event is not None and quality.eligible
            s.commit()
        events = [_add_shared_event(app, "migration-" + str(n)) for n in range(3)]
        with Session(owner) as s:
            s.get(Event, events[0]).facts = [{"name": "amount", "value": "1.00亿元"}]
            s.get(Event, events[1]).status = "retracted"
            s.get(Event, events[2]).status = "rejected"
            s.commit()
        metadata = MetaData()
        receipts = Table("personal_event_view_receipts", metadata, autoload_with=owner)
        reports = Table("personal_company_reports", metadata, autoload_with=owner)
        report_id = uuid4()
        seen = datetime(2026, 1, 1, tzinfo=UTC)
        with owner.begin() as c:
            c.execute(
                receipts.insert(),
                [
                    dict(id=uuid4(), owner_user_id=u, event_id=e, first_seen_at=seen)
                    for u in (NO_ACCESS_USER_ID, BETA_USER_ID)
                    for e in events
                ],
            )
            c.execute(
                reports.insert(),
                dict(
                    id=report_id,
                    owner_user_id=NO_ACCESS_USER_ID,
                    company_id=SHARED_COMPANY_ID,
                    company_legal_name="示例星河科技一号有限公司",
                    report_version="personal-v1",
                    idempotency_key="a" * 64,
                    title="旧报告",
                    as_of=seen,
                    markdown="# 虚构旧报告\n保留原始正文。",
                    content_hash="b" * 64,
                    source_event_ids=[str(events[0])],
                    created_at=seen,
                ),
            )
        before = snapshot(owner)
        assert before["event_observations"][0] > 0
        image = os.getenv("RELEASE_RUNTIME_IMAGE")
        started = time.monotonic()
        if image:
            runtime(image, url, "alembic", "upgrade", "head")
        else:
            command.upgrade(Config("alembic.ini"), "head")
        print(
            f"migration_seconds={time.monotonic() - started:.3f}; "
            f"rows={sum(v[0] for v in before.values())}"
        )
        assert snapshot(owner) == before
        command.check(Config("alembic.ini"))
        bootstrap_application_role(url, role, "local_fixture_app_only")
        app_url = (
            make_url(url)
            .set(username=role, password="local_fixture_app_only")
            .render_as_string(hide_password=False)
        )
        app_engine = build_engine(app_url)
        with app_engine.connect() as c:
            assert c.execute(
                text(
                    "SELECT rolsuper,rolbypassrls,EXISTS(SELECT 1 FROM pg_class "
                    "WHERE relowner=pg_roles.oid AND relname='personal_report_requests') "
                    "FROM pg_roles WHERE rolname=current_user"
                )
            ).one() == (False, False, False)
        with Session(app_engine) as s:
            set_request_context(s, BETA_USER_ID, BETA_TENANT_ID)
            visible = list(s.scalars(select(PersonalEventViewReceipt)))
            assert len(visible) == 3 and all(r.owner_user_id == BETA_USER_ID for r in visible)
            assert all(r.first_seen_at == seen for r in visible)
            with pytest.raises(DBAPIError):
                with s.begin_nested():
                    s.add(
                        PersonalReportRequest(
                            owner_user_id=BETA_USER_ID,
                            report_id=report_id,
                            idempotency_key="d" * 64,
                        )
                    )
                    s.flush()
        new_app = create_app(
            Settings(
                database_url=app_url,
                app_mode="demo",
                external_calls_enabled=False,
                paid_api_calls_enabled=False,
                auto_refresh_enabled=False,
            )
        )
        try:
            with TestClient(new_app) as client:
                path = f"/api/v1/me/companies/{SHARED_COMPANY_ID}"
                response = client.post(path + "/view", headers=PERSONAL_HEADERS)
                assert response.status_code == 200
                assert str(events[0]) not in {e["id"] for e in response.json()["new_events"]}
                with Session(owner) as s:
                    s.get(Event, events[0]).facts = [{"name": "amount", "value": "2亿元"}]
                    s.commit()
                response = client.post(path + "/view", headers=PERSONAL_HEADERS)
                assert str(events[0]) in {e["id"] for e in response.json()["new_events"]}
                one = client.post(
                    path + "/reports", headers=PERSONAL_HEADERS, json={"idempotency_key": "e" * 64}
                )
                two = client.post(
                    path + "/reports", headers=PERSONAL_HEADERS, json={"idempotency_key": "f" * 64}
                )
                assert one.status_code == two.status_code == 200
                assert one.json()["id"] == two.json()["id"] and two.json()["reused"]
                assert (
                    client.get("/api/v1/me/usage", headers=PERSONAL_HEADERS).json()["reports"][
                        "used"
                    ]
                    == 1
                )
                assert client.get(
                    "/api/v1/me/reports/" + str(report_id),
                    headers={"X-Demo-User-Id": str(BETA_USER_ID)},
                ).status_code in {403, 404}
        finally:
            new_app.state.engine.dispose()
        old_image = os.getenv("RELEASE_OLD_RUNTIME_IMAGE")
        if old_image:
            probe = f"""
from fastapi.testclient import TestClient
from backend.app.main import app
with TestClient(app) as c:
    h={PERSONAL_HEADERS!r}
    for path in ['/api/v1/companies/{SHARED_COMPANY_ID}', '/api/v1/me/reports/{report_id}']:
        r=c.get(path,headers=h)
        assert r.status_code==200, (path,r.status_code)
    r=c.post('/api/v1/me/companies/{SHARED_COMPANY_ID}/view',headers=h)
    assert r.status_code==200
print('old runtime company/report/legacy receipt read passed; semantic writes unsupported')
"""
            with Session(owner) as s:
                s.get(Event, events[0]).facts = [{"name": "amount", "value": "3亿元"}]
                s.commit()
            stable = snapshot(owner)
            with owner.connect() as c:
                versions = c.execute(
                    text("SELECT id,semantic_version FROM personal_event_view_receipts ORDER BY id")
                ).all()
            print(runtime(old_image, app_url, "python", "-c", probe))
            # Existing old receipt writes must not corrupt new columns or historical rows.
            after_old = snapshot(owner)
            assert {k: v for k, v in after_old.items() if k != "personal_company_view_states"} == {
                k: v for k, v in stable.items() if k != "personal_company_view_states"
            }
            with owner.connect() as c:
                assert (
                    c.execute(
                        text(
                            "SELECT id,semantic_version FROM personal_event_view_receipts "
                            "ORDER BY id"
                        )
                    ).all()
                    == versions
                )
        before_downgrade = snapshot(owner)
        with pytest.raises(RuntimeError, match="Retain"):
            command.downgrade(Config("alembic.ini"), "0035")
        assert snapshot(owner) == before_downgrade
        with owner.connect() as c:
            assert c.scalar(text("SELECT version_num FROM alembic_version")) == "0036"
        assert "semantic_version" in {
            c["name"] for c in inspect(owner).get_columns("personal_event_view_receipts")
        }
    finally:
        app.state.engine.dispose()
        if app_engine is not None:
            app_engine.dispose()
        owner.dispose()
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            c.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()
