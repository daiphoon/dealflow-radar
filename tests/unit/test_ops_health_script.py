from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HEALTH_SCRIPT = REPOSITORY_ROOT / "deploy" / "health-check.sh"


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _fixture_environment(tmp_path: Path, *, include_worker: bool) -> dict[str, str]:
    deploy_directory = tmp_path / "release"
    (deploy_directory / "deploy").mkdir(parents=True)
    (deploy_directory / "compose.production.yml").write_text("services: {}\n", encoding="utf-8")
    (deploy_directory / "deploy" / "compose.single-host.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    environment_file = tmp_path / "single-host.env"
    environment_file.write_text(
        "APP_PUBLIC_ORIGIN=https://app.example.test\n"
        "ON_DEMAND_RESEARCH_ENABLED=true\n"
        f"UNRELATED_SECRET=$(touch {tmp_path / 'must-not-exist'})\n",
        encoding="utf-8",
    )

    fake_docker = tmp_path / "docker"
    _write_executable(
        fake_docker,
        """#!/bin/sh
set -eu
if [ "$1" = "inspect" ]; then
  echo healthy
  exit 0
fi
case " $* " in
  *" ps --status running --services "*)
    printf '%s\n' database api frontend proxy
    if [ "${FAKE_INCLUDE_WORKER:-false}" = "true" ]; then
      echo on-demand-research-worker
    fi
    ;;
  *" ps -q "*)
    for argument in "$@"; do service="$argument"; done
    printf 'container-%s\n' "$service"
    ;;
  *)
    echo "unexpected docker invocation" >&2
    exit 2
    ;;
esac
""",
    )
    fake_curl = tmp_path / "curl"
    _write_executable(fake_curl, "#!/bin/sh\nexit 0\n")
    fake_df = tmp_path / "df"
    _write_executable(
        fake_df,
        "#!/bin/sh\nprintf 'Filesystem 1024-blocks Used Available Capacity Mounted\\n'\n"
        "printf '/dev/test 100 20 80 20%% /test\\n'\n",
    )
    return {
        **os.environ,
        "DEALFLOW_RADAR_DEPLOY_DIRECTORY": str(deploy_directory),
        "DEALFLOW_RADAR_ENV_FILE": str(environment_file),
        "DOCKER_BIN": str(fake_docker),
        "CURL_BIN": str(fake_curl),
        "DF_BIN": str(fake_df),
        "FAKE_INCLUDE_WORKER": "true" if include_worker else "false",
    }


def test_health_check_requires_healthy_research_worker_when_queue_is_enabled(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        ["sh", str(HEALTH_SCRIPT)],
        env=_fixture_environment(tmp_path, include_worker=True),
        check=True,
        capture_output=True,
        text=True,
    )

    assert "status=healthy" in result.stdout
    assert "research_queue=true" in result.stdout
    assert not (tmp_path / "must-not-exist").exists()


def test_health_check_fails_when_enabled_research_worker_is_missing(tmp_path: Path) -> None:
    result = subprocess.run(
        ["sh", str(HEALTH_SCRIPT)],
        env=_fixture_environment(tmp_path, include_worker=False),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "on-demand-research-worker" in result.stderr
