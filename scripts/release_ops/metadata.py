"""Finite release metadata checks; no business rows, credentials, or write SQL."""

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from .evidence import command, redact
from .probes import mounted_configuration
from .snapshot import json_command


def database(spec):
    role = spec["application_role"]
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError("invalid role name")
    query = f"""SELECT json_build_object(
      'schema',(SELECT version_num FROM alembic_version),
      'writable_tables',(SELECT count(*) FROM information_schema.tables
        WHERE table_schema='public' AND has_table_privilege('{role}',
        quote_ident(table_schema)||'.'||quote_ident(table_name),'INSERT,UPDATE,DELETE')),
      'role_safe',(SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname='{role}'),
      'active_refresh',(SELECT count(*) FROM refresh_jobs
        WHERE status IN ('pending','queued','running','leased')),
      'active_research',(SELECT count(*) FROM company_research_jobs
        WHERE status IN ('pending','queued','running','leased')),
      'rls_tables',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relrowsecurity))"""
    return command(
        [
            "docker",
            "exec",
            spec["database_container"],
            "psql",
            "-X",
            "-qAt",
            "-U",
            spec["database_user"],
            "-d",
            spec["database_name"],
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "BEGIN READ ONLY; " + query + "; COMMIT",
        ],
    )


def evaluate_database(kind, actual, expected):
    if kind == "schema":
        good = actual["schema"] == "0036"
    elif kind == "role-readonly":
        good = actual["role_safe"] is True and actual["writable_tables"] == 0
    elif kind == "role-normal":
        # Exact previously reviewed counts, not merely "has some write permission".
        good = actual["role_safe"] is True and all(actual[k] == v for k, v in expected.items())
        good = good and {"writable_tables", "rls_tables"} <= set(expected)
    else:
        raise ValueError("unknown database metadata check")
    return {"status": "PASS" if good else "BLOCKED", "actual": actual}


def observe_metadata(spec):
    kind = spec["kind"]
    if kind == "compose-frozen":
        from .actions import commands

        directory, normal = commands(spec["root"], spec["business_sha"], "open_normal_proxy")
        argv = normal[0][:-2] + [
            "-f",
            "deploy/compose.safe-degrade.yml",
            "config",
            "--format",
            "json",
        ]
        run = subprocess.run(argv, cwd=directory, capture_output=True, text=True, timeout=20)
        if run.returncode:
            return {
                "status": "CHECK_ERROR",
                "exit_code": run.returncode,
                "stderr": redact(run.stderr),
            }
        services = json.loads(run.stdout)["services"]
        images = {
            k: services[k]["image"]
            for k in ("api", "frontend", "safe-degrade-control", "safe-degrade-restore-role")
        }
        expected = spec["images"]
        if set(images) != set(expected) or not spec["configuration_sha256"]:
            raise ValueError("complete image/configuration binding required")
        hashes = {
            f: hashlib.sha256((directory / f).read_bytes()).hexdigest()
            for f in spec["configuration_sha256"]
        }
        # Normal configuration must also have all approved switches off;
        # the safe overlay alone cannot prove the normal runtime is closed.
        raw = subprocess.run(
            normal[0][:-2] + ["config", "--format", "json"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if raw.returncode:
            return {
                "status": "CHECK_ERROR",
                "exit_code": raw.returncode,
                "stderr": redact(raw.stderr),
            }
        regular = json.loads(raw.stdout)["services"]
        flags = {
            k: str(regular["api"]["environment"].get(k, "MISSING")) for k in spec["disabled_flags"]
        }
        good = (
            images == expected
            and hashes == spec["configuration_sha256"]
            and all(regular[k]["image"] == expected[k] for k in ("api", "frontend"))
            and bool(flags)
            and all(v.lower() == "false" for v in flags.values())
        )
        return {
            "status": "PASS" if good else "BLOCKED",
            "exit_code": 0,
            "actual": {"images": images, "configurations": hashes, "closed_flags": flags},
        }
    if kind in {"schema", "role-readonly", "role-normal", "jobs-off"}:
        result = database(spec)
        if result["status"] != "PASS":
            return result
        actual = result["actual"]
        if kind != "jobs-off":
            return {**result, **evaluate_database(kind, actual, spec.get("expected", {}))}
        running = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=10
        )
        if running.returncode:
            return {"status": "CHECK_ERROR", "exit_code": running.returncode}
        api = json_command(["docker", "inspect", spec["api_container"]])[0]
        # Read only reviewed booleans in memory. Never persist inspect Env.
        env = dict(v.split("=", 1) for v in api["Config"]["Env"])
        names = spec["disabled_flags"]
        if not names or any(not v.endswith("_ENABLED") for v in names):
            raise ValueError("closed flag allowlist required")
        flags = {v: env.get(v, "MISSING") for v in names}
        good = (
            actual["active_refresh"] == actual["active_research"] == 0
            and not set(spec["worker_containers"]) & set(running.stdout.splitlines())
            and all(v.lower() == "false" for v in flags.values())
        )
        return {
            "status": "PASS" if good else "BLOCKED",
            "exit_code": 0,
            "actual": {
                "database": actual,
                "closed_flags": flags,
                "workers_running": sorted(
                    set(spec["worker_containers"]) & set(running.stdout.splitlines())
                ),
            },
        }
    if kind in {"config-normal", "config-static", "config-normal-active"}:
        path = Path(spec["path"]).resolve(strict=True)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if kind == "config-normal":
            return {
                "status": "PASS" if sha == spec["sha256"] else "BLOCKED",
                "actual": {"sha256": sha, "scope": "candidate_file_only"},
            }
        proxy = json_command(["docker", "inspect", spec["proxy_container"]])[0]
        result = command(
            ["docker", "exec", spec["proxy_container"], "sha256sum", "/etc/caddy/Caddyfile"],
            expect_json=False,
        )
        if result["status"] != "PASS":
            return result
        mounts = [v for v in proxy["Mounts"] if v["Destination"] == "/etc/caddy/Caddyfile"]
        started = datetime.fromisoformat(proxy["State"]["StartedAt"].replace("Z", "+00:00"))
        argv = [proxy["Path"], *proxy["Args"]]
        uses = (
            proxy["State"]["Running"]
            and "caddy" in Path(argv[0]).name
            and "run" in argv
            and "--config" in argv
            and argv[argv.index("--config") + 1] == "/etc/caddy/Caddyfile"
        )
        return {
            **result,
            **mounted_configuration(
                spec["sha256"],
                sha,
                result["stdout"].split()[0],
                started_after_file=started.timestamp() >= path.stat().st_mtime,
                process_uses_config=uses,
                read_only_mount=len(mounts) == 1
                and not mounts[0]["RW"]
                and mounts[0]["Source"] == str(path),
            ),
            "target_container_id": proxy["Id"],
        }
    raise ValueError("unsupported metadata checkpoint")
