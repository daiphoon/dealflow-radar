"""The actual standalone archive/bridge/CLI path, including nonzero checker exits."""

import base64
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from scripts.release_ops.bridge import send, source_sha256
from scripts.release_ops.evidence import package_sha256


def bundle_request(directory):
    source = Path(__file__).resolve().parents[2] / "scripts/release_ops"
    buffer = io.BytesIO()
    files = {}
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path in sorted(source.glob("*.py")):
            value = path.read_bytes()
            files[path.name] = hashlib.sha256(value).hexdigest()
            item = tarfile.TarInfo("scripts/release_ops/" + path.name)
            item.size = len(value)
            item.mode = 0o600
            archive.addfile(item, io.BytesIO(value))
    raw = buffer.getvalue()
    return {
        "mode": "install",
        "directory": str(directory),
        "files": files,
        "archive": base64.b64encode(raw).decode(),
        "archive_sha256": hashlib.sha256(raw).hexdigest(),
        "ops_package_sha256": package_sha256(),
    }


def test_standalone_archive_and_true_cli_preserve_checker_failure(tmp_path):
    candidate = tmp_path / "candidate"
    request = bundle_request(candidate)
    installed = send(request, interpreter=sys.executable)
    assert installed["status"] == "PASS", installed
    assert installed["payload"]["bridge_source_sha256"] == source_sha256()
    # A cwd module collision cannot replace the verified standalone import path.
    (candidate / "operator.py").write_text('raise RuntimeError("incidental workspace imported")')
    observe = {
        "mode": "invoke",
        "directory": str(candidate),
        "ops_package_sha256": package_sha256(),
        "interpreter": sys.executable,
        "operation": "observe-metadata",
        "checkpoint": "schema",
        "attempt": "test",
        "evidence_root": str(tmp_path / "proof"),
        "input": {"kind": "schema"},
    }
    result = send(observe, interpreter=sys.executable)
    assert result["transport_exit_code"] == 0
    assert result["status"] == "CHECK_ERROR"
    assert result["payload"]["checker_exit_code"] == 2
    first = tmp_path / "proof/test/first-failure.json"
    before = first.read_bytes()
    observe.update(checkpoint="role-readonly", input={"kind": "role-readonly"})
    second = send(observe, interpreter=sys.executable)
    assert second["status"] == "CHECK_ERROR" and first.read_bytes() == before
    assert "incidental workspace imported" not in json.dumps(result)


@pytest.mark.parametrize("change", ["archive", "files", "package", "duplicate"])
def test_standalone_install_rejects_tampering_without_replacing_old(tmp_path, change):
    request = bundle_request(tmp_path / "candidate")
    if change == "archive":
        request["archive_sha256"] = "0" * 64
    elif change == "files":
        request["files"]["bridge.py"] = "0" * 64
    elif change == "package":
        request["ops_package_sha256"] = "0" * 64
    else:
        assert send(request, interpreter=sys.executable)["status"] == "PASS"
    result = send(request, interpreter=sys.executable)
    assert result["status"] == "CHECK_ERROR", result
    assert result["payload"]["exception_type"] in {"PermissionError", "FileExistsError"}


def test_public_bridge_cli_consumes_failure_from_independent_archive(tmp_path):
    candidate = tmp_path / "candidate"
    assert send(bundle_request(candidate), interpreter=sys.executable)["status"] == "PASS"
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "mode": "invoke",
                "directory": str(candidate),
                "ops_package_sha256": package_sha256(),
                "interpreter": sys.executable,
                "operation": "observe-metadata",
                "checkpoint": "schema",
                "attempt": "cli",
                "evidence_root": str(tmp_path / "proof"),
                "input": {"kind": "schema"},
            }
        )
    )
    output = tmp_path / "envelope.json"
    loader = (
        "import sys,types,runpy;s=types.ModuleType('scripts');"
        "s.__path__=[sys.argv.pop(1)+'/scripts'];sys.modules['scripts']=s;"
        "runpy.run_module('scripts.release_ops.bridge',run_name='__main__')"
    )
    run = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            loader,
            str(candidate),
            "--request",
            str(request),
            "--interpreter",
            sys.executable,
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        cwd="/",
        timeout=30,
    )
    value = json.loads(output.read_text())
    assert run.returncode == 2 and value["status"] == "CHECK_ERROR"
    assert value["transport_exit_code"] == 0
    assert value["payload"]["checker_exit_code"] == 2
