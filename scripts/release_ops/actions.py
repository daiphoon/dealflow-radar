"""Typed host actions, separate from observation; only an explicit authorization can apply."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .compose import ComposeSpec, compose_environment
from .current import release_lock
from .evidence import command, package_sha256, write_json

REQUIRED = {
    "start_api": {
        "compose-frozen",
        "identity-api",
        "identity-frontend",
        "identity-control",
        "schema",
        "role-readonly",
        "maintenance",
        "jobs-off",
        "config-static",
    },
    "start_frontend": {
        "compose-frozen",
        "api-ready",
        "identity-api",
        "identity-frontend",
        "role-readonly",
        "maintenance",
    },
    "restore_role": {
        "compose-frozen",
        "api-ready",
        "frontend-ready",
        "identity-api",
        "identity-frontend",
        "identity-control",
        "schema",
        "jobs-off",
        "maintenance",
    },
    "open_normal_proxy": {
        "compose-frozen",
        "api-ready",
        "frontend-ready",
        "identity-api",
        "identity-frontend",
        "config-normal",
        "role-normal",
        "jobs-off",
        "prepare-current",
    },
    "isolate": set(),
}


def commands(root, business_sha, action, isolation=None):
    if action not in REQUIRED:
        raise ValueError("unknown action")
    spec = ComposeSpec(root, business_sha, isolation)
    up = ["up", "-d", "--no-deps", "--no-build", "--pull", "never"]
    run = ["run", "--rm", "--no-deps", "--pull", "never", "-T"]
    control = run + ["safe-degrade-control", "python", "-m", "scripts.safe_degrade_database"]
    table = {
        "start_api": [spec.argv("normal", *up, "api")],
        "start_frontend": [spec.argv("normal", *up, "frontend")],
        "restore_role": [spec.argv("restore", *run, "safe-degrade-restore-role")],
        "open_normal_proxy": [
            spec.argv("normal", "config", "--quiet"),
            spec.argv("normal", *up, "proxy"),
        ],
        "isolate": [
            spec.argv("static", *up, "proxy"),
            spec.argv(
                "normal",
                "stop",
                "api",
                "frontend",
                "web-research-worker",
                "investor-analysis-worker",
            ),
            spec.argv("control", *control, "restrict"),
            spec.argv("control", *control, "verify"),
        ],
    }
    return spec.directory, table[action]


def authorize(binding, authorization, action, gates, *, now=None):
    for key in ("attempt", "business_sha", "ops_package_sha256", "root"):
        if binding[key] != authorization[key]:
            raise PermissionError("authorization binding mismatch:" + key)
    if (
        binding["ops_package_sha256"] != package_sha256()
        or action not in authorization["allowed_actions"]
    ):
        raise PermissionError("unapproved code or action")
    if binding.get("isolation") != authorization.get("isolation"):
        raise PermissionError("authorization isolation mapping mismatch")
    now = now or datetime.now(UTC)
    valid = set()
    for gate in gates:
        path = Path(gate["evidence"])
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != gate["sha256"]:
            raise PermissionError("gate evidence changed")
        value = json.loads(raw)
        age = (now - datetime.fromisoformat(value["finished_at"])).total_seconds()
        if value["attempt_id"] != binding["attempt"] or not 0 <= age <= 300:
            raise PermissionError("stale/different attempt evidence")
        if value["status"] == "PASS":
            valid.add(value["checkpoint_id"])
    if not REQUIRED[action] <= valid:
        raise PermissionError(
            "required preconditions missing:" + ",".join(sorted(REQUIRED[action] - valid))
        )


def apply_action(binding, authorization, action, gates, recorder, execute=None):
    """Never called by observe. Cleanup failures retain the earlier checkpoint evidence."""
    if recorder.attempt != binding["attempt"]:
        raise PermissionError("recorder attempt differs from action")
    authorize(binding, authorization, action, gates)
    directory, argvs = commands(
        binding["root"], binding["business_sha"], action, binding.get("isolation")
    )

    def local(argv):
        # cwd is explicit; current is never used to choose a Compose file.
        return command(
            argv, timeout=60, expect_json=False, cwd=directory, env=compose_environment()
        )

    execute = execute or local
    results = []
    with release_lock(binding["root"], binding["attempt"]):
        if action == "open_normal_proxy":
            write_json(
                Path(binding["root"]) / (".opened-" + binding["attempt"] + ".json"),
                {"attempt": binding["attempt"], "action": action},
            )
        for index, argv in enumerate(argvs):
            result = recorder.check(
                f"{action}-{index}",
                "host",
                "release",
                {"approved_action": action, "compose_argv": argv, "cwd": str(directory)},
                action,
                lambda argv=argv: execute(argv),
            )
            results.append(result)
            if result["status"] != "PASS" and action != "isolate":
                break
    outcome = {
        "status": "PASS" if all(v["status"] == "PASS" for v in results) else "CHECK_ERROR",
        "steps": results,
        "applied_action": action,
    }
    if action == "isolate":
        try:
            verified = json.loads(results[-1].get("stdout", "")).get("read_only") is True
        except (ValueError, TypeError):
            verified = False
        outcome["read_only_verified"] = verified
        if not verified:
            outcome["status"] = "INCONCLUSIVE"
    return outcome
