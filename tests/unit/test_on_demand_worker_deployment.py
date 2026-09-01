from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_single_host_research_worker_uses_private_persistent_cache_mount() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker compose is required for the deployment contract test")

    environment = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": os.environ["PATH"],
        "TIANYANCHA_AUTHORIZATION": "",
        "TIANYANCHA_AUTHORIZATION_PATH": "/dev/null",
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "research",
            "--env-file",
            "deploy/single-host.env.example",
            "-f",
            "compose.production.yml",
            "-f",
            "deploy/compose.single-host.yml",
            "config",
            "--format",
            "json",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    config = json.loads(result.stdout)
    api = config["services"]["api"]
    worker = config["services"]["on-demand-research-worker"]

    assert worker["profiles"] == ["research"]
    assert worker["command"] == [
        "python",
        "-m",
        "scripts.run_on_demand_research_worker",
    ]
    assert worker["restart"] == "unless-stopped"
    assert worker["read_only"] is True
    assert worker["cap_drop"] == ["ALL"]
    assert worker["environment"]["EXTERNAL_CALLS_ENABLED"] == "false"
    assert worker["environment"]["ON_DEMAND_RESEARCH_ENABLED"] == "false"
    assert worker["environment"]["TIANYANCHA_IDENTITY_CALLS_ENABLED"] == "false"
    assert worker["environment"]["TIANYANCHA_RESEARCH_CALLS_ENABLED"] == "false"
    assert worker["environment"]["PAID_API_CALLS_ENABLED"] == "false"
    assert worker["environment"]["AUTO_REFRESH_ENABLED"] == "false"
    assert worker["environment"]["AUTO_PUBLISH_ENABLED"] == "false"
    assert worker["environment"]["TIANYANCHA_AUTHORIZATION"] == ""
    assert (
        worker["environment"]["TIANYANCHA_AUTHORIZATION_FILE"]
        == "/run/secrets/tianyancha-authorization"
    )
    assert worker["environment"]["ON_DEMAND_WORKER_POLL_SECONDS"] == "5"
    assert worker["healthcheck"]["test"][-1] == "--healthcheck"
    assert len(worker["volumes"]) == 2
    cache_mount = next(
        mount
        for mount in worker["volumes"]
        if mount["target"] == "/app/data/private/provider_cache"
    )
    assert cache_mount["type"] == "bind"
    assert cache_mount["source"] == "/opt/dealflow-radar/private/provider_cache"
    assert cache_mount["target"] == "/app/data/private/provider_cache"
    secret_mount = next(
        mount
        for mount in worker["volumes"]
        if mount["target"] == "/run/secrets/tianyancha-authorization"
    )
    assert secret_mount["type"] == "bind"
    assert secret_mount["source"] == "/dev/null"
    assert secret_mount["read_only"] is True

    # Older Compose releases omit an explicit default false from rendered JSON.
    # Keep the source-level assertion here; CI also proves fail-closed behavior
    # by attempting to start the worker with a missing host directory.
    single_host_override = (REPOSITORY_ROOT / "deploy" / "compose.single-host.yml").read_text(
        encoding="utf-8"
    )
    assert (
        """        bind:
          create_host_path: false
"""
        in single_host_override
    )
    assert not api.get("volumes")
