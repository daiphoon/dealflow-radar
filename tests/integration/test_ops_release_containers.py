"""Opt-in local-only recovery of frozen images, with a new empty PostgreSQL database."""

import hashlib
import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from scripts.release_ops.current import commit, prepare
from scripts.release_ops.evidence import Recorder, package_sha256
from scripts.release_ops.identity import verify_identity
from scripts.release_ops.metadata import observe_metadata
from scripts.release_ops.probes import Contract, wait_ready
from scripts.release_ops.snapshot import observe_daemon

BUSINESS = "47c4a5ce82731b6b9a03899012c47ff84d2d7abd"
API = (
    "ghcr.io/daiphoon/dealflow-radar-api@"
    "sha256:ee944823816b1a20fdd600bfd02594aaf0523f09b853a5f3dd3a20f8f53c41b0"
)
FRONT = (
    "ghcr.io/daiphoon/dealflow-radar-frontend@"
    "sha256:97bf6b3a5218efa6457cb636971bd32c809911c40a5a61a10261c1f030c80db4"
)
CONTROL = (
    "ghcr.io/daiphoon/dealflow-radar-safety-control@"
    "sha256:4a426d238f86dd2964b71f946b3ccd2d0d94ace7e8f7d077dd2485d10a822710"
)


def test_frozen_images_isolated_ops_recovery(tmp_path):
    if os.getenv("M1_OPS_ISOLATED") != "1":
        pytest.skip("requires explicit local frozen-image rehearsal")
    assert (
        subprocess.check_output(["docker", "context", "show"], text=True).strip() == "desktop-linux"
    )
    assert not os.getenv("DOCKER_HOST") and not os.getenv("DOCKER_CONTEXT")
    blobs_dir = Path(os.environ["M1_OPS_BLOBS"])
    blobs = {"sha256:" + p.name: p.read_bytes() for p in blobs_dir.iterdir()}
    static = Path(os.environ["M1_OPS_STATIC_CADDY"]).resolve()
    assert (
        hashlib.sha256(static.read_bytes()).hexdigest()
        == "f194fe1f31617b5fa24ce7c087fbb672d5e8ea1c2fd387bc24e47a6e61cfd88b"
    )
    suffix = uuid4().hex[:10]
    network = "m1-ops-isolated-" + suffix
    names = {v: network + "-" + v for v in ["db", "api", "frontend", "proxy"]}
    created = []

    def docker(*argv):
        r = subprocess.run(["docker", *argv], capture_output=True, text=True, timeout=100)
        assert r.returncode == 0, r.stderr[-1500:]
        return r.stdout.strip()

    def env(values):
        return [item for k, v in values.items() for item in ["-e", k + "=" + v]]

    def run(role, image, values, extra=()):
        docker(
            "run",
            "--pull",
            "never",
            "-d",
            "--name",
            names[role],
            "--network",
            network,
            "--network-alias",
            role,
            *extra,
            *env(values),
            image,
        )
        created.append(names[role])

    admin = "postgresql+psycopg://ops_owner:synthetic_owner@db:5432/ops_isolated"
    app = "postgresql+psycopg://equity_app:synthetic_app@db:5432/ops_isolated"
    role_env = {
        "DATABASE_ADMIN_URL": admin,
        "APP_DATABASE_USER": "equity_app",
        "APP_DATABASE_PASSWORD": "synthetic_app",
    }

    def control(*args):
        return docker(
            "run", "--rm", "--pull", "never", "--network", network, *env(role_env), CONTROL, *args
        )

    rec = Recorder(tmp_path / "evidence", "isolated")
    identities = []

    def internal_probe(contract):
        run = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "-w",
                "/ops",
                names["api"],
                "python",
                "-m",
                "scripts.release_ops.http_worker",
            ],
            input=json.dumps(contract.__dict__),
            text=True,
            capture_output=True,
            timeout=10,
        )
        assert run.returncode == 0, run.stderr
        return json.loads(run.stdout)

    def probe(label, contract):
        result = wait_ready(
            lambda timeout: internal_probe(replace(contract, timeout=timeout)),
            lambda i, f: rec.check(
                f"{label}-{i}",
                "isolated_api_container",
                contract.layer,
                contract.__dict__,
                "http",
                f,
            ),
            deadline=90,
            max_attempts=30,
        )
        assert result["status"] == "PASS", result
        return result

    def sql(q):
        return docker(
            "exec", names["db"], "psql", "-U", "ops_owner", "-d", "ops_isolated", "-At", "-c", q
        )

    def counts():
        return json.loads(
            sql(
                "SELECT json_build_object("
                "'reports',(SELECT count(*) FROM personal_company_reports),"
                "'usage',(SELECT count(*) FROM personal_usage_records),"
                "'requests',(SELECT count(*) FROM personal_report_requests),"
                "'schema',(SELECT version_num FROM alembic_version))"
            )
        )

    docker("network", "create", "--internal", network)
    try:
        run(
            "db",
            "postgres:16",
            {
                "POSTGRES_USER": "ops_owner",
                "POSTGRES_PASSWORD": "synthetic_owner",
                "POSTGRES_DB": "ops_isolated",
            },
        )
        for _ in range(40):
            r = subprocess.run(
                ["docker", "exec", names["db"], "pg_isready", "-U", "ops_owner"],
                capture_output=True,
            )
            if not r.returncode:
                break
            time.sleep(0.25)
        else:
            pytest.fail("isolated PostgreSQL did not become ready")
        docker(
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            network,
            "-e",
            "DATABASE_URL=" + admin,
            API,
            "alembic",
            "upgrade",
            "head",
        )
        control("python", "-m", "scripts.bootstrap_local_database")
        baseline = counts()
        assert baseline == {"reports": 0, "usage": 0, "requests": 0, "schema": "0036"}
        assert json.loads(control("python", "-m", "scripts.safe_degrade_database", "restrict"))[
            "read_only"
        ]
        metadata_spec = {
            "application_role": "equity_app",
            "database_container": names["db"],
            "database_user": "ops_owner",
            "database_name": "ops_isolated",
        }
        readonly_proof = observe_metadata({**metadata_spec, "kind": "role-readonly"})
        assert readonly_proof["status"] == "PASS", readonly_proof
        run(
            "api",
            API,
            {
                "DATABASE_URL": app,
                "APP_MODE": "demo",
                "EXTERNAL_CALLS_ENABLED": "false",
                "PAID_API_CALLS_ENABLED": "false",
                "AUTO_REFRESH_ENABLED": "false",
                "AUTO_PUBLISH_ENABLED": "false",
                "WEB_RESEARCH_ENABLED": "false",
            },
            ["-v", str(Path("scripts").resolve()) + ":/ops/scripts:ro"],
        )
        api_url = "http://api:8000"
        probe("api-ready", Contract("api_internal", api_url + "/ready", "normal"))
        run(
            "frontend",
            FRONT,
            {
                "AUTH_PROVIDER": "cloudbase",
                "API_BASE_URL": "http://api:8000",
                "APP_PUBLIC_ORIGIN": "http://localhost",
            },
            [],
        )
        front_url = "http://frontend:3000"
        probe(
            "frontend-ready",
            Contract("frontend_internal", front_url + "/login", "normal", marker="登录"),
        )
        for role, ref in [("api", API), ("frontend", FRONT)]:
            md = ref.split("@")[1]
            config = json.loads(blobs[md])["config"]["digest"]
            approved = {
                "role": role,
                "reference": ref,
                "config_digest": config,
                "platform": "linux/amd64",
                "revision": BUSINESS,
            }
            observed = observe_daemon(ref, names[role])
            assert verify_identity(approved, blobs, observed)["status"] == "PASS"
            identities.append((approved, blobs, observed))
        run(
            "proxy",
            "caddy:2.10.2-alpine",
            {"SITE_ADDRESS": ":80"},
            ["-v", str(static) + ":/etc/caddy/Caddyfile:ro"],
        )
        proxy_url = "http://proxy"
        probe(
            "maintenance",
            Contract(
                "static_maintenance",
                proxy_url + "/login",
                "static_maintenance",
                expected_status=503,
            ),
        )
        control("python", "-m", "scripts.bootstrap_local_database")
        normal_proof = observe_metadata(
            {
                **metadata_spec,
                "kind": "role-normal",
                "expected": {"writable_tables": 42, "rls_tables": 37},
            }
        )
        assert normal_proof["status"] == "PASS", normal_proof
        root = tmp_path / "release-root"
        (root / "releases" / BUSINESS).mkdir(parents=True)
        old = root / "releases" / ("a" * 40)
        old.mkdir()
        (root / "current").symlink_to(old)
        prepared = prepare(root, "isolated", BUSINESS, str(old), identities, package_sha256())
        assert prepared["status"] == "PASS"
        assert (root / "current").resolve() == old
        docker("rm", "-f", names["proxy"])
        created.remove(names["proxy"])
        run(
            "proxy",
            "caddy:2.10.2-alpine",
            {"SITE_ADDRESS": ":80"},
            [
                "-v",
                str(Path("deploy/Caddyfile").resolve()) + ":/etc/caddy/Caddyfile:ro",
            ],
        )
        proxy_url = "http://proxy"
        result = probe(
            "normal-entry", Contract("normal_public", proxy_url + "/login", "normal", marker="登录")
        )
        # Synthetic completion signal tests pointer machinery; not a production browser assertion.
        public = {
            "attempt": "isolated",
            "status": "PASS",
            "browser_reports_read": True,
            "temporary_routes_removed": True,
            "continuous_successes": result["continuous_successes"],
        }
        auth = {k: prepared[k] for k in ["attempt", "business_sha", "ops_package_sha256"]}
        auth.update(
            allowed_actions=["commit_current"],
            prepared_sha256=hashlib.sha256(
                json.dumps(prepared, sort_keys=True).encode()
            ).hexdigest(),
        )
        assert commit(prepared, auth, public)["status"] == "PASS"
        assert commit(prepared, auth, public)["actual"] == "already_committed"
        assert counts() == baseline
        output = {
            "status": "PASS",
            "business_sha": BUSINESS,
            "images": [API, FRONT, CONTROL],
            "context": "desktop-linux",
            "internal_network": True,
            "production_contacted": False,
            "reports_usage_requests_before_after": baseline,
            "identity_checks": 2,
            "readonly_role": readonly_proof["actual"],
            "normal_role": normal_proof["actual"],
            "normal_entry_continuous_successes": 2,
            "current_idempotent": True,
            "browser_report_signal": "synthetic-only; production browser acceptance still required",
        }
        if os.getenv("M1_OPS_EVIDENCE"):
            Path(os.environ["M1_OPS_EVIDENCE"]).write_text(json.dumps(output, indent=2))
    finally:
        for name in reversed(created):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)
