"""Read-only Docker metadata; never expose inspect Env or run an application command."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .evidence import redact


def json_command(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(f"{argv[0]} metadata exit={result.returncode}: {redact(result.stderr)}")
    return json.loads(result.stdout)


def image_metadata(value):
    return {
        **{
            k: value.get(k)
            for k in ("Id", "RepoDigests", "Descriptor", "Architecture", "Os", "RootFS")
        },
        "revision": value.get("Config", {})
        .get("Labels", {})
        .get("org.opencontainers.image.revision"),
    }


def observe_daemon(reference, container_name=None):
    # Exact immutable ref is required by the identity verifier; no pull is performed.
    info = json_command(["docker", "info", "--format", "{{json .}}"])
    driver_type = dict(info.get("DriverStatus", [])).get("driver-type", "")
    store = (
        "containerd"
        if driver_type == "io.containerd.snapshotter.v1"
        else "classic"
        if info.get("Driver") == "overlay2" and not driver_type
        else "unknown"
    )
    context = subprocess.run(
        ["docker", "context", "show"], capture_output=True, text=True, timeout=10
    )
    if context.returncode:
        raise RuntimeError("docker context unavailable")
    by_reference = image_metadata(json_command(["docker", "image", "inspect", reference])[0])
    if container_name is None:
        return {
            "scope": "daemon_image",
            "store": store,
            "context": context.stdout.strip(),
            "by_reference": by_reference,
        }
    value = json_command(["docker", "inspect", container_name])[0]
    by_container = image_metadata(json_command(["docker", "image", "inspect", value["Image"]])[0])
    health = value["State"].get("Health", {})
    return {
        "observer_location": "docker_host",
        "observed_at": datetime.now(UTC).isoformat(),
        "store": store,
        "context": context.stdout.strip(),
        "engine": {
            k: info.get(k)
            for k in ("ServerVersion", "Driver", "DriverStatus", "OSType", "Architecture")
        },
        "by_reference": by_reference,
        "by_container": by_container,
        "container": {
            "Id": value["Id"],
            "Image": value["Image"],
            "Config.Image": value["Config"]["Image"],
        },
        "state": {
            k: value["State"].get(k)
            for k in ("Running", "Status", "ExitCode", "StartedAt", "FinishedAt", "Error")
        },
        "health": redact({"Status": health.get("Status"), "Log": health.get("Log", [])[-5:]}),
        "restart_count": value["RestartCount"],
        "networks": list(value["NetworkSettings"]["Networks"]),
        "mounts": [
            {k: v.get(k) for k in ("Type", "Source", "Destination", "RW")} for v in value["Mounts"]
        ],
        "log_path": value.get("LogPath"),
        "log_config": value["HostConfig"].get("LogConfig"),
    }


def host_configuration(path):
    import hashlib

    path = Path(path)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "mtime_ns": path.stat().st_mtime_ns,
    }


def observe_health(container_name, expected_test, timeout=3):
    """Reuse the image/Compose healthcheck; do not invent a competing internal route."""
    run = subprocess.run(
        ["docker", "inspect", container_name], capture_output=True, text=True, timeout=timeout
    )
    if run.returncode:
        return {"status": "CHECK_ERROR", "exit_code": run.returncode, "stderr": redact(run.stderr)}
    value = json.loads(run.stdout)[0]
    if value["Config"].get("Healthcheck", {}).get("Test") != expected_test:
        return {"status": "BLOCKED", "actual": "unexpected_healthcheck_definition"}
    health = value["State"].get("Health", {})
    return {
        "status": "PASS"
        if value["State"]["Running"] and health.get("Status") == "healthy"
        else "NOT_READY",
        "target_container_id": value["Id"],
        "exit_code": 0,
        "actual": redact({"running": value["State"]["Running"], "health": health}),
    }
