"""Real Compose 2.40.3 config through the formal CLI; no daemon or GHCR needed."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BUSINESS = "47c4a5ce82731b6b9a03899012c47ff84d2d7abd"
ROLES = ("api", "frontend", "safe-degrade-control", "safe-degrade-restore-role")
FLAGS = ["EXTERNAL_CALLS_ENABLED", "WATCHLIST_MONITOR_ENABLED"]


@pytest.fixture
def compose_case(tmp_path):
    binary = os.getenv("M1_COMPOSE_BINARY")
    if not binary:
        if os.getenv("CI"):
            pytest.fail("ordinary CI must install the pinned real Compose regression binary")
        pytest.skip("set M1_COMPOSE_BINARY to the isolated official Compose 2.40.3 binary")
    assert "v2.40.3" in subprocess.check_output([binary, "version"], text=True)
    source = Path(__file__).resolve().parents[2]
    release = tmp_path / "releases" / BUSINESS
    (release / "deploy").mkdir(parents=True)
    files = [
        "compose.production.yml",
        "deploy/compose.single-host.yml",
        "deploy/compose.safe-degrade.yml",
    ]
    for name in files:
        shutil.copyfile(source / name, release / name)
    refs = {role: "registry.invalid/fixture@sha256:" + "d" * 64 for role in ROLES}
    env = (source / "deploy/single-host.env.example").read_text()
    env += (
        "\nBACKEND_IMAGE="
        + refs["api"]
        + "\nFRONTEND_IMAGE="
        + refs["frontend"]
        + "\nSAFE_CONTROL_IMAGE="
        + refs["safe-degrade-control"]
        + "\n"
    )
    (release / "deploy/single-host.env").write_text(env)
    tools = tmp_path / "bin"
    tools.mkdir()
    trace = tmp_path / "commands.jsonl"
    wrapper = tools / "docker"
    wrapper.write_text(
        "#!"
        + sys.executable
        + "\n"
        + r"""
import json, os, subprocess, sys
args=sys.argv[1:]
assert args[0]=="compose" and (args[-1]=="version" or "config" in args), args
with open(os.environ["M1_CONFIG_TRACE"], "a") as f: f.write(json.dumps(args)+"\n")
r=subprocess.run([os.environ["M1_COMPOSE_BINARY"], *args[1:]],capture_output=True,text=True)
fault=os.getenv("M1_CONFIG_FAULT")
if "config" in args and fault=="command": sys.exit(17)
if "config" in args and fault=="json": print("broken json"); sys.exit(0)
if "config" in args and fault=="field" and r.returncode==0:
 d=json.loads(r.stdout); d["services"]["api"].pop("image"); r.stdout=json.dumps(d)
