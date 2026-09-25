"""Opt-in actual diagnostic overlay on a disposable local PostgreSQL container."""

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from tests.integration import test_curated_import as curated
from tests.integration.test_diagnostic_service import diagnostic as diagnostic
from tests.integration.test_release_migration import snapshot

database = curated.database


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=45)
    if check:
        assert result.returncode == 0, result.stderr[-1500:]
    return result.stdout.strip()


def test_real_overlay_runtime_fail_closed_and_site_survives(diagnostic, database, tmp_path):
    image = os.getenv("RELEASE_RUNTIME_IMAGE")
    if not image:
        pytest.skip("explicit immutable local runtime image required")
    assert image.startswith("sha256:")
    service, principal = diagnostic
    project = "release-diag-" + tmp_path.name.lower().replace("_", "-")[-24:]
    credentials, state = tmp_path / "credentials", tmp_path / "state"
    credentials.mkdir(mode=0o755)
    state.mkdir(mode=0o777)
    state.chmod(0o777)
    token = "synthetic-container-token"
    record = [
        {
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "principal_id": principal.id,
            "company_ids": list(map(str, principal.company_ids)),
            "expires_at": time.time() + 600,
        }
    ]
    credential_file = credentials / "credentials.json"
    credential_file.write_text(json.dumps(record))
    credential_file.chmod(0o444)
    budget = state / "budget.json"
    budget.write_text("{}")
    docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        "0",
        "-v",
        f"{state}:/state",
        "--entrypoint",
        "python",
        image,
        "-c",
        "import os; os.chown('/state',10001,10001); os.chmod('/state',0o700); "
        "os.chown('/state/budget.json',10001,10001); os.chmod('/state/budget.json',0o600)",
    )
    base = tmp_path / "compose.yml"
    base.write_text("services:\n  database:\n    image: postgres:16\nnetworks:\n  app: {}\n")
    env = os.environ | {
        "DIAGNOSTIC_IMAGE": image,
        "DIAGNOSTIC_COMMIT": "local-runtime-rehearsal",
        "MCP_DATABASE_URL": make_url(service.engine.url)
        .set(host="database", port=5432)
        .render_as_string(hide_password=False),
        "MCP_CURSOR_SECRET": "synthetic-container-cursor-key-32-only",
        "MCP_CREDENTIALS_DIR": str(credentials),
        "MCP_STATE_DIR": str(state),
    }

    def compose(*args):
        r = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                project,
                "-f",
                str(base),
                "-f",
                str(Path("deploy/compose.diagnostic.yml").resolve()),
                "--profile",
                "diagnostic",
                *args,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert r.returncode == 0, r.stderr[-1500:]
        return r.stdout.strip()

    def call(token_value=token):
        code = f"""
import json,urllib.request,urllib.error
data=json.dumps({{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{{"name":"find_company","arguments":{{"query":"示例"}}}}}}).encode()
request=urllib.request.Request('http://127.0.0.1:8090/mcp', data=data,headers={{
 'Content-Type':'application/json','Accept':'application/json, text/event-stream',
 'Authorization':'Bearer '+{token_value!r}}})
try:
 r=urllib.request.urlopen(request,timeout=6)
except urllib.error.HTTPError as e:
 r=e
print(json.dumps({{'status_code':r.status,'text':r.read().decode()}}))
"""
        return SimpleNamespace(**json.loads(docker("exec", container, "python", "-c", code)))

    def health():
        return docker(
            "exec",
            api,
            "python",
            "-c",
            "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).status)",
        )

    def wait():
        for _ in range(20):
            try:
                if call().status_code == 200:
                    return
            except AssertionError:
                pass
            time.sleep(0.1)
        pytest.fail("isolated diagnostic did not become ready")

    api = frontend = None
    connected = False
    before = snapshot(database.owner)
    try:
        compose("up", "--no-start", "--no-deps", "diagnostic-mcp")
        container = compose("ps", "-aq", "diagnostic-mcp")
        network = project + "_diagnostic"
        docker(
            "network", "connect", "--alias", "database", network, "dealflow-closeout-pg-20260925"
        )
        connected = True
        compose("start", "diagnostic-mcp")
        wait()
        info = json.loads(docker("inspect", container))[0]
        host = info["HostConfig"]
        assert info["Config"]["User"] in {"app", "10001", "10001:10001"}
        assert host["ReadonlyRootfs"] and host["Memory"] == 256 * 1024 * 1024
        assert host["PidsLimit"] == 64 and host["NanoCpus"] == 500_000_000
        assert host["LogConfig"]["Config"] == {"max-size": "5m", "max-file": "2"}
        assert host["PortBindings"]["8090/tcp"][0]["HostIp"] == "127.0.0.1"
        print("effective_published_ports=" + json.dumps(info["NetworkSettings"]["Ports"]))
        assert not any("docker.sock" in m["Source"] for m in info["Mounts"])
        assert not next(m for m in info["Mounts"] if m["Destination"] == "/run/diagnostic")["RW"]
        assert json.loads(docker("network", "inspect", network))[0]["Internal"]
        probe = """
import os,socket
assert os.getuid()==10001 and os.getgid()==10001
assert os.stat('/var/lib/diagnostic').st_mode & 0o777 == 0o700
assert os.stat('/var/lib/diagnostic/budget.json').st_mode & 0o777 == 0o600
for p in ['/app/forbidden','/run/diagnostic/forbidden']:
 try: open(p,'w'); raise AssertionError('write permitted')
 except OSError: pass
s=socket.socket(); s.settimeout(.3)
try: s.connect(('192.0.2.1',443)); raise AssertionError('egress permitted')
except OSError: pass
"""
        docker("exec", container, "python", "-c", probe)
        api_url = (
            make_url(database.app.url)
            .set(host="database", port=5432)
            .render_as_string(hide_password=False)
        )
        api = docker(
            "run",
            "-d",
            "--rm",
            "--network",
            network,
            "--read-only",
            "-p",
            "127.0.0.1::8000",
            "-e",
            "APP_MODE=demo",
            "-e",
            f"DATABASE_URL={api_url}",
            "-e",
            "EXTERNAL_CALLS_ENABLED=false",
            "-e",
            "PAID_API_CALLS_ENABLED=false",
            "-e",
            "AUTO_REFRESH_ENABLED=false",
            image,
        )
        frontend_image = os.getenv("RELEASE_FRONTEND_IMAGE")
        if frontend_image:
            assert frontend_image.startswith("sha256:")
            frontend = docker(
                "run", "-d", "--rm", "--network", network, "--read-only", frontend_image
            )
        for _ in range(20):
            try:
                if health() == "200":
                    break
            except AssertionError:
                pass
            time.sleep(0.1)
        elapsed = []
        for _ in range(3):
            assert "diagnostic_schema_version" in call().text
            start = time.monotonic()
            assert health() == "200"
            elapsed.append(time.monotonic() - start)
        print("cohost_health_seconds=" + json.dumps(elapsed))
        compose("restart", "diagnostic-mcp")
        wait()
        assert call("wrong").status_code == 401
        credential_file.chmod(0o644)
        credential_file.write_text(json.dumps([{**record[0], "expires_at": 1}]))
        credential_file.chmod(0o444)
        assert call().status_code == 401
        credential_file.chmod(0o644)
        credential_file.write_text(json.dumps(record))
        credential_file.chmod(0o444)
        # Corruption, unwritable and missing quota stores must not grant a fresh budget.
        for mutation in [
            "p.write_text('{broken')",
            "p.write_text('{}'); p.chmod(0o400)",
            "p.chmod(0o600); p.unlink()",
        ]:
            docker(
                "exec",
                container,
                "python",
                "-c",
                "from pathlib import Path; p=Path('/var/lib/diagnostic/budget.json'); " + mutation,
            )
            response = call()
            assert "diagnostic_schema_version" not in response.text
            assert (
                "diagnostic_unavailable" in response.text
                or "diagnostic_request_denied" in response.text
            )
        compose("stop", "diagnostic-mcp")
        assert health() == "200"
        if frontend:
            assert (
                docker(
                    "exec",
                    frontend,
                    "node",
                    "-e",
                    "fetch('http://127.0.0.1:3000/login').then(r=>console.log(r.status))",
                )
                == "200"
            )
        assert snapshot(database.owner) == before
        compose("start", "diagnostic-mcp")
        for _ in range(20):
            if json.loads(docker("inspect", container))[0]["State"]["Status"] == "exited":
                break
            time.sleep(0.1)
        assert json.loads(docker("inspect", container))[0]["State"]["Status"] == "exited"
    finally:
        if api:
            docker("rm", "-f", api, check=False)
        if frontend:
            docker("rm", "-f", frontend, check=False)
        if connected:
            docker(
                "network",
                "disconnect",
                project + "_diagnostic",
                "dealflow-closeout-pg-20260925",
                check=False,
            )
        compose("down")
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0",
            "-v",
            f"{state}:/state",
            "--entrypoint",
            "python",
            image,
            "-c",
            f"import os; os.chown('/state',{os.getuid()},{os.getgid()}); os.chmod('/state',0o700)",
            check=False,
        )
        with database.owner.begin() as c:
            c.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=current_database() AND usename IN (:reader,:app)"
                ),
                {"reader": service.engine.url.username, "app": database.app.url.username},
            )
