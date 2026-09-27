"""The finite test mapping cannot inject deployment actions or relax production targets."""

import hashlib
import json

import pytest

from scripts.release_ops.actions import commands
from scripts.release_ops.compose import ComposeSpec, compose_environment


def test_config_and_actions_use_the_same_explicit_spec(tmp_path, monkeypatch):
    (tmp_path / "releases" / ("b" * 40)).mkdir(parents=True)
    monkeypatch.setenv("COMPOSE_PROFILES", "tools,analysis,safe-restore")
    monkeypatch.setenv("COMPOSE_FILE", "unreviewed.yml")
    spec = ComposeSpec(str(tmp_path), "b" * 40)
    normal = spec.argv("normal", "config", "--format", "json")
    _, actions = commands(tmp_path, "b" * 40, "start_api")
    assert normal == spec.argv("normal") + ["config", "--format", "json"]
    assert actions[0] == spec.argv("normal") + [
        "up",
        "-d",
        "--no-deps",
        "--no-build",
        "--pull",
        "never",
        "api",
    ]
    assert "--profile" not in actions[0] and actions[0][-1] == "api"
    assert "COMPOSE_PROFILES" not in compose_environment()
    assert "COMPOSE_FILE" not in compose_environment()
    assert "tools,analysis,safe-restore" in __import__("os").environ["COMPOSE_PROFILES"]
    _, isolate = commands(tmp_path, "b" * 40, "isolate")
    assert isolate[0][-1] == "proxy" and "deploy/compose.maintenance-static.yml" in isolate[0]
    assert "--no-deps" in isolate[0] and "--no-build" in isolate[0]
    assert isolate[2][-1] == "restrict" and isolate[3][-1] == "verify"
    _, restore = commands(tmp_path, "b" * 40, "restore_role")
    assert restore[0].count("--profile") == 1 and "safe-restore" in restore[0]
    assert restore[0][-1] == "safe-degrade-restore-role"


@pytest.mark.parametrize("fault", ["command", "image", "environment", "network", "port", "extra"])
def test_isolation_overlay_rejects_non_mapping_changes(tmp_path, fault):
    project = "m1-ops-isolated-" + "a" * 10
    root = tmp_path / project
    directory = root / "releases" / ("b" * 40) / "deploy"
    directory.mkdir(parents=True)
    data = {
        "services": {
            "api": {"platform": "linux/amd64", "ports": ["127.0.0.1:12345:8000"]},
            "frontend": {"platform": "linux/amd64", "ports": ["127.0.0.1:12346:3000"]},
            "migrate": {"platform": "linux/amd64"},
            "bootstrap-role": {"platform": "linux/amd64"},
        },
        "networks": {
            "app": {
                "internal": False,
                "driver_opts": {"com.docker.network.bridge.enable_ip_masquerade": "false"},
            }
        },
    }
    if fault in {"command", "image", "environment"}:
        data["services"]["api"][fault] = "unapproved"
    elif fault == "network":
        data["networks"]["app"]["driver_opts"]["com.docker.network.bridge.enable_ip_masquerade"] = (
            "true"
        )
    elif fault == "port":
        data["services"]["api"]["ports"] = ["0.0.0.0:12345:8000"]
    else:
        data["services"]["unreviewed"] = {"image": "unapproved"}
    file = directory / "compose.ops-isolated.yml"
    file.write_text(json.dumps(data))
    spec = ComposeSpec(
        str(root),
        "b" * 40,
        {"project": project, "overlay_sha256": hashlib.sha256(file.read_bytes()).hexdigest()},
    )
    with pytest.raises(ValueError):
        spec.argv("normal", "config", "--format", "json")
