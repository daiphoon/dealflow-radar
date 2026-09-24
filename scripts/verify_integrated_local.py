"""可审计全量本地验证：创建全新 PostgreSQL 容器和虚构基库，完成后只清理本脚本资源。"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tests", nargs="*", default=[])
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(Path("data/private").resolve()):
        parser.error("logs must be private")
    output.mkdir(parents=True, exist_ok=False)
    name = "dealflow-integrated-" + secrets.token_hex(5)
    owner_password, app_password = secrets.token_hex(24), secrets.token_hex(24)
    env = {
        **os.environ,
        "POSTGRES_PASSWORD": owner_password,
        "APP_MODE": "demo",
        "EXTERNAL_CALLS_ENABLED": "false",
        "PAID_API_CALLS_ENABLED": "false",
        "AUTO_REFRESH_ENABLED": "false",
        "AUTO_PUBLISH_ENABLED": "false",
        "TRUSTED_SOURCE_CALLS_ENABLED": "false",
        "RUN_EXTERNAL_TESTS": "false",
    }
    steps = []

    def run(label, command, custom=None):
        started = time.monotonic()
        with (output / (label + ".log")).open("w") as log:
            result = subprocess.run(
                command, env=custom or env, stdout=log, stderr=subprocess.STDOUT
            )
        steps.append(
            {
                "step": label,
                "command": command,
                "exit_code": result.returncode,
                "seconds": round(time.monotonic() - started, 2),
            }
        )
        (output / "manifest.json").write_text(
            json.dumps(
                {
                    "environment": "isolated_local_postgresql16",
                    "external_research_calls": 0,
                    "steps": steps,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print(label, result.returncode, flush=True)
        if result.returncode:
            raise RuntimeError("local verification failed: " + label)

    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--tmpfs",
                "/var/lib/postgresql/data:rw,size=2g",
                "-e",
                "POSTGRES_PASSWORD",
                "-e",
                "POSTGRES_DB=integrated_test",
                "-p",
                "127.0.0.1::5432",
                "postgres:16",
            ],
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        port = (
            subprocess.check_output(["docker", "port", name, "5432/tcp"], text=True)
            .strip()
            .rsplit(":", 1)[1]
        )
        for _ in range(30):
            if (
                subprocess.run(
                    ["docker", "exec", name, "pg_isready", "-U", "postgres"],
                    stdout=subprocess.DEVNULL,
                ).returncode
                == 0
            ):
                break
            time.sleep(1)
        admin = f"postgresql+psycopg://postgres:{owner_password}@127.0.0.1:{port}/integrated_test"
        app = f"postgresql+psycopg://integrated_app:{app_password}@127.0.0.1:{port}/integrated_test"
        env.update(
            DATABASE_ADMIN_URL=admin,
            DATABASE_URL=app,
            POSTGRES_RLS_DATABASE_URL=app,
            APP_DATABASE_USER="integrated_app",
            APP_DATABASE_PASSWORD=app_password,
        )
        owner_env = {**env, "DATABASE_URL": admin}
        run("migrate", [sys.executable, "-m", "alembic", "upgrade", "head"], owner_env)
        run("drift", [sys.executable, "-m", "alembic", "check"], owner_env)
        run("seed", [sys.executable, "-m", "scripts.seed_demo"], owner_env)
        run("role", [sys.executable, "-m", "scripts.bootstrap_local_database"])
        run("backend", [sys.executable, "-m", "pytest", "-q", "-ra", *args.tests])
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


if __name__ == "__main__":
    main()
