"""Finite release metadata checks; no business rows, credentials, or write SQL."""

import hashlib
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .evidence import command
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
      'role_safe',(SELECT NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole
                              OR rolreplication) FROM pg_roles WHERE rolname='{role}'),
      'owned_relations',(SELECT count(*) FROM pg_class
                        WHERE relowner=(SELECT oid FROM pg_roles WHERE rolname='{role}')),
      'memberships',(SELECT count(*) FROM pg_auth_members
                     WHERE member=(SELECT oid FROM pg_roles WHERE rolname='{role}')),
      'migration_write',has_table_privilege('{role}','public.alembic_version','INSERT,UPDATE,DELETE'),
      'critical_rls',(SELECT count(*)=4 AND bool_and(relrowsecurity) FROM pg_class c
                      JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
                      AND c.relname IN ('personal_report_requests','personal_company_reports',
                                       'personal_watchlist_items','personal_event_view_receipts')),
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


def ordinary_role(actual):
    return (
        actual["role_safe"] is True
        and actual["owned_relations"] == 0
        and actual["memberships"] == 0
        and actual["migration_write"] is False
        and actual["critical_rls"] is True
    )


def evaluate_database(kind, actual, expected):
    if kind == "schema":
        good = actual["schema"] == "0036"
    elif kind == "role-readonly":
        good = ordinary_role(actual) and actual["writable_tables"] == 0
    elif kind == "role-normal":
        # Exact previously reviewed counts, not merely "has some write permission".
        good = ordinary_role(actual) and all(actual[k] == v for k, v in expected.items())
        good = good and {"writable_tables", "rls_tables"} <= set(expected)
    else:
        raise ValueError("unknown database metadata check")
    return {"status": "PASS" if good else "BLOCKED", "actual": actual}


def observe_metadata(spec):
    kind = spec["kind"]
    if kind == "compose-frozen":
        from .compose import observe_frozen

        return observe_frozen(spec)
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
        sources = [str(path)]
        if spec.get("isolation") is not None:
            from .compose import ComposeSpec

            isolated = ComposeSpec(spec["root"], spec["business_sha"], spec["isolation"])
            if (
                spec["proxy_container"] != isolated.project + "-proxy-1"
                or path.parent != isolated.directory / "deploy"
            ):
                raise PermissionError("isolated mount must belong to approved release/project")
            if sys.platform == "darwin":
                # Docker Desktop may report this daemon-side alias for the same host bind.
                sources.append("/host_mnt" + str(path))

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
                and mounts[0]["Source"] in sources,
            ),
            "configuration_adoption": {
                "expected_source": str(path),
                "allowed_isolated_sources": sources,
                "mounts": [{k: v[k] for k in ("Source", "Destination", "RW")} for v in mounts],
                "started_at": proxy["State"]["StartedAt"],
                "host_file_mtime": path.stat().st_mtime,
                "started_after_file": started.timestamp() >= path.stat().st_mtime,
                "process_uses_config": uses,
                "argv": argv,
                "host_sha256": sha,
                "mounted_sha256": result["stdout"].split()[0],
            },
            "target_container_id": proxy["Id"],
        }
    raise ValueError("unsupported metadata checkpoint")