sys.stdout.write(r.stdout); sys.stderr.write(r.stderr); sys.exit(r.returncode)
"""
    )
    wrapper.chmod(0o700)
    execution_env = {
        **os.environ,
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "M1_CONFIG_TRACE": str(trace),
        "M1_COMPOSE_BINARY": binary,
        "COMPOSE_PROFILES": "tools,restore,analysis,web-research",
    }
    for key in (
        "DATABASE_URL",
        "DATABASE_ADMIN_URL",
        "BACKEND_IMAGE",
        "FRONTEND_IMAGE",
        "SAFE_CONTROL_IMAGE",
        *FLAGS,
    ):
        execution_env.pop(key, None)
    spec = {
        "kind": "compose-frozen",
        "root": str(tmp_path),
        "business_sha": BUSINESS,
        "images": refs,
        "disabled_flags": FLAGS,
        "configuration_sha256": {
            name: hashlib.sha256((release / name).read_bytes()).hexdigest() for name in files
        },
    }

    def invoke(case="pass", cwd=source):
        path = tmp_path / (case + "-input.json")
        path.write_text(json.dumps(spec))
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.release_ops",
                "observe-metadata",
                "--input",
                str(path),
                "--attempt",
                case,
                "--checkpoint",
                "compose-frozen",
                "--evidence",
                str(tmp_path / "evidence"),
            ],
            cwd=cwd,
            env=execution_env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        result = json.loads(run.stdout)
        commands = [json.loads(line) for line in trace.read_text().splitlines()]
        assert all(
            cmd[0] == "compose" and ("config" in cmd or cmd[-1] == "version") for cmd in commands
        )
        assert not any(
            token in cmd
            for cmd in commands
            for token in ("up", "run", "start", "restart", "stop", "down")
        )
        assert "replace_with_owner_password" not in run.stdout
        assert "replace_with_app_password" not in run.stdout
        if os.getenv("M1_CONFIG_EVIDENCE"):
            out = Path(os.environ["M1_CONFIG_EVIDENCE"])
            out.mkdir(parents=True, exist_ok=True)
            (out / (case + ".json")).write_text(
                json.dumps(
                    {"cli_exit": run.returncode, "result": result, "commands": commands}, indent=2
                )
            )
        return run, result, commands

    return release, spec, execution_env, invoke


def test_real_compose_formal_cli(compose_case):
    _, _, _, invoke = compose_case
    run, result, commands = invoke()
    assert run.returncode == 0, result
    assert result["status"] == "PASS" and result["expected"] is not None
    assert result["actual"]["normal"]["services"] == ["api", "database", "frontend", "proxy"]
    assert {"safe-degrade-control", "safe-degrade-restore-role"} <= set(
        result["actual"]["control"]["services"]
    )
    normal = next(cmd for cmd in commands if "config" in cmd and "--profile" not in cmd)
    assert "deploy/compose.safe-degrade.yml" not in normal
    control = next(cmd for cmd in commands if "--profile" in cmd)
    assert control[control.index("--profile") + 1] == "safe-control"
    assert control.count("--profile") == 2 and "safe-restore" in control


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("one-profile", "BLOCKED"),
        ("missing", "BLOCKED"),
        ("control-image", "BLOCKED"),
        ("normal-image", "BLOCKED"),
        ("flag", "BLOCKED"),
        ("flag-missing", "CHECK_ERROR"),
        ("required-env", "CHECK_ERROR"),
        ("command", "CHECK_ERROR"),
        ("json", "CHECK_ERROR"),
        ("field", "CHECK_ERROR"),
    ],
)
def test_real_compose_refuses_contract_breakage(compose_case, fault, expected):
    release, spec, env, invoke = compose_case
    safe = release / "deploy/compose.safe-degrade.yml"
    normal = release / "compose.production.yml"
    env_file = release / "deploy/single-host.env"
    if fault == "one-profile":
        safe.write_text(
            safe.read_text().replace('profiles: ["safe-restore"]', 'profiles: ["other"]')
        )
    elif fault == "missing":
        safe.write_text(safe.read_text().split("  safe-degrade-restore-role:")[0])
    elif fault in {"control-image", "normal-image"}:
        key = "SAFE_CONTROL_IMAGE" if fault == "control-image" else "BACKEND_IMAGE"
        env_file.write_text(
            env_file.read_text() + "\n" + key + "=registry.invalid/wrong@sha256:" + "e" * 64 + "\n"
        )
    elif fault == "flag":
        env_file.write_text(env_file.read_text() + "\nWATCHLIST_MONITOR_ENABLED=true\n")
    elif fault == "flag-missing":
        normal.write_text(
            "\n".join(
                line
                for line in normal.read_text().splitlines()
                if not line.startswith("  WATCHLIST_MONITOR_ENABLED:")
            )
            + "\n"
        )
    elif fault == "required-env":
        env_file.write_text(
            "\n".join(
                line
                for line in env_file.read_text().splitlines()
                if not line.startswith("DATABASE_URL=")
            )
            + "\n"
        )
    else:
        env["M1_CONFIG_FAULT"] = fault
    # Fault evidence proves semantic rejection, rather than just a changed file hash.
    spec["configuration_sha256"] = {
        name: hashlib.sha256((release / name).read_bytes()).hexdigest()
        for name in spec["configuration_sha256"]
    }
    run, result, _ = invoke(fault)
    assert run.returncode != 0 and result["status"] == expected, result
    assert result["expected"] and result["actual"] and result["exception_type"] is not None
    first = Path(result["evidence"]).parent / "first-failure.json"
    assert json.loads(first.read_text())["status"] == expected
