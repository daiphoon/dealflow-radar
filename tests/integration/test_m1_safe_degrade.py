"""Fail-closed read-only rehearsal on a disposable PostgreSQL database."""

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.database import set_request_context
from backend.app.demo import ALPHA_TENANT_ID, BETA_TENANT_ID, BETA_USER_ID, NO_ACCESS_USER_ID
from backend.app.main import create_app
from backend.app.models import (
    CompanyResearchJob,
    CompanyWatchSchedule,
    EventEvidence,
    EventFact,
    EventFactSupport,
    EventObservation,
    PersonalReportRequest,
    PersonalWatchlistItem,
    RefreshJob,
    UsageLedger,
    User,
)
from backend.app.providers import MockResearchProvider
from backend.app.services import seed_demo_entities
from scripts.bootstrap_local_database import bootstrap_application_role
from tests.integration.test_personal_changes_reports import (
    PERSONAL_HEADERS,
    SHARED_COMPANY_ID,
    _add_shared_event,
)
from tests.integration.test_release_migration import snapshot

FAKE_HTTP_SERVER = """
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'upstream reached')
    do_POST = do_GET
    do_PATCH = do_GET
    do_DELETE = do_GET
HTTPServer(('0.0.0.0', int(sys.argv[1])), Handler).serve_forever()
"""


def _docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr[-1500:]
    return result.stdout.strip()


@contextmanager
def _normal_application_stack(api_image, frontend_image, database_url):
    """Start only the selected API, frontend and normal Caddy; never a Worker."""
    suffix = uuid4().hex[:10]
    network = f"m1-normal-{suffix}"
    api, frontend, proxy = (f"m1-{name}-{suffix}" for name in ("api", "frontend", "proxy"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    _docker("network", "create", network)
    try:
        for name, alias, image, environment in (
            (
                api,
                "api",
                api_image,
                {
                    "DATABASE_URL": database_url,
                    "APP_MODE": "demo",
                    "EXTERNAL_CALLS_ENABLED": "false",
                    "PAID_API_CALLS_ENABLED": "false",
                    "AUTO_REFRESH_ENABLED": "false",
                    "AUTO_PUBLISH_ENABLED": "false",
                    "WEB_RESEARCH_ENABLED": "false",
                    "WEB_RESEARCH_CALLS_ENABLED": "false",
                },
            ),
            (
                frontend,
                "frontend",
                frontend_image,
                {
                    "AUTH_PROVIDER": "demo",
                    "DEMO_USER_ID": PERSONAL_HEADERS["X-Demo-User-Id"],
                    "API_BASE_URL": "http://api:8000",
                    "APP_PUBLIC_ORIGIN": origin,
                },
            ),
        ):
            env_args = [
                arg for key, value in environment.items() for arg in ("-e", f"{key}={value}")
            ]
            _docker(
                "run",
                "--rm",
                "-d",
                "--name",
                name,
                "--network",
                network,
                "--network-alias",
                alias,
                "--add-host=host.docker.internal:host-gateway",
                *env_args,
                image,
            )
        _docker(
            "run",
            "--rm",
            "-d",
            "--name",
            proxy,
            "--network",
            network,
            "-e",
            "SITE_ADDRESS=:80",
            "-p",
            f"127.0.0.1:{port}:80",
            "-v",
            f"{Path('deploy/Caddyfile').resolve()}:/etc/caddy/Caddyfile:ro",
            "caddy:2.10.2-alpine",
        )
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                    f"{origin}/companies/{SHARED_COMPANY_ID}", timeout=5
                ) as response:
                    body = response.read().decode()
                    if response.status == 200 and "示例星河科技一号有限公司" in body:
                        break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.2)
        else:
            pytest.fail("new frontend did not render the actual company through normal Caddy")
        yield origin, frontend
    finally:
        subprocess.run(["docker", "rm", "-f", proxy, frontend, api], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)


