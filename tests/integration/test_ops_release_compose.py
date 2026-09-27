"""Opt-in: frozen amd64 images through the exact archive/CLI/Compose/apply path."""

import base64
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from uuid import uuid4

import pytest

from scripts.release_ops.bridge import send
from scripts.release_ops.compose import ComposeSpec, compose_environment
from scripts.release_ops.evidence import package_sha256
from tests.integration.test_ops_release_containers import API, BUSINESS, CONTROL, FRONT

OLD = "ff174998893a1a868b95e469817c1f1199221e89"
FLAGS = [
    "EXTERNAL_CALLS_ENABLED",
    "PAID_API_CALLS_ENABLED",
    "AUTO_REFRESH_ENABLED",
    "AUTO_PUBLISH_ENABLED",
    "TRUSTED_SOURCE_CALLS_ENABLED",
    "SOURCE_MONITOR_SCHEDULER_ENABLED",
    "WEB_RESEARCH_ENABLED",
    "WEB_RESEARCH_CALLS_ENABLED",
    "WEB_RESEARCH_MATTERS_ENABLED",
    "WEB_RESEARCH_EXTRACTION_ENABLED",
    "WEB_RESEARCH_TAVILY_ENABLED",
    "WEB_RESEARCH_TOPIC_PLANNING_ENABLED",
    "WEB_RESEARCH_INCREMENTAL_ENABLED",
    "INVESTOR_ANALYSIS_ENABLED",
    "WATCHLIST_MONITOR_ENABLED",
]
HEALTH = {
    "api": [
        "CMD",
        "python",
        "-c",
        "import urllib.request; "
        "urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3).read()",
    ],
    "frontend": [
        "CMD",
        "node",
        "-e",
        "fetch('http://127.0.0.1:3000/login').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))",
    ],
}


