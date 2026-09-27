"""Bounded, redacted evidence with durable first-failure retention."""

import hashlib
import json
import os
import re
import subprocess
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def redact(value):
    if isinstance(value, dict):
        return {
            k: (
                "[REDACTED]"
                if re.search(
                    r"(?i)^(authorization|cookie|set-cookie|token|password|secret|api_key|otp|env)$",
                    k,
                )
                else redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        return value
    value = re.sub(
        r"(?im)\b(authorization|cookie|set-cookie)\s*[:=][^\r\n]*", r"\1: [REDACTED]", value
    )
    value = re.sub(
        r"(?i)\b(password|token|secret|api[_-]?key|otp)\b[\s\"\x27:=]+[^\s,;\"\x27]+",
        r"\1=[REDACTED]",
        value,
    )
    value = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", value)
    value = re.sub(r"://[^/\s:@]+:[^/\s@]+@", "://[REDACTED]@", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", "[EMAIL]", value)
    return value[:12000]


def location(value):
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))


def utc():
    return datetime.now(UTC).isoformat()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = (json.dumps(redact(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


class Recorder:
    def __init__(self, directory, attempt):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", attempt):
            raise ValueError("invalid attempt id")
        self.directory = Path(directory) / attempt
        self.attempt = attempt
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def check(self, checkpoint, observer, service, expected, kind, operation, container_id=None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", checkpoint):
            raise ValueError("invalid checkpoint id")
        start, tick = utc(), time.monotonic()
        try:
            result = operation()
            if result.get("status") not in {
                "PASS",
                "NOT_READY",
                "BLOCKED",
                "CHECK_ERROR",
                "INCONCLUSIVE",
            }:
                raise ValueError("invalid probe result")
        except Exception as exc:
            result = {
                "status": "CHECK_ERROR",
                "exception_type": type(exc).__name__,
                "actual": str(exc),
                "traceback": traceback.format_exc(limit=6),
            }
        record = {
            "attempt_id": self.attempt,
            "checkpoint_id": checkpoint,
            "observer_location": observer,
            "target_service": service,
            "target_container_id": container_id or result.get("container_id"),
            "started_at": start,
            "finished_at": utc(),
            "duration_ms": round((time.monotonic() - tick) * 1000),
            "expected": expected,
            "actual": None,
            "command_kind": kind,
            "exit_code": None,
            "remote_exit_code": None,
            "exception_type": None,
            "http_status": None,
            "transport_class": None,
            "location": None,
            **result,
        }
        path = self.directory / (checkpoint + ".json")
        try:
            sha = write_json(path, record)
        except OSError as exc:
            return {
                **redact(record),
                "observation_status": record["status"],
                "status": "CHECK_ERROR",
                "evidence": None,
                "evidence_sha256": None,
                "evidence_error": redact(f"{type(exc).__name__}:{exc}"),
            }

        if record["status"] in {"BLOCKED", "CHECK_ERROR", "INCONCLUSIVE"}:
            try:
                write_json(
                    self.directory / "first-failure.json",
                    {
                        "checkpoint_id": checkpoint,
                        "evidence": str(path),
                        "sha256": sha,
                        "status": record["status"],
                    },
                )
            except FileExistsError:
                pass
            except OSError as exc:
                return {
                    **redact(record),
                    "observation_status": record["status"],
                    "status": "CHECK_ERROR",
                    "evidence": str(path),
                    "evidence_sha256": sha,
                    "evidence_error": redact(f"first failure index: {type(exc).__name__}:{exc}"),
                }

        return {**redact(record), "evidence": str(path), "evidence_sha256": sha}


def command(argv, *, remote=False, timeout=20, expect_json=True, cwd=None):
    """Run an already selected command; never print argv/env or invoke cleanup."""
    try:
        run = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "INCONCLUSIVE",
            "exception_type": "TimeoutExpired",
            "transport_class": "ssh_timeout" if remote else "command_timeout",
            "exit_code": None,
            "remote_exit_code": None,
            "stdout": redact(
                exc.stdout.decode(errors="replace")
                if isinstance(exc.stdout, bytes)
                else exc.stdout or ""
            ),
            "stderr": redact(
                exc.stderr.decode(errors="replace")
                if isinstance(exc.stderr, bytes)
                else exc.stderr or ""
            ),
        }
    result = {
        "exit_code": run.returncode,
        "remote_exit_code": None if not remote or run.returncode == 255 else run.returncode,
        "stdout": redact(run.stdout),
        "stderr": redact(run.stderr),
    }
    if run.returncode:
        return {
            **result,
            "status": "INCONCLUSIVE" if remote and run.returncode == 255 else "CHECK_ERROR",
            "exception_type": "SSHTransportError"
            if remote and run.returncode == 255
            else "CommandExitError",
        }
    try:
        actual = json.loads(run.stdout) if expect_json else run.stdout
    except json.JSONDecodeError as exc:
        return {
            **result,
            "status": "CHECK_ERROR",
            "exception_type": type(exc).__name__,
            "actual": str(exc),
        }
    return {**result, "status": "PASS", "actual": actual}


def package_sha256():
    base = Path(__file__).parent
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(base.glob("*.py"))}
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