def test_real_caddy_safe_gate_rejects_server_actions_before_old_frontend():
    if os.getenv("M1_DOCKER_GATE_TESTS") != "1":
        pytest.skip("set M1_DOCKER_GATE_TESTS=1 for the actual local Caddy gate")
    config = Path("deploy/Caddyfile.safe-degrade").resolve()
    assert config.is_file()
    compose = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "tools",
            "--profile",
            "restore",
            "--env-file",
            "deploy/single-host.env.example",
            "-f",
            "compose.production.yml",
            "-f",
            "deploy/compose.single-host.yml",
            "-f",
            "deploy/compose.safe-degrade.yml",
            "config",
            "--format",
            "json",
        ],
        env={**os.environ, "SAFE_CONTROL_IMAGE": "dealflow-m1-control:synthetic-test"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert compose.returncode == 0, compose.stderr
    services = json.loads(compose.stdout)["services"]
    assert "safe-degrade-restore-role" not in services
    assert services["bootstrap-role"]["command"] == ["/bin/false"]
    assert services["migrate"]["command"] == ["/bin/false"]
    suffix = uuid4().hex[:10]
    network, frontend, api, proxy = (
        f"m1-gate-{suffix}",
        f"m1-frontend-{suffix}",
        f"m1-api-{suffix}",
        f"m1-proxy-{suffix}",
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    _docker("network", "create", network)
    try:
        for name, service_port in ((frontend, "3000"), (api, "8000")):
            _docker(
                "run",
                "--rm",
                "-d",
                "--name",
                name,
                "--network",
                network,
                "--network-alias",
                "frontend" if name == frontend else "api",
                "--entrypoint",
                "python",
                os.getenv("M1_GATE_UPSTREAM_IMAGE", "dealflow-radar-backend:m5a-local"),
                "-c",
                FAKE_HTTP_SERVER,
                service_port,
            )

        def start_proxy(caddyfile: Path) -> None:
            _docker(
                "run",
                "--rm",
                "-d",
                "--name",
                proxy,
                "--network",
                network,
                "-e",
                "SITE_ADDRESS=:80",
                "-p",
                f"127.0.0.1:{port}:80",
                "-v",
                f"{caddyfile}:/etc/caddy/Caddyfile:ro",
                "caddy:2.10.2-alpine",
            )

        def request(path: str, method: str = "GET") -> tuple[int, bytes]:
            probe = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                method=method,
                headers={"Next-Action": "synthetic-m1-action"},
            )
            try:
                with urllib.request.urlopen(probe, timeout=5) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as error:
                return error.code, error.read()

        def await_proxy() -> None:
            for _ in range(30):
                try:
                    if request("/health")[0] == 200:
                        return
                except (OSError, urllib.error.URLError):
                    time.sleep(0.1)
            pytest.fail("proxy did not become ready")

        company = "/companies/00000000-0000-4000-8000-000000000001"
        start_proxy(Path("deploy/Caddyfile").resolve())
        await_proxy()
        assert request(company, "POST") == (200, b"upstream reached")
        _docker("rm", "-f", proxy)

        start_proxy(config)
        await_proxy()
        assert request("/ready")[0] == 200
        for path in (company, f"{company}?_rsc=synthetic", "/_next/static/chunk.js"):
            assert request(path)[0] == 200
        for path in (
            "/reports/00000000-0000-4000-8000-000000000001",
            "/watchlist",
            "/auth/refresh",
            "/api/v1/companies/00000000-0000-4000-8000-000000000001",
            "/evidence/00000000-0000-4000-8000-000000000001",
            "/?q=example",
            "/companies/not-a-uuid",
            "/_next/image?url=example",
            "/login",
            f"{company}?result=report_generated",
        ):
            status, body = request(path)
            assert status == 503 and b"maintenance_read_only" in body, path
        for path, method in (
            (company, "POST"),
            ("/login", "POST"),
            ("/reports", "POST"),
            ("/watchlist", "DELETE"),
            (company, "PATCH"),
            (company, "PUT"),
        ):
            status, body = request(path, method)
            assert status == 503 and b"maintenance_read_only" in body
        with ThreadPoolExecutor(max_workers=12) as pool:
            denied = list(pool.map(lambda _: request(company, "POST"), range(30)))
        assert all(status == 503 and b"maintenance_read_only" in body for status, body in denied)
        _docker("rm", "-f", proxy)
        start_proxy(Path("deploy/Caddyfile").resolve())
        await_proxy()
        assert request(company, "POST") == (200, b"upstream reached")
    finally:
        subprocess.run(["docker", "rm", "-f", proxy, frontend, api], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)


def test_restrict_existing_app_role_preserves_rows_and_can_be_explicitly_restored(monkeypatch):
    admin_url = os.getenv("DATABASE_ADMIN_URL")
    if not admin_url or make_url(admin_url).host not in {"localhost", "127.0.0.1"}:
        pytest.skip("requires an explicitly configured local disposable PostgreSQL")

    from scripts.safe_degrade_database import restrict_application_role, verify_read_only_role

    name = "m1_safe_" + uuid4().hex
    role = "m1_safe_" + uuid4().hex[:16]
    password = "synthetic-m1-app-password"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    owner_url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    owner = create_engine(owner_url)
    app = None
    new_application = None
    fixture_application = None
    try:
        monkeypatch.setenv("DATABASE_URL", owner_url)
        command.upgrade(Config("alembic.ini"), "head")
        with Session(owner) as session:
            seed_demo_entities(session, MockResearchProvider().load())
            session.commit()
        bootstrap_application_role(owner_url, role, password)
        app_url = (
            make_url(owner_url)
            .set(username=role, password=password)
            .render_as_string(hide_password=False)
        )
        app = create_engine(app_url)
        fixture_application = create_app(
            Settings(
                database_url=owner_url,
                app_mode="demo",
                external_calls_enabled=False,
                paid_api_calls_enabled=False,
                auto_refresh_enabled=False,
            )
        )
        event_id = _add_shared_event(fixture_application, "m1-safe-degrade")
        fixture_application.state.engine.dispose()
        fixture_application = None
        with Session(owner) as session:
            evidence = session.scalar(
                select(EventEvidence).where(EventEvidence.event_id == event_id)
            )
            assert evidence is not None and evidence.raw_document_id is not None
            fact = EventFact(
                event_id=event_id,
                fact_key="synthetic_milestone",
                name="示例里程碑",
                value="已完成",
                position=0,
            )
            session.add(fact)
            session.flush()
            session.add_all(
                [
                    EventFactSupport(
                        event_id=event_id,
                        event_fact_id=fact.id,
                        event_evidence_id=evidence.id,
                        support_status="supported",
                        policy_version="synthetic-m1",
                    ),
                    EventObservation(
                        event_id=event_id,
                        raw_document_id=evidence.raw_document_id,
                        schema_version="synthetic-m1",
                        fact_version="synthetic-m1",
                        observation_kind="initial",
                        occurred_on=None,
                        date_precision="unknown",
                        candidate_payload={"synthetic": True},
                        created_by=BETA_USER_ID,
                    ),
                    PersonalWatchlistItem(
                        owner_user_id=NO_ACCESS_USER_ID,
                        company_id=SHARED_COMPANY_ID,
                    ),
                    RefreshJob(
                        tenant_id=BETA_TENANT_ID,
                        company_id=SHARED_COMPANY_ID,
                        refresh_reason="synthetic_history",
                        status="completed",
                        idempotency_key=uuid4().hex,
                    ),
                    CompanyResearchJob(
                        company_id=SHARED_COMPANY_ID,
                        created_by_user_id=BETA_USER_ID,
                        status="completed",
                        policy_version="synthetic-m1",
                    ),
                    CompanyWatchSchedule(
                        company_id=SHARED_COMPANY_ID,
                        policy_version="synthetic-m1",
                        next_check_at=datetime.now(UTC),
                    ),
                    UsageLedger(
                        tenant_id=BETA_TENANT_ID,
                        company_id=SHARED_COMPANY_ID,
                        provider="mock",
                        operation="synthetic_history",
                        external_calls=0,
                        cost_status="confirmed_free",
                        usage_state="settled",
                        idempotency_key=uuid4().hex,
                    ),
                ]
            )
            session.commit()
        new_application = create_app(
            Settings(
                database_url=app_url,
                app_mode="demo",
                external_calls_enabled=False,
                paid_api_calls_enabled=False,
                auto_refresh_enabled=False,
            )
        )
        report_key = "d" * 64
        with TestClient(new_application) as client:
            path = f"/api/v1/me/companies/{SHARED_COMPANY_ID}"
            assert (
                client.get(
                    f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=PERSONAL_HEADERS
                ).status_code
                == 200
            )
            assert client.post(path + "/view", headers=PERSONAL_HEADERS).status_code == 200
            report = client.post(
                path + "/reports",
                headers=PERSONAL_HEADERS,
                json={"idempotency_key": report_key},
            )
            assert report.status_code == 200
            report_id = report.json()["id"]
        with Session(owner) as session:
            session.add(
                PersonalReportRequest(
                    owner_user_id=NO_ACCESS_USER_ID,
                    report_id=report_id,
                    idempotency_key="r" * 64,
                )
            )
            session.commit()
        new_image = os.getenv("M1_NEW_RUNTIME_IMAGE")
        new_frontend = os.getenv("M1_NEW_FRONTEND_IMAGE")
        container_url = (
            make_url(app_url).set(host="host.docker.internal").render_as_string(hide_password=False)
        )
        if new_image and new_frontend:
            assert new_image.startswith("sha256:") and new_frontend.startswith("sha256:")
            with _normal_application_stack(new_image, new_frontend, container_url):
                pass
        before = snapshot(owner)
        with owner.connect() as connection:
            first_seen = connection.execute(
                text("SELECT id, first_seen_at FROM personal_event_view_receipts ORDER BY id")
            ).all()
            versions = (
                connection.execute(
                    text("SELECT semantic_version FROM personal_event_view_receipts")
                )
                .scalars()
                .all()
            )
            assert versions and all(version and len(version) == 64 for version in versions)

        # The actual application role can write before entering safe mode.
        with app.connect() as connection:
            assert connection.scalar(
                text("SELECT has_table_privilege(current_user, 'users', 'UPDATE')")
            )

        # An extra publicly executable definer function must abort the entire transition.
        with owner.begin() as connection:
            connection.execute(
                text(
                    "CREATE FUNCTION public.m1_unreviewed_definer() RETURNS integer "
                    "LANGUAGE sql SECURITY DEFINER AS $$ SELECT 1 $$"
                )
            )
        with pytest.raises(RuntimeError, match="write path"):
            restrict_application_role(owner_url, role)
        with app.connect() as connection:
            assert connection.scalar(
                text("SELECT has_table_privilege(current_user, 'users', 'UPDATE')")
            )
        with owner.begin() as connection:
            connection.execute(text("DROP FUNCTION public.m1_unreviewed_definer()"))

        # Deployment drift outside public must also fail closed, not leave a write bypass.
        with owner.begin() as connection:
            connection.execute(text("CREATE SCHEMA m1_unreviewed"))
            connection.execute(text("CREATE TABLE m1_unreviewed.state (value integer)"))
            connection.execute(text(f'GRANT USAGE ON SCHEMA m1_unreviewed TO "{role}"'))
            connection.execute(text(f'GRANT UPDATE ON m1_unreviewed.state TO "{role}"'))
        with pytest.raises(RuntimeError, match="write path"):
            restrict_application_role(owner_url, role)
        with owner.begin() as connection:
            connection.execute(text("DROP SCHEMA m1_unreviewed CASCADE"))

        restrict_application_role(owner_url, role)
        assert verify_read_only_role(owner_url, role)["read_only"] is True
        with Session(app) as session:
            set_request_context(session, BETA_USER_ID, BETA_TENANT_ID)
            assert session.get(User, BETA_USER_ID) is not None
            assert list(session.scalars(select(PersonalWatchlistItem))) == []
            with pytest.raises(DBAPIError):
                session.execute(
                    text("UPDATE users SET status = status WHERE id = :id"), {"id": BETA_USER_ID}
                )
            session.rollback()
        with Session(app) as session:
            set_request_context(session, NO_ACCESS_USER_ID, ALPHA_TENANT_ID)
            assert len(list(session.scalars(select(PersonalWatchlistItem)))) == 1
        with TestClient(new_application) as client:
            assert (
                client.get(
                    f"/api/v1/companies/{SHARED_COMPANY_ID}", headers=PERSONAL_HEADERS
                ).status_code
                == 200
            )
        with app.connect() as connection:
            for sql in (
                "INSERT INTO users DEFAULT VALUES",
                "DELETE FROM users WHERE false",
                "TRUNCATE TABLE users",
            ):
                with pytest.raises(DBAPIError):
                    connection.execute(text(sql))
                connection.rollback()
        safe_snapshot = snapshot(owner)
        assert safe_snapshot == before

        container_url = (
            make_url(app_url).set(host="host.docker.internal").render_as_string(hide_password=False)
        )
        old_image = os.getenv("M1_OLD_RUNTIME_IMAGE")
        if old_image:
            assert old_image.startswith("sha256:")
            old_probe = f"""
from fastapi.testclient import TestClient
from backend.app.main import app
with TestClient(app, raise_server_exceptions=False) as client:
    headers={PERSONAL_HEADERS!r}
    assert client.get('/api/v1/companies/{SHARED_COMPANY_ID}',headers=headers).status_code==200
    assert client.get('/api/v1/me/usage',headers=headers).status_code==200
    rejected=client.post('/api/v1/me/companies/{SHARED_COMPANY_ID}/view',headers=headers)
    assert rejected.status_code>=400, rejected.status_code
print('frozen old API reads; write rejected by database role')
"""
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--read-only",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--add-host=host.docker.internal:host-gateway",
                    "-e",
                    f"DATABASE_URL={container_url}",
                    "-e",
                    "APP_MODE=demo",
                    "-e",
                    "EXTERNAL_CALLS_ENABLED=false",
                    "-e",
                    "PAID_API_CALLS_ENABLED=false",
                    "-e",
                    "AUTO_REFRESH_ENABLED=false",
                    "-e",
                    "WEB_RESEARCH_ENABLED=false",
                    "-e",
                    "WEB_RESEARCH_CALLS_ENABLED=false",
                    "--entrypoint",
                    "python",
                    old_image,
                    "-c",
                    old_probe,
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
            assert result.returncode == 0, result.stderr[-1500:]
            assert "frozen old API reads" in result.stdout
            assert snapshot(owner) == before

            old_frontend = os.getenv("M1_OLD_FRONTEND_IMAGE")
            if old_frontend:
                assert old_frontend.startswith("sha256:")
                suffix = uuid4().hex[:10]
                network = f"m1-old-page-{suffix}"
                api_name = f"m1-old-api-{suffix}"
                front_name = f"m1-old-front-{suffix}"
                proxy_name = f"m1-old-proxy-{suffix}"
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    port = listener.getsockname()[1]
                _docker("network", "create", network)
                try:
                    _docker(
                        "run",
                        "--rm",
                        "-d",
                        "--name",
                        api_name,
                        "--network",
                        network,
                        "--network-alias",
                        "api",
                        "--add-host=host.docker.internal:host-gateway",
                        "-e",
                        f"DATABASE_URL={container_url}",
                        "-e",
                        "APP_MODE=demo",
                        "-e",
                        "EXTERNAL_CALLS_ENABLED=false",
                        "-e",
                        "PAID_API_CALLS_ENABLED=false",
                        "-e",
                        "AUTO_REFRESH_ENABLED=false",
                        "-e",
                        "WEB_RESEARCH_ENABLED=false",
                        "-e",
                        "WEB_RESEARCH_CALLS_ENABLED=false",
                        old_image,
                    )
                    _docker(
                        "run",
                        "--rm",
                        "-d",
                        "--name",
                        front_name,
                        "--network",
                        network,
                        "--network-alias",
                        "frontend",
                        "-e",
                        "AUTH_PROVIDER=demo",
                        "-e",
                        f"DEMO_USER_ID={PERSONAL_HEADERS['X-Demo-User-Id']}",
                        "-e",
                        "API_BASE_URL=http://api:8000",
                        "-e",
                        f"APP_PUBLIC_ORIGIN=http://127.0.0.1:{port}",
                        old_frontend,
                    )
                    _docker(
                        "run",
                        "--rm",
                        "-d",
                        "--name",
                        proxy_name,
                        "--network",
                        network,
                        "-e",
                        "SITE_ADDRESS=:80",
                        "-p",
                        f"127.0.0.1:{port}:80",
                        "-v",
                        f"{Path('deploy/Caddyfile.safe-degrade').resolve()}:/etc/caddy/Caddyfile:ro",
                        "caddy:2.10.2-alpine",
                    )
                    page_url = None
                    body = ""
                    for _ in range(50):
                        try:
                            page = urllib.request.urlopen(
                                f"http://127.0.0.1:{port}/companies/{SHARED_COMPANY_ID}",
                                timeout=5,
                            )
                            page_url = page.url
                            body = page.read().decode()
                            if page.status == 200 and "示例星河科技一号有限公司" in body:
                                break
                        except (OSError, urllib.error.URLError):
                            pass
                        time.sleep(0.2)
                    assert "示例星河科技一号有限公司" in body, (page_url, body[-300:])
                    old_action = _docker(
                        "exec",
                        front_name,
                        "node",
                        "-e",
                        "const fs=require('fs'); const m=JSON.parse(fs.readFileSync("
                        "'.next/server/server-reference-manifest.json','utf8')); "
                        "console.log(Object.entries(m.node).find("
                        "([id,v])=>v.exportedName==='loadPersonalCompanyChanges')[0]);",
                    )
                    background = urllib.request.Request(
                        f"http://127.0.0.1:{port}/companies/{SHARED_COMPANY_ID}",
                        data=json.dumps([str(SHARED_COMPANY_ID)]).encode(),
                        headers={
                            "Next-Action": old_action,
                            "Content-Type": "text/plain;charset=UTF-8",
                        },
                    )
                    with pytest.raises(urllib.error.HTTPError) as denied:
                        urllib.request.urlopen(background, timeout=5)
                    assert denied.value.code == 503
                    assert b"maintenance_read_only" in denied.value.read()

                    def denied_request(path, method="GET"):
                        request = urllib.request.Request(
                            f"http://127.0.0.1:{port}{path}",
                            method=method,
                            headers={"Next-Action": "synthetic-m1-action"},
                        )
                        with pytest.raises(urllib.error.HTTPError) as denied:
                            urllib.request.urlopen(request, timeout=5)
                        assert denied.value.code == 503
                        assert b"maintenance_read_only" in denied.value.read()

                    for path in (
                        "/reports",
                        f"/reports/{report_id}",
                        "/watchlist",
                        "/auth/refresh",
                        f"/api/v1/me/companies/{SHARED_COMPANY_ID}/view",
                        f"/api/v1/me/companies/{SHARED_COMPANY_ID}/reports",
                        f"/api/v1/companies/{SHARED_COMPANY_ID}/refresh",
                        "/api/v1/research",
                    ):
                        for method in ("GET", "POST", "PATCH", "DELETE", "PUT"):
                            denied_request(path, method)
                    with ThreadPoolExecutor(max_workers=12) as pool:
                        list(
                            pool.map(
                                lambda _: denied_request(f"/companies/{SHARED_COMPANY_ID}", "POST"),
                                range(30),
                            )
                        )
                    assert snapshot(owner) == before
                finally:
                    subprocess.run(
                        ["docker", "rm", "-f", proxy_name, front_name, api_name],
                        capture_output=True,
                    )
                    subprocess.run(["docker", "network", "rm", network], capture_output=True)

        # Restoring normal privileges requires the established, explicit bootstrap step.
        bootstrap_application_role(owner_url, role, password)
        with app.connect() as connection:
            assert connection.scalar(
                text("SELECT has_table_privilege(current_user, 'users', 'UPDATE')")
            )
        if new_image:
            assert new_image.startswith("sha256:")
            restored_probe = f"""
from fastapi.testclient import TestClient
from backend.app.main import app
with TestClient(app) as client:
    headers={PERSONAL_HEADERS!r}
    path='/api/v1/me/companies/{SHARED_COMPANY_ID}'
    assert client.post(path+'/view',headers=headers).status_code==200
    report=client.post(path+'/reports',headers=headers,json={{'idempotency_key':{report_key!r}}})
    assert report.status_code==200, report.text
    assert report.json()['id']=={report_id!r} and report.json()['reused']
print('new immutable API restored receipt and original report reuse')
"""
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--read-only",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--add-host=host.docker.internal:host-gateway",
                    "-e",
                    f"DATABASE_URL={container_url}",
                    "-e",
                    "APP_MODE=demo",
                    "-e",
                    "EXTERNAL_CALLS_ENABLED=false",
                    "-e",
                    "PAID_API_CALLS_ENABLED=false",
                    "-e",
                    "AUTO_REFRESH_ENABLED=false",
                    "--entrypoint",
                    "python",
                    new_image,
                    "-c",
                    restored_probe,
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
            assert result.returncode == 0, result.stderr[-1500:]
            assert "new immutable API restored" in result.stdout
        if new_image and new_frontend:
            with _normal_application_stack(new_image, new_frontend, container_url) as (
                origin,
                frontend,
            ):
                actions = json.loads(
                    _docker(
                        "exec",
                        frontend,
                        "node",
                        "-e",
                        "const fs=require('fs'); const m=JSON.parse(fs.readFileSync("
                        "'.next/server/server-reference-manifest.json','utf8')); "
                        "console.log(JSON.stringify(Object.fromEntries(Object.entries(m.node)"
                        ".filter(([id,v])=>v.filename==='app/personal-actions.ts')"
                        ".map(([id,v])=>[v.exportedName,id]))));",
                    )
                )
                path = f"{origin}/companies/{SHARED_COMPANY_ID}"
                # The public application uses real Next Server Actions, not a public API route.
                request = urllib.request.Request(
                    path,
                    data=json.dumps([str(SHARED_COMPANY_ID)]).encode(),
                    headers={
                        "Content-Type": "text/plain;charset=UTF-8",
                        "Origin": origin,
                        "Next-Action": actions["loadPersonalCompanyChanges"],
                    },
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    assert response.status == 200 and b"viewed_at" in response.read()
                form = {
                    f"$ACTION_ID_{actions['generateCompanyReport']}": "",
                    "company_id": str(SHARED_COMPANY_ID),
                    "idempotency_key": report_key,
                }
                boundary = "m1-" + uuid4().hex
                multipart = (
                    "".join(
                        f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n'
                        f"\r\n{value}\r\n"
                        for key, value in form.items()
                    )
                    + f"--{boundary}--\r\n"
                )
                request = urllib.request.Request(
                    path,
                    data=multipart.encode(),
                    headers={
                        "Content-Type": f"multipart/form-data; boundary={boundary}",
                        "Origin": origin,
                    },
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    assert response.status == 200
                    assert f"/reports/{report_id}?result=report_reused" in response.url
        with TestClient(new_application) as client:
            path = f"/api/v1/me/companies/{SHARED_COMPANY_ID}"
            assert client.post(path + "/view", headers=PERSONAL_HEADERS).status_code == 200
            reused = client.post(
                path + "/reports",
                headers=PERSONAL_HEADERS,
                json={"idempotency_key": report_key},
            )
            assert reused.status_code == 200
            assert reused.json()["id"] == report_id and reused.json()["reused"]
        after = snapshot(owner)
        assert {table for table in before if after[table] != before[table]} <= {
            "personal_company_view_states"
        }
        for table in (
            "personal_company_reports",
            "personal_report_requests",
            "refresh_jobs",
            "company_research_jobs",
            "usage_ledger",
            "event_observations",
            "event_evidence",
            "event_facts",
            "event_fact_supports",
        ):
            assert after[table] == before[table], table
        with owner.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT id, first_seen_at FROM personal_event_view_receipts ORDER BY id")
                ).all()
                == first_seen
            )
        evidence_path = os.getenv("M1_EVIDENCE_PATH")
        if evidence_path:
            output = Path(evidence_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(
                    {
                        "baseline": before,
                        "safe_degrade": safe_snapshot,
                        "recovered": after,
                        "safe_changed_tables": [
                            table for table in before if before[table] != safe_snapshot[table]
                        ],
                        "recovered_changed_tables": [
                            table for table in before if before[table] != after[table]
                        ],
                        "first_seen_preserved": True,
                        "report_reused": True,
                        "old_image": old_image,
                        "old_frontend": os.getenv("M1_OLD_FRONTEND_IMAGE"),
                        "new_image": new_image,
                        "new_frontend": new_frontend,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
    finally:
        if fixture_application is not None:
            fixture_application.state.engine.dispose()
        if new_application is not None:
            new_application.state.engine.dispose()
        if app is not None:
            app.dispose()
        owner.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()
