"""One finite Compose specification for configuration observation and release actions."""

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .evidence import redact

NORMAL_FILES = ("compose.production.yml", "deploy/compose.single-host.yml")
SAFE_FILE = "deploy/compose.safe-degrade.yml"
STATIC_FILE = "deploy/compose.maintenance-static.yml"
CONTROL_PROFILES = ("safe-control", "safe-restore")
IMAGES = ("api", "frontend", "safe-degrade-control", "safe-degrade-restore-role")


def compose_environment():
    # All parameters belong to the spec; accidental shell profiles/files/projects do not.
    return {
        k: v
        for k, v in os.environ.items()
        if k
        not in {"COMPOSE_PROFILES", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_ENV_FILES"}
    }


@dataclass(frozen=True)
class ComposeSpec:
    root: str
    business_sha: str
    isolation: dict | None = None

    @property
    def directory(self):
        if not re.fullmatch(r"[a-f0-9]{40}", self.business_sha):
            raise ValueError("invalid business SHA")
        root = Path(self.root).resolve(strict=True)
        directory = root / "releases" / self.business_sha
        if directory.resolve(strict=True) != directory:
            raise ValueError("release directory must not escape via symlink")
        return directory

    @property
    def project(self):
        if self.isolation is None:
            return "dealflow-radar-production"
        if set(self.isolation) != {"project", "overlay_sha256"}:
            raise ValueError("finite isolation parameters required")
        project = self.isolation["project"]
        if not re.fullmatch(r"m1-ops-isolated-[a-f0-9]{10}", project):
            raise ValueError("isolated project required")
        root = Path(self.root).resolve(strict=True)
        if root.name != project or root == Path("/opt/dealflow-radar"):
            raise ValueError("isolated root must match project")
        overlay = self.directory / "deploy/compose.ops-isolated.yml"
        if (
            overlay.resolve(strict=True) != overlay
            or hashlib.sha256(overlay.read_bytes()).hexdigest() != self.isolation["overlay_sha256"]
        ):
            raise ValueError("isolated overlay changed")
        mapping = json.loads(overlay.read_text())
        if set(mapping) != {"services", "networks"} or mapping["networks"] != {
            "app": {
                "internal": False,
                "driver_opts": {"com.docker.network.bridge.enable_ip_masquerade": "false"},
            }
        }:
            raise ValueError("isolation only maps a NAT-disabled network and local ports/platform")
        services = mapping["services"]
        if set(services) != {"api", "frontend", "migrate", "bootstrap-role"}:
            raise ValueError("isolated service mapping differs")
        for name, value in services.items():
            keys = {"platform", "ports"} if name in {"api", "frontend"} else {"platform"}
            if set(value) != keys or value["platform"] != "linux/amd64":
                raise ValueError("isolated mapping cannot override images, commands or environment")
            if "ports" in value:
                target = "8000" if name == "api" else "3000"
                ports = value["ports"]
                if (
                    len(ports) != 1
                    or not re.fullmatch(r"127\.0\.0\.1:[1-9][0-9]{0,4}:" + target, ports[0])
                    or int(ports[0].split(":")[1]) > 65535
                ):
                    raise ValueError("isolated ports must be finite loopback mappings")
        # No remote Docker destination can be used by the test mapping.
        if os.getenv("DOCKER_HOST") or os.getenv("DOCKER_CONTEXT"):
            raise PermissionError("isolation requires an explicit local Docker context")
        context = subprocess.check_output(
            ["docker", "context", "show"], text=True, timeout=10
        ).strip()
        values = json.loads(
            subprocess.check_output(
                ["docker", "context", "inspect", context], text=True, timeout=10
            )
        )
        endpoint = values[0]["Endpoints"]["docker"]["Host"]
        if context not in {"desktop-linux", "default"} or not endpoint.startswith("unix://"):
            raise PermissionError("isolation requires a local unix Docker endpoint")
        return project

    def summary(self, mode):
        files = list(NORMAL_FILES)
        profiles = []
        if mode in {"control", "restore", "static"}:
            files.append(SAFE_FILE)
            profiles = (
                list(CONTROL_PROFILES)
                if mode == "control"
                else ["safe-restore"]
                if mode == "restore"
                else ["safe-control"]
            )
        elif mode != "normal":
            raise ValueError("unknown Compose mode")
        if mode == "static":
            files.append(STATIC_FILE)
        if self.isolation is not None:
            files.append("deploy/compose.ops-isolated.yml")
        return {
            "mode": mode,
            "project": self.project,
            "cwd": str(self.directory),
            "env_file": "deploy/single-host.env",
            "files": files,
            "profiles": profiles,
        }

    def argv(self, mode, *tail):
        summary = self.summary(mode)
        return [
            "docker",
            "compose",
            "--project-name",
            summary["project"],
            "--env-file",
            summary["env_file"],
            *[arg for name in summary["files"] for arg in ("-f", name)],
            *[arg for profile in summary["profiles"] for arg in ("--profile", profile)],
            *tail,
        ]


def expected_contract(spec):
    execution = ComposeSpec(spec["root"], spec["business_sha"], spec.get("isolation"))
    return {
        "views": {mode: execution.summary(mode) for mode in ("normal", "control")},
        "required_services": {
            "normal": ["api", "database", "frontend", "proxy"],
            "control": ["safe-degrade-control", "safe-degrade-restore-role"],
        },
        "images": spec["images"],
        "configuration_sha256": spec["configuration_sha256"],
        "closed_flags": {key: "false" for key in spec["disabled_flags"]},
    }


def observe_frozen(spec):
    expected = expected_contract(spec)
    if (
        set(spec["images"]) != set(IMAGES)
        or not spec["configuration_sha256"]
        or not spec["disabled_flags"]
    ):
        raise ValueError("complete image/configuration/flag binding required")
    if any(not re.fullmatch(r"[A-Z_]+_ENABLED", key) for key in spec["disabled_flags"]):
        raise ValueError("closed flag allowlist required")
    execution = ComposeSpec(spec["root"], spec["business_sha"], spec.get("isolation"))
    hashes = {}
    for name in spec["configuration_sha256"]:
        path = execution.directory / name
        if path.resolve(strict=True) != path or not path.is_relative_to(execution.directory):
            raise ValueError("configuration path must remain in release")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    actual = {"configuration_sha256": hashes, "normal": {}, "control": {}}
    command_results = []

    def refusal(status, reason, details=None):
        return {
            "status": status,
            "expected": expected,
            "actual": actual,
            "exception_type": "ComposeContractMismatch"
            if status == "BLOCKED"
            else "ComposeCheckError",
            "reason": reason,
            "details": details,
            "commands": command_results,
            "exit_code": command_results[-1]["exit_code"] if command_results else None,
        }

    def run(argv):
        try:
            value = subprocess.run(
                argv,
                cwd=execution.directory,
                env=compose_environment(),
                capture_output=True,
                text=True,
                timeout=20,
            )
        except subprocess.TimeoutExpired:
            command_results.append({"argv": argv, "exit_code": None})
            return None
        command_results.append({"argv": argv, "exit_code": value.returncode})
        return value

    version = run(["docker", "compose", "version"])
    if version is None:
        return refusal("INCONCLUSIVE", "compose_version_timeout")
    if version.returncode:
        return refusal("CHECK_ERROR", "compose_version_failed")
    actual["compose_version"] = redact(version.stdout.strip())
    for mode in ("normal", "control"):
        value = run(execution.argv(mode, "config", "--format", "json"))
        actual[mode] = {**expected["views"][mode], "services": [], "missing_services": []}
        if value is None:
            return refusal("INCONCLUSIVE", "compose_config_timeout", mode)
        if value.returncode:
            # Never serialize raw config or stderr: interpolation errors can contain secrets.
            return refusal("CHECK_ERROR", "compose_config_command_failed", mode)
        try:
            data = json.loads(value.stdout)
            services = data["services"]
            if not isinstance(services, dict):
                raise TypeError("services must be an object")
        except (ValueError, KeyError, TypeError):
            return refusal("CHECK_ERROR", "compose_json_or_services_invalid", mode)
        actual[mode]["services"] = sorted(services)
        missing = sorted(set(expected["required_services"][mode]) - set(services))
        actual[mode]["missing_services"] = missing
        if missing:
            return refusal("BLOCKED", "required_services_missing", mode)
        try:
            images = {
                name: services[name]["image"]
                for name in expected["required_services"][mode]
                if name in IMAGES
            }
            if any(not isinstance(image, str) or not image for image in images.values()):
                raise TypeError("image must be a nonempty string")
            actual[mode]["images"] = images
            if mode == "normal":
                env = services["api"]["environment"]
                flags = {key: env[key] for key in spec["disabled_flags"]}
                if any(not isinstance(value, str) for value in flags.values()):
                    raise TypeError("closed flags must be strings")
                actual[mode]["closed_flags"] = flags
                if any(value.lower() != "false" for value in flags.values()):
                    return refusal("BLOCKED", "normal_flag_enabled")
            else:
                attrs = {
                    name: {
                        key: services[name][key]
                        for key in (
                            "profiles",
                            "read_only",
                            "cap_drop",
                            "security_opt",
                            "restart",
                            "networks",
                            "command",
                        )
                    }
                    for name in images
                }
                actual[mode]["safety_attributes"] = attrs
                for name, profile in zip(IMAGES[2:], CONTROL_PROFILES, strict=True):
                    service = attrs[name]
                    cmd = (
                        ["python", "-m", "scripts.safe_degrade_database", "restrict"]
                        if name == IMAGES[2]
                        else ["python", "-m", "scripts.bootstrap_local_database"]
                    )
                    good = (
                        service["profiles"] == [profile]
                        and service["read_only"] is True
                        and service["cap_drop"] == ["ALL"]
                        and service["restart"] == "no"
                        and service["security_opt"] == ["no-new-privileges:true"]
                        and set(service["networks"]) == {"app"}
                        and service["command"] == cmd
                    )
                    if name == IMAGES[2] and "APP_DATABASE_PASSWORD" in services[name].get(
                        "environment", {}
                    ):
                        good = False
                    if not good:
                        return refusal("BLOCKED", "control_safety_attributes_mismatch", name)
        except (KeyError, TypeError):
            return refusal("CHECK_ERROR", "required_judgement_field_missing_or_invalid", mode)
        if any(image != spec["images"][name] for name, image in images.items()):
            return refusal("BLOCKED", "approved_image_mismatch", mode)
    if hashes != spec["configuration_sha256"]:
        return refusal("BLOCKED", "configuration_sha256_mismatch")
    return {
        "status": "PASS",
        "expected": expected,
        "actual": actual,
        "commands": command_results,
        "exit_code": 0,
    }
