"""Bounded readonly production metadata. Never outputs Env, credentials or business rows."""

import datetime
import json
import pathlib
import subprocess
from urllib.parse import urlsplit


def run(argv):
    p = subprocess.run(argv, text=True, capture_output=True, timeout=20)
    if p.returncode:
        raise RuntimeError("readonly metadata command failed: " + str(p.returncode))
    return p.stdout.strip()


def observe_host(spec):
    root = pathlib.Path(spec["root"])
    db_name = spec["database_container"]
    db = json.loads(run(["docker", "inspect", db_name]))[0]
    env = dict(v.split("=", 1) for v in db["Config"]["Env"])

    def sql(q):
        return json.loads(
            run(
                [
                    "docker",
                    "exec",
                    db_name,
                    "psql",
                    "-X",
                    "-qAt",
                    "-U",
                    env["POSTGRES_USER"],
                    "-d",
                    env["POSTGRES_DB"],
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    "BEGIN READ ONLY; " + q + "; COMMIT",
                ]
            )
        )

    query = """SELECT jsonb_build_object(
     'schema',(SELECT version_num FROM alembic_version),
     'role',(SELECT
        jsonb_build_object('superuser',rolsuper,'bypassrls',rolbypassrls,'createdb',rolcreatedb,'createrole',rolcreaterole,'replication',rolreplication)
       FROM pg_roles WHERE rolname='equity_app'),
     'owned_relations',(SELECT count(*) FROM pg_class WHERE relowner=(SELECT oid FROM
        pg_roles WHERE rolname='equity_app')),
     'memberships',(SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM
        pg_roles WHERE rolname='equity_app')),
     'schema_create',has_schema_privilege('equity_app','public','CREATE'),
     'watchlist_function_execute',has_function_privilege('equity_app','public.watchlist_monitor_targets()','EXECUTE'),
     'tables',(SELECT
        jsonb_agg(jsonb_build_object('name',c.relname,'rls',c.relrowsecurity,'forced_rls',c.relforcerowsecurity,
       'select',has_table_privilege('equity_app',c.oid,'SELECT'),'insert',has_table_privilege('equity_app',c.oid,'INSERT'),
       'update',has_table_privilege('equity_app',c.oid,'UPDATE'),'delete',has_table_privilege('equity_app',c.oid,'DELETE'),
       'truncate',has_table_privilege('equity_app',c.oid,'TRUNCATE'),'references',has_table_privilege('equity_app',c.oid,'REFERENCES'),
       'trigger',has_table_privilege('equity_app',c.oid,'TRIGGER')) ORDER BY c.relname)
       FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
        AND c.relkind='r'),
     'policies_sha256',(SELECT encode(sha256(convert_to(coalesce(jsonb_agg(to_jsonb(p) ORDER
        BY schemaname,tablename,policyname),'[]')::text,'UTF8')),'hex') FROM pg_policies p
        WHERE schemaname='public'),
     'active_refresh',(SELECT count(*) FROM refresh_jobs WHERE status IN
        ('pending','queued','running','leased')),
     'active_research',(SELECT count(*) FROM company_research_jobs WHERE status IN
        ('pending','queued','running','leased')))"""
    database = sql(query)
    containers = {}
    for role in ["api", "frontend", "database", "proxy"]:
        value = json.loads(run(["docker", "inspect", spec["project"] + "-" + role + "-1"]))[0]
        x = {
            "id": value["Id"],
            "image": value["Config"]["Image"],
            "running": value["State"]["Running"],
            "status": value["State"]["Status"],
            "command": value["Config"]["Cmd"],
            "entrypoint": value["Config"]["Entrypoint"],
            "health_test": value["Config"].get("Healthcheck", {}).get("Test"),
        }
        if role in ["api", "frontend"]:
            e = dict(v.split("=", 1) for v in value["Config"]["Env"])
            x["auth_provider"] = e.get("AUTH_PROVIDER")
            if role == "frontend":
                x["public_origin"] = e.get("APP_PUBLIC_ORIGIN")
            if role == "api":
                x["database_application_role"] = urlsplit(e["DATABASE_URL"]).username
        containers[role] = x
    locks = []
    for p in [root / ".release.lock", *root.glob(".opened-*.json")]:
        if p.exists():
            locks.append(
                {
                    "name": p.name,
                    "mode": oct(p.stat().st_mode & 0o777),
                    "content": p.read_text()[:1024],
                }
            )
    output = {
        "observed_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "host": run(["hostname"]),
        "architecture": run(["uname", "-m"]),
        "python": run(["python3", "--version"]),
        "database_user": env["POSTGRES_USER"],
        "database_name": env["POSTGRES_DB"],
        "database": database,
        "containers": containers,
        "current": str((root / "current").resolve()),
        "locks": locks,
        "running_application_containers": [
            n
            for n in run(["docker", "ps", "--format", "{{.Names}}"]).splitlines()
            if n.startswith("dealflow-radar")
        ],
        "compose_version": run(["docker", "compose", "version"]),
    }
    return output