def test_frozen_images_formal_compose_apply_recovery(tmp_path):
    if os.getenv("M1_OPS_ISOLATED") != "1":
        pytest.skip("explicit local frozen-image and synthetic database authorization required")
    assert os.getenv("M1_COMPOSE_BINARY")
    source = Path(__file__).resolve().parents[2]
    project = "m1-ops-isolated-" + uuid4().hex[:10]
    root = tmp_path / project
    release = root / "releases" / BUSINESS
    (release / "deploy").mkdir(parents=True)
    previous = root / "releases" / OLD
    previous.mkdir()
    (root / "current").symlink_to(previous)
    files = [
        "compose.production.yml",
        "deploy/compose.single-host.yml",
        "deploy/compose.safe-degrade.yml",
        "deploy/Caddyfile",
        "deploy/Caddyfile.safe-degrade",
    ]
    for name in files:
        shutil.copyfile(source / name, release / name)
    for name, variable in (
        ("Caddyfile.maintenance-static", "M1_OPS_STATIC_CADDY"),
        ("compose.maintenance-static.yml", "M1_OPS_STATIC_COMPOSE"),
    ):
        shutil.copyfile(os.environ[variable], release / "deploy" / name)
        files.append("deploy/" + name)

    def port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    ports = {name: port() for name in ("api", "frontend", "proxy", "tls")}
    overlay = release / "deploy/compose.ops-isolated.yml"
    overlay.write_text(
        json.dumps(
            {
                "services": {
                    "api": {"platform": "linux/amd64", "ports": [f"127.0.0.1:{ports['api']}:8000"]},
                    "frontend": {
                        "platform": "linux/amd64",
                        "ports": [f"127.0.0.1:{ports['frontend']}:3000"],
                    },
                    **{
                        name: {"platform": "linux/amd64"}
                        for name in (
                            "migrate",
                            "bootstrap-role",
                        )
                    },
                },
                "networks": {
                    "app": {
                        "internal": False,
                        "driver_opts": {"com.docker.network.bridge.enable_ip_masquerade": "false"},
                    }
                },
            }
        )
    )
    isolation = {
        "project": project,
        "overlay_sha256": hashlib.sha256(overlay.read_bytes()).hexdigest(),
    }
    files.append("deploy/compose.ops-isolated.yml")
    execution = ComposeSpec(str(root), BUSINESS, isolation)
    tools = tmp_path / "bin"
    tools.mkdir()
    docker_binary = shutil.which("docker")
    trace = tmp_path / "actual-commands.jsonl"
    wrapper = tools / "docker"
    wrapper.write_text(
        "#!"
        + sys.executable
        + "\n"
        + "import json,os,sys\n"
        + "with open(os.environ['M1_EXECUTION_TRACE'],'a') as f: "
        "f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        + "args=sys.argv[1:]\n"
        + "if args[0]=='compose': "
        "os.execv(os.environ['M1_COMPOSE_BINARY'],"
        "[os.environ['M1_COMPOSE_BINARY'],*args[1:]])\n"
        + "os.execv("
        + repr(docker_binary)
        + ",["
        + repr(docker_binary)
        + ",*args])\n"
    )
    # The trace uses literal newlines, never captured environment or config bodies.
    wrapper.chmod(0o700)
    original_path = os.environ["PATH"]
    os.environ["PATH"] = str(tools) + os.pathsep + original_path
    os.environ["M1_EXECUTION_TRACE"] = str(trace)
    os.environ["COMPOSE_PROFILES"] = "web-research,analysis,tools,restore"
    env = (source / "deploy/single-host.env.example").read_text()
    env += (
        "\n"
        + "\n".join(
            key + "=" + value
            for key, value in {
                "APP_MODE": "demo",
                "AUTH_PROVIDER": "cloudbase",
                "CLOUDBASE_ENV_ID": "synthetic-no-calls",
                "SITE_ADDRESS": "http://:80",
                "APP_PUBLIC_ORIGIN": f"http://127.0.0.1:{ports['proxy']}",
                "BIND_ADDRESS": "127.0.0.1",
                "HTTP_PORT": str(ports["proxy"]),
                "HTTPS_PORT": str(ports["tls"]),
                "POSTGRES_DATABASE": "ops_isolated",
                "POSTGRES_OWNER_USER": "ops_owner",
                "POSTGRES_OWNER_PASSWORD": "synthetic_owner",
                "APP_DATABASE_USER": "equity_app",
                "APP_DATABASE_PASSWORD": "synthetic_app",
                "DATABASE_ADMIN_URL": "postgresql+psycopg://ops_owner:synthetic_owner@database:5432/ops_isolated",
                "DATABASE_URL": "postgresql+psycopg://equity_app:synthetic_app@database:5432/ops_isolated",
                "BACKEND_IMAGE": API,
                "FRONTEND_IMAGE": FRONT,
                "SAFE_CONTROL_IMAGE": CONTROL,
                **dict.fromkeys(FLAGS, "false"),
            }.items()
        )
        + "\n"
    )
    (release / "deploy/single-host.env").write_text(env)
    (release / "deploy/single-host.env").chmod(0o600)
    modules = source / "scripts/release_ops"
    buffer = io.BytesIO()
    manifest = {}
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path in sorted(modules.glob("*.py")):
            raw = path.read_bytes()
            manifest[path.name] = hashlib.sha256(raw).hexdigest()
            item = tarfile.TarInfo("scripts/release_ops/" + path.name)
            item.size, item.mode = len(raw), 0o600
            archive.addfile(item, io.BytesIO(raw))
    raw = buffer.getvalue()
    candidate = root / "ops/candidate"
    installed = send(
        {
            "mode": "install",
            "directory": str(candidate),
            "files": manifest,
            "archive": base64.b64encode(raw).decode(),
            "archive_sha256": hashlib.sha256(raw).hexdigest(),
            "ops_package_sha256": package_sha256(),
        },
        interpreter=sys.executable,
    )
    assert installed["status"] == "PASS", installed
    blobs = Path(os.environ["M1_OPS_BLOBS"])
    proof = root / "proof"
    attempt = "isolated"
    envelopes, gates = [], {}
    binding = {
        "root": str(root),
        "attempt": attempt,
        "business_sha": BUSINESS,
        "ops_package_sha256": package_sha256(),
        "isolation": isolation,
    }
    auth = {
        **binding,
        "allowed_actions": [
            "start_api",
            "start_frontend",
            "restore_role",
            "open_normal_proxy",
            "isolate",
            "commit_current",
        ],
    }
    auth_path = root / "authorization.json"
    auth_path.write_text(json.dumps(auth))
    base_request = {
        "mode": "invoke",
        "directory": str(candidate),
        "interpreter": sys.executable,
        "ops_package_sha256": package_sha256(),
        "attempt": attempt,
        "blobs": str(blobs),
    }
    counter = 0

    def cli(operation, checkpoint, data, allow_failure=False, authorization=None):
        nonlocal counter
        counter += 1
        request = {
            **base_request,
            "operation": operation,
            "checkpoint": checkpoint,
            "evidence_root": str(proof / str(counter)),
            "input": data,
        }
        if operation in {"apply-step", "prepare-current", "apply-current"}:
            request["authorization"] = str(authorization or auth_path)
        value = send(request, interpreter=sys.executable)
        envelopes.append(value)
        if not allow_failure:
            assert value["status"] == "PASS", value
            assert value["payload"]["checker_exit_code"] == 0
        result = value["payload"]["result"]
        if result.get("evidence") and result["status"] == "PASS":
            gates[checkpoint] = {
                "evidence": result["evidence"],
                "sha256": result["evidence_sha256"],
            }
        return result

    def compose(mode, *tail):
        run = subprocess.run(
            execution.argv(mode, *tail),
            cwd=release,
            env=compose_environment(),
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert run.returncode == 0, run.stderr[-2000:]
        return run.stdout

    def apply(action, allow_failure=False):
        return cli(
            "apply-step",
            action,
            {"binding": binding, "action": action, "gates": list(gates.values())},
            allow_failure,
        )

    db = {
        "application_role": "equity_app",
        "database_container": project + "-database-1",
        "database_user": "ops_owner",
        "database_name": "ops_isolated",
    }

    def metadata(kind, extra=None):
        return cli(
            "observe-metadata",
            kind,
            {
                **db,
                "kind": kind,
                "root": str(root),
                "business_sha": BUSINESS,
                "isolation": isolation,
                **(extra or {}),
            },
        )

    def identity(role):
        ref = {"api": API, "frontend": FRONT, "safety-control": CONTROL}[role]
        data = {"reference": ref}
        if role != "safety-control":
            data["container"] = project + "-" + role + "-1"
        observed = cli("observe-daemon", "daemon-" + role, data)["actual"]
        approved = json.loads(
            (
                Path(os.environ["M1_OPS_APPROVALS"]) / ("final-" + role + "-identity-input.json")
            ).read_text()
        )["approved"]
        path = root / (role + "-identity.json")
        path.write_text(json.dumps({"approved": approved, "observed": observed}))
        cli(
            "observe-identity",
            "identity-control" if role == "safety-control" else "identity-" + role,
            {"approved": approved, "observed": observed},
        )
        return str(path)

    def http(checkpoint, layer, url, **extra):
        return cli(
            "observe-http",
            checkpoint,
            {
                "observer_location": "isolated_mac_localhost",
                "contract": {
                    "layer": layer,
                    "mode": "static_maintenance" if checkpoint == "maintenance" else "normal",
                    "url": url,
                    **extra,
                },
                "bounds": {"deadline": 45, "max_attempts": 12, "consecutive": 2},
            },
        )

    def digest():
        query = (
            "SELECT tablename FROM pg_tables WHERE schemaname='public' "
            "AND tablename<>'alembic_version' ORDER BY tablename"
        )

        def sql(q):
            r = subprocess.run(
                [
                    docker_binary,
                    "exec",
                    db["database_container"],
                    "psql",
                    "-X",
                    "-qAt",
                    "-U",
                    "ops_owner",
                    "-d",
                    "ops_isolated",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    "BEGIN READ ONLY; " + q + "; COMMIT",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert r.returncode == 0, r.stderr
            return r.stdout.strip()

        tables = sql(query).splitlines()
        return {
            table: json.loads(
                sql(
                    "SELECT json_build_object('count',count(*),'sha256',"
                    "encode(sha256(convert_to(coalesce(jsonb_agg(to_jsonb(t) "
                    "ORDER BY to_jsonb(t)::text),'[]')::text,'UTF8')),'hex')) FROM " + table + " t"
                )
            )
            for table in tables
        }

    compose_spec = {
        "kind": "compose-frozen",
        "root": str(root),
        "business_sha": BUSINESS,
        "isolation": isolation,
        "images": {
            "api": API,
            "frontend": FRONT,
            "safe-degrade-control": CONTROL,
            "safe-degrade-restore-role": CONTROL,
        },
        "disabled_flags": FLAGS,
        "configuration_sha256": {
            name: hashlib.sha256((release / name).read_bytes()).hexdigest() for name in files
        },
    }
    before = after = fault_after = None
    try:
        compose("normal", "up", "-d", "--no-deps", "--no-build", "--pull", "never", "database")
        for _ in range(60):
            value = subprocess.run(
                [docker_binary, "exec", db["database_container"], "pg_isready", "-U", "ops_owner"],
                capture_output=True,
            )
            if value.returncode == 0:
                break
            time.sleep(0.5)
        else:
            pytest.fail("isolated database not ready")
        # Only this new synthetic database is initialized; the release path stays frozen.
        compose("normal", "run", "--rm", "--no-deps", "--pull", "never", "-T", "migrate")
        compose(
            "restore",
            "run",
            "--rm",
            "--no-deps",
            "--pull",
            "never",
            "-T",
            "safe-degrade-restore-role",
        )
        compose(
            "normal",
            "up",
            "--no-start",
            "--no-deps",
            "--no-build",
            "--pull",
            "never",
            "api",
            "frontend",
        )
        apply("isolate")
        before = digest()
        assert len(before) == 42
        cli("observe-metadata", "compose-frozen", compose_spec)
        identity("api")
        identity("frontend")
        identity("safety-control")
        metadata("schema")
        metadata("role-readonly")
        metadata(
            "jobs-off",
            {
                "api_container": project + "-api-1",
                "worker_containers": [
                    project + "-web-research-worker-1",
                    project + "-investor-analysis-worker-1",
                ],
                "disabled_flags": FLAGS,
            },
        )
        metadata(
            "config-static",
            {
                "path": str(release / "deploy/Caddyfile.maintenance-static"),
                "sha256": compose_spec["configuration_sha256"][
                    "deploy/Caddyfile.maintenance-static"
                ],
                "proxy_container": project + "-proxy-1",
            },
        )
        proxy_info = json.loads(
            subprocess.check_output([docker_binary, "inspect", project + "-proxy-1"], text=True)
        )[0]
        debug = {
            "ports": proxy_info["NetworkSettings"]["Ports"],
            "bindings": proxy_info["HostConfig"]["PortBindings"],
            "site_address": next(
                v for v in proxy_info["Config"]["Env"] if v.startswith("SITE_ADDRESS=")
            ),
            "state": proxy_info["State"]["Status"],
            "logs": subprocess.run(
                [docker_binary, "logs", "--tail", "15", project + "-proxy-1"],
                capture_output=True,
                text=True,
            ).stderr,
        }
        debug_path = Path(os.environ["M1_OPS_COMPOSE_EVIDENCE"]).parent / (project + "-debug.json")
        debug_path.write_text(json.dumps(debug, indent=2))
        debug_path.chmod(0o600)
        http(
            "maintenance",
            "static_maintenance",
            f"http://127.0.0.1:{ports['proxy']}/login",
            expected_status=503,
        )
        apply("start_api")
        cli(
            "observe-health",
            "api-ready",
            {
                "container": project + "-api-1",
                "expected_test": HEALTH["api"],
                "bounds": {"deadline": 60, "max_attempts": 24},
            },
        )
        apply("start_frontend")
        cli(
            "observe-health",
            "frontend-ready",
            {
                "container": project + "-frontend-1",
                "expected_test": HEALTH["frontend"],
                "bounds": {"deadline": 60, "max_attempts": 24},
            },
        )
        apply("restore_role")
        metadata("role-normal", {"expected": {"writable_tables": 42, "rls_tables": 37}})
        identities = [identity("api"), identity("frontend")]
        prepared_path = root / "prepared.json"
        prepared = cli(
            "prepare-current",
            "prepare-current",
            {
                "root": str(root),
                "target": BUSINESS,
                "expected_old": str(previous),
                "identities": identities,
                "ops_package_sha256": package_sha256(),
                "output": str(prepared_path),
            },
        )
        serialized = json.loads(prepared_path.read_text())
        assert serialized["status"] == "PASS" and (root / "current").resolve() == previous
        assert (
            prepared["prepared_sha256"]
            == hashlib.sha256(json.dumps(serialized, sort_keys=True).encode()).hexdigest()
        )
        metadata(
            "config-normal",
            {
                "path": str(release / "deploy/Caddyfile"),
                "sha256": compose_spec["configuration_sha256"]["deploy/Caddyfile"],
            },
        )
        apply("open_normal_proxy")
        metadata(
            "config-normal-active",
            {
                "path": str(release / "deploy/Caddyfile"),
                "sha256": compose_spec["configuration_sha256"]["deploy/Caddyfile"],
                "proxy_container": project + "-proxy-1",
            },
        )
        http(
            "normal-public",
            "normal_public",
            f"http://127.0.0.1:{ports['proxy']}/login",
            marker="登录原始股雷达",
        )
        http("api-internal", "api_internal", f"http://127.0.0.1:{ports['api']}/ready")
        public = root / "public-simulated.json"
        public.write_text(
            json.dumps(
                {
                    "attempt": attempt,
                    "status": "PASS",
                    "browser_reports_read": True,
                    "temporary_routes_removed": True,
                    "continuous_successes": 2,
                    "simulation": (
                        "browser confirmation only; "
                        "no CloudBase login or production report acceptance"
                    ),
                }
            )
        )
        auth_current = root / "authorization-current.json"
        auth_current.write_text(
            json.dumps({**auth, "prepared_sha256": prepared["prepared_sha256"]})
        )
        cli(
            "apply-current",
            "current",
            {"prepared": str(prepared_path), "public_evidence": str(public)},
            authorization=auth_current,
        )
        assert (root / "current").resolve() == release
        after = digest()
        assert after == before
        # A true config refusal is retained while real cleanup stops apps and restricts the role.
        wrong = {
            **compose_spec,
            "images": {
                **compose_spec["images"],
                "api": "registry.invalid/wrong@sha256:" + "f" * 64,
            },
        }
        failure = cli("observe-metadata", "compose-frozen", wrong, True)
        assert failure["status"] == "BLOCKED"
        first = Path(failure["evidence"]).parent / "first-failure.json"
        retained = first.read_bytes()
        cleanup = apply("isolate")
        assert cleanup["read_only_verified"] is True
        repeat = apply("open_normal_proxy", True)
        assert repeat["status"] == "CHECK_ERROR"
        assert first.read_bytes() == retained
        metadata("role-readonly")
        http(
            "maintenance",
            "static_maintenance",
            f"http://127.0.0.1:{ports['proxy']}/login",
            expected_status=503,
        )
        for role in ("api", "frontend"):
            assert (
                json.loads(
                    subprocess.check_output(
                        [docker_binary, "inspect", project + "-" + role + "-1"], text=True
                    )
                )[0]["State"]["Running"]
                is False
            )
        fault_after = digest()
        assert fault_after == before
        commands = [json.loads(line) for line in trace.read_text().splitlines()]
        normal_opens = [
            v
            for v in commands
            if v[0] == "compose"
            and "up" in v
            and v[-1] == "proxy"
            and "deploy/compose.maintenance-static.yml" not in v
        ]
        assert len(normal_opens) == 1
        outcome = {
            "status": "PASS",
            "project": project,
            "root": str(root),
            "business_sha": BUSINESS,
            "ops_package_sha256": package_sha256(),
            "archive_sha256": installed["payload"]["archive_sha256"],
            "isolation": isolation,
            "ports": ports,
            "browser_confirmation": "SIMULATED",
            "normal_open_count": len(normal_opens),
            "before": before,
            "after": after,
            "fault_after": fault_after,
            "current_after_success": str(release),
            "envelopes": envelopes,
            "actual_commands": commands,
            "reports_usage_mappings_added": 0,
            "provider_model_calls": 0,
        }
        out = Path(os.environ["M1_OPS_COMPOSE_EVIDENCE"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(outcome, indent=2, ensure_ascii=False))
        out.chmod(0o600)
    finally:
        try:
            compose("normal", "down", "--volumes", "--remove-orphans")
        finally:
            os.environ["PATH"] = original_path
            os.environ.pop("M1_EXECUTION_TRACE", None)
            os.environ.pop("COMPOSE_PROFILES", None)
