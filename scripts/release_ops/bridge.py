"""Reviewed Mac/SSH adapter: immutable archive, standalone interpreter, typed CLI only.

The same source runs with python -I on the remote host or isolated local host.
Request JSON contains declarative parameters, never executable Python or shell.
"""

import base64
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path


def source_sha256():
    if "BRIDGE_SOURCE_SHA256" in globals():
        return globals()["BRIDGE_SOURCE_SHA256"]
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def verify_package(directory, expected):
    package = Path(directory) / "scripts/release_ops"
    files = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.glob("*.py"))
    }
    actual = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    if actual != expected:
        raise PermissionError("standalone ops package SHA mismatch")
    return actual


def install(request):
    raw = base64.b64decode(request["archive"], validate=True)
    if hashlib.sha256(raw).hexdigest() != request["archive_sha256"]:
        raise PermissionError("archive SHA mismatch")
    directory = Path(request["directory"])
    if directory.is_symlink() or directory.exists():
        raise FileExistsError("candidate directory already exists; no replacement allowed")
    found, contents = {}, {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for item in archive:
            if not item.isfile() or not re.fullmatch(r"scripts/release_ops/[a-z_]+\.py", item.name):
                raise ValueError("archive members must be reviewed regular ops modules")
            name = Path(item.name).name
            if name in found or item.size > 200000:
                raise ValueError("duplicate or oversized archive member")
            value = archive.extractfile(item).read()
            found[name] = hashlib.sha256(value).hexdigest()
            contents[item.name] = value
    if found != request["files"]:
        raise PermissionError("archive file manifest differs")
    content_sha = hashlib.sha256(json.dumps(found, sort_keys=True).encode()).hexdigest()
    if content_sha != request["ops_package_sha256"]:
        raise PermissionError("archive content SHA differs")
    directory.mkdir(parents=True, mode=0o700)
    for name, value in contents.items():
        path = directory / name
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(value)
        path.chmod(0o600)
    return {
        "status": "PASS",
        "directory": str(directory),
        "files": found,
        "archive_sha256": request["archive_sha256"],
        "ops_package_sha256": verify_package(directory, content_sha),
        "python": sys.version.split()[0],
        "bridge_source_sha256": source_sha256(),
    }


def invoke(request):
    directory = Path(request["directory"]).resolve(strict=True)
    verified = verify_package(directory, request["ops_package_sha256"])
    operation = request["operation"]
    allowed = {
        "observe-metadata",
        "observe-daemon",
        "observe-identity",
        "observe-health",
        "observe-http",
        "plan",
        "prepare-current",
        "apply-step",
        "apply-current",
    }
    if operation not in allowed:
        raise ValueError("unsupported formal operation")
    if operation in {"prepare-current", "apply-step", "apply-current"} and not request.get(
        "authorization"
    ):
        raise PermissionError("write-capable CLI requires an explicit authorization path")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", request["checkpoint"]):
        raise ValueError("invalid checkpoint")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request["attempt"]):
        raise ValueError("invalid attempt")
    evidence_root = Path(request["evidence_root"]).resolve()
    inputs = evidence_root / "inputs"
    inputs.mkdir(parents=True, mode=0o700, exist_ok=True)
    input_path = inputs / (request["attempt"] + "-" + request["checkpoint"] + ".json")
    fd = os.open(input_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(request["input"], stream)
    loader = (
        "import runpy,sys,types;sys.dont_write_bytecode=True;"
        "scripts=types.ModuleType('scripts');"
        "scripts.__path__=[sys.argv.pop(1)+'/scripts'];"
        "sys.modules['scripts']=scripts;"
        "runpy.run_module('scripts.release_ops',run_name='__main__')"
    )
    argv = [
        request["interpreter"],
        "-I",
        "-c",
        loader,
        str(directory),
        operation,
        "--input",
        str(input_path),
        "--attempt",
        request["attempt"],
        "--checkpoint",
        request["checkpoint"],
        "--evidence",
        str(evidence_root),
    ]
    if request.get("blobs"):
        argv += ["--blobs", request["blobs"]]
    if request.get("authorization"):
        argv += ["--authorization", request["authorization"]]
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update(PYTHONPATH=str(directory), PYTHONDONTWRITEBYTECODE="1")
    # -I initializes stdlib independently; the loader then selects only the verified archive.
    try:
        run = subprocess.run(
            argv, cwd=directory, env=env, capture_output=True, text=True, timeout=180
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "INCONCLUSIVE",
            "checker_exit_code": None,
            "reason": "formal_cli_timeout",
            "ops_package_sha256": verified,
        }
    from scripts.release_ops.evidence import redact

    try:
        result = json.loads(run.stdout)
    except ValueError:
        result = {"status": "CHECK_ERROR", "reason": "formal_cli_output_invalid"}
    status = result.get("status", "CHECK_ERROR")
    if run.returncode != 0 and status == "PASS":
        status = "CHECK_ERROR"
    return {
        "status": status,
        "checker_exit_code": run.returncode,
        "result": result,
        "stderr": redact(run.stderr),
        "formal_argv": argv,
        "cwd": str(directory),
        "interpreter": request["interpreter"],
        "ops_package_sha256": verified,
        "bridge_source_sha256": source_sha256(),
    }


def execute(request):
    # Explicit insertion only after verification; never rely on the operator workspace.
    if request["mode"] == "install":
        return install(request)
    verify_package(request["directory"], request["ops_package_sha256"])
    import types

    scripts = types.ModuleType("scripts")
    scripts.__path__ = [request["directory"] + "/scripts"]
    sys.modules["scripts"] = scripts
    if request["mode"] == "invoke":
        return invoke(request)
    if request["mode"] == "host-context":
        from scripts.release_ops.host import observe_host

        return {
            "status": "PASS",
            "actual": observe_host(request["input"]),
            "ops_package_sha256": request["ops_package_sha256"],
        }
    raise ValueError("unsupported bridge mode")


def send(request, *, interpreter, destination=None):
    """Actual local/SSH transport of this exact reviewed source, no arbitrary remote script."""
    source = "BRIDGE_SOURCE_SHA256=" + repr(source_sha256()) + "\n" + Path(__file__).read_text()
    if destination is None:
        argv = [interpreter, "-I", "-c", source]
    else:
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*@[A-Za-z0-9.-]+", destination):
            raise ValueError("invalid SSH destination")
        remote = "cd / && sudo -n " + shlex.join([interpreter, "-I", "-c", source])
        argv = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            destination,
            remote,
        ]
    from .evidence import redact

    try:
        run = subprocess.run(
            argv, input=json.dumps(request), capture_output=True, text=True, timeout=210, cwd="/"
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "INCONCLUSIVE",
            "transport_exit_code": None,
            "reason": "bridge_transport_timeout",
            "bridge_source_sha256": source_sha256(),
        }
    try:
        value = json.loads(run.stdout)
    except ValueError:
        value = {"status": "CHECK_ERROR", "reason": "bridge_output_invalid"}
    return redact(
        {
            "status": value.get("status", "CHECK_ERROR")
            if run.returncode == 0
            else "INCONCLUSIVE"
            if run.returncode == 255
            else "CHECK_ERROR",
            "transport_exit_code": run.returncode,
            "ssh_exit_code": run.returncode if destination else None,
            "remote_program_exit_code": run.returncode if run.returncode != 255 else None,
            "bridge_source_sha256": source_sha256(),
            "payload": value,
            "stderr": run.stderr,
        }
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request")
    parser.add_argument("--destination")
    parser.add_argument("--interpreter", default="/usr/bin/python3")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        if args.request:
            output = send(
                json.loads(Path(args.request).read_text()),
                interpreter=args.interpreter,
                destination=args.destination,
            )
            if args.output:
                from .evidence import write_json

                write_json(args.output, output)
        else:
            output = execute(json.load(sys.stdin))
    except Exception as exc:
        # No payload/credential content appears in bootstrap errors.
        output = {
            "status": "CHECK_ERROR",
            "exception_type": type(exc).__name__,
            "reason": "bridge_request_or_package_failed",
        }
    print(json.dumps(output))
    # Remote wrapper preserves a parseable status. Local CLI consumes that status.
    if args.request:
        return {"PASS": 0, "NOT_READY": 3, "BLOCKED": 1, "CHECK_ERROR": 2, "INCONCLUSIVE": 4}.get(
            output["status"], 2
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
