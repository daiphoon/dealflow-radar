"""Behavioral checks for the release entry point; no real credentials or network."""

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.release_ops import current
from scripts.release_ops.actions import apply_action, commands, package_sha256
from scripts.release_ops.evidence import Recorder, command, redact
from scripts.release_ops.identity import digest, verify_identity
from scripts.release_ops.probes import (
    Contract,
    failure_policy,
    mounted_configuration,
    response_result,
    wait_ready,
)


def image_fixture(store="containerd"):
    config = json.dumps(
        {
            "os": "linux",
            "architecture": "amd64",
            "config": {"Labels": {"org.opencontainers.image.revision": "b" * 40}},
            "rootfs": {"diff_ids": ["sha256:" + "d" * 64]},
        }
    ).encode()
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": digest(config), "size": len(config)},
            "layers": [{"digest": "sha256:" + "e" * 64, "size": 12}],
        }
    ).encode()
    md = digest(manifest)
    ref = "registry.example.invalid/api@" + md
    ident = md if store == "containerd" else digest(config)
    image = {
        "Id": ident,
        "RepoDigests": [ref],
        "Architecture": "amd64",
        "Os": "linux",
        "RootFS": {"Layers": ["sha256:" + "d" * 64]},
        "revision": "b" * 40,
    }
    if store == "containerd":
        image["Descriptor"] = {
            "digest": md,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
        }
    return (
        {
            "reference": ref,
            "revision": "b" * 40,
            "platform": "linux/amd64",
            "config_digest": digest(config),
        },
        {md: manifest, digest(config): config},
        {
            "store": store,
            "context": "isolated",
            "by_reference": image,
            "by_container": image.copy(),
            "container": {"Id": "container-1", "Image": ident, "Config.Image": ref},
        },
    )


def test_incident_containerd_id_is_not_config_digest():
    approved, blobs, observed = image_fixture()
    assert observed["container"]["Image"] != approved["config_digest"]
    assert verify_identity(approved, blobs, observed)["status"] == "PASS"


def test_classic_store_same_approved_chain():
    assert verify_identity(*image_fixture("classic"))["status"] == "PASS"


def test_old_image_cannot_pass_with_forged_same_revision():
    approved, blobs, observed = image_fixture()
    observed["container"]["Image"] = "sha256:" + "f" * 64
    assert verify_identity(approved, blobs, observed)["status"] == "BLOCKED"


def test_cli_observe_does_not_apply(tmp_path):
    approved, blobs, observed = image_fixture()
    root = tmp_path / "blobs"
    root.mkdir()
    for k, v in blobs.items():
        (root / k.split(":")[1]).write_bytes(v)
    spec = tmp_path / "input.json"
    spec.write_text(json.dumps({"approved": approved, "observed": observed}))
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.release_ops",
            "observe-identity",
            "--input",
            str(spec),
            "--blobs",
            str(root),
            "--evidence",
            str(tmp_path / "evidence"),
            "--attempt",
            "test",
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["status"] == "PASS"
    assert not (tmp_path / "current").exists()


@pytest.mark.parametrize(
    "change,expected",
    [
        ("wrong_manifest", "BLOCKED"),
        ("wrong_config", "BLOCKED"),
        ("wrong_platform", "BLOCKED"),
        ("wrong_rootfs", "BLOCKED"),
        ("wrong_descriptor", "BLOCKED"),
        ("missing_field", "INCONCLUSIVE"),
        ("unknown_store", "INCONCLUSIVE"),
        ("reference_string_only", "BLOCKED"),
        ("wrong_container_resolution", "BLOCKED"),
    ],
)
def test_identity_failures(change, expected):
    approved, blobs, o = image_fixture()
    if change == "wrong_manifest":
        blobs[approved["reference"].split("@")[1]] += b" "
    if change == "wrong_config":
        approved["config_digest"] = "sha256:" + "a" * 64
    if change == "wrong_platform":
        o["by_reference"]["Architecture"] = "arm64"
    if change == "wrong_rootfs":
        o["by_reference"]["RootFS"] = {"Layers": ["sha256:" + "a" * 64]}
    if change == "wrong_descriptor":
        o["by_reference"]["Descriptor"] = {
            "digest": approved["config_digest"],
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
        }
    if change == "missing_field":
        del o["by_reference"]["Descriptor"]
    if change == "unknown_store":
        o["store"] = "unknown"
    if change == "reference_string_only":
        o["by_reference"]["RepoDigests"] = []
    if change == "wrong_container_resolution":
        o["by_container"]["Id"] = "sha256:" + "a" * 64
    assert verify_identity(approved, blobs, o)["status"] == expected


@pytest.mark.parametrize("selected_id", [False, True])
def test_index_amd64_chain_is_resolved_before_comparing(selected_id):
    a, b, o = image_fixture()
    md = a["reference"].split("@")[1]
    raw = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": md,
                    "size": len(b[md]),
                    "platform": {"os": "linux", "architecture": "amd64"},
                }
            ],
        }
    ).encode()
    idx = digest(raw)
    b[idx] = raw
    a["reference"] = "registry.example.invalid/api@" + idx
    for image in (o["by_reference"], o["by_container"]):
        image["RepoDigests"] = [a["reference"]]
        if not selected_id:
            image.update(
                Id=idx,
                Descriptor={"digest": idx, "mediaType": "application/vnd.oci.image.index.v1+json"},
            )
    o["container"].update({"Config.Image": a["reference"], "Image": md if selected_id else idx})
    r = verify_identity(a, b, o)
    assert r["status"] == "PASS"
    assert r["selected_manifest"] == md


def test_serialized_json_is_not_a_raw_manifest_digest():
    a, b, o = image_fixture()
    key = a["reference"].split("@")[1]
    b[key] = json.dumps(json.loads(b[key]), sort_keys=True, indent=2).encode()
    assert verify_identity(a, b, o)["status"] == "BLOCKED"


def test_probe_contracts_and_maintenance_are_not_interchangeable():
    normal = Contract("normal_public", "https://release.invalid/login", "normal", marker="登录")
    assert (
        response_result(normal, 200, {"Content-Type": "text/html"}, "登录".encode())["status"]
        == "PASS"
    )
    assert (
        response_result(
            normal,
            200,
            {"Content-Type": "text/html", "X-Maintenance-Mode": "read-only"},
            "登录".encode(),
        )["status"]
        == "BLOCKED"
    )
    assert response_result(normal, 503, {}, b"upstream not ready")["status"] == "NOT_READY"
    assert response_result(normal, 503, {}, b"maintenance_read_only")["status"] == "BLOCKED"
    assert (
        response_result(normal, 200, {"Content-Type": "text/html"}, b"wrong site")["status"]
        == "BLOCKED"
    )
    with pytest.raises(ValueError):
        response_result(
            Contract("normal_public", "https://release.invalid/health", "normal"), 200, {}, b"OK"
        )
    safe = Contract("safe_degrade", "https://release.invalid/health", "safe_degrade")
    assert response_result(safe, 200, {}, b'{"status":"maintenance_read_only"}')["status"] == "PASS"
    api = Contract("api_internal", "http://api:8000/ready", "normal")
    assert (
        response_result(api, 200, {}, b'{"status":"ready","database":"reachable"}')["status"]
        == "PASS"
    )
    assert response_result(api, 200, {}, b"not JSON")["status"] == "CHECK_ERROR"


@pytest.mark.parametrize(
    "status,location,expected",
    [
        (401, "", "PASS"),
        (302, "/login?secret=hidden", "PASS"),
        (302, "https://evil.invalid/login", "BLOCKED"),
        (302, "/other", "BLOCKED"),
    ],
)
def test_expected_anonymous_rejection(status, location, expected):
    c = Contract(
        "anonymous",
        "https://release.invalid/reports/existing",
        "normal",
        expected_status=status,
        redirect_path="/login",
    )
    r = response_result(c, status, {"Location": location}, b"")
    assert r["status"] == expected
    assert "hidden" not in r["location"]


def test_readiness_is_bounded_and_needs_consecutive_success(tmp_path):
    clock = [0.0]
    calls = []
    sequence = iter(["NOT_READY", "PASS", "NOT_READY", "PASS", "PASS"])
    rec = Recorder(tmp_path, "bounded")

    def probe(timeout):
        calls.append(timeout)
        return {"status": next(sequence)}

    r = wait_ready(
        probe,
        lambda i, f: rec.check(f"wait-{i}", "isolated", "frontend", 200, "readiness", f),
        clock=lambda: clock[0],
        sleep=lambda t: clock.__setitem__(0, clock[0] + t),
    )
    assert r["status"] == "PASS" and len(calls) == 5
    clock[0] = 0
    r = wait_ready(
        lambda timeout: {"status": "NOT_READY"},
        lambda i, f: f(),
        deadline=4,
        interval=2,
        clock=lambda: clock[0],
        sleep=lambda t: clock.__setitem__(0, clock[0] + t),
    )
    assert r["status"] == "NOT_READY" and clock[0] == 4
    for status in ["BLOCKED", "CHECK_ERROR", "INCONCLUSIVE"]:
        attempts = []
        r = wait_ready(
            lambda timeout: attempts.append(timeout) or {"status": status}, lambda i, f: f()
        )
        assert r["status"] == status and len(attempts) == 1


def test_transport_and_command_errors_leave_first_evidence(tmp_path):
    rec = Recorder(tmp_path, "failure")
    r = rec.check(
        "ssh",
        "mac",
        "host",
        "JSON",
        "read_metadata",
        lambda: command(
            [
                sys.executable,
                "-c",
                'import sys; print("connection lost",file=sys.stderr); sys.exit(255)',
            ],
            remote=True,
        ),
    )
    assert r["status"] == "INCONCLUSIVE" and r["exit_code"] == 255 and r["remote_exit_code"] is None
    r = rec.check(
        "json",
        "mac",
        "host",
        "JSON",
        "read_metadata",
        lambda: command([sys.executable, "-c", 'print("broken JSON")']),
    )
    assert r["exception_type"] == "JSONDecodeError"
    r = rec.check(
        "assert",
        "host",
        "proxy",
        "valid",
        "probe",
        lambda: (_ for _ in ()).throw(AssertionError("specific checkpoint")),
    )
    assert (
        r["exception_type"] == "AssertionError"
        and "specific checkpoint" in Path(r["evidence"]).read_text()
    )
    rec.check(
        "cleanup",
        "host",
        "proxy",
        "stopped",
        "cleanup",
        lambda: (_ for _ in ()).throw(RuntimeError("cleanup also failed")),
    )
    first = json.loads((tmp_path / "failure/first-failure.json").read_text())
    assert first["checkpoint_id"] == "ssh"
    assert hashlib.sha256(Path(r["evidence"]).read_bytes()).hexdigest() == r["evidence_sha256"]


def test_secret_redaction():
    r = redact(
        {
            "Cookie": "session=hidden",
            "message": (
                "Authorization: Bearer hidden\npassword=hidden\n"
                "https://person:hidden@example.invalid token=hidden"
            ),
            "token": "hidden",
        }
    )
    assert "hidden" not in json.dumps(r)


def test_checker_failure_never_claims_business_failure_or_auto_stops():
    assert (
        failure_policy("CHECK_ERROR", public_open=False)
        == "pause_collect_evidence_keep_maintenance"
    )
    assert failure_policy("CHECK_ERROR", public_open=True) == "return_maintenance_safety_unproven"
    assert (
        failure_policy(
            "CHECK_ERROR", public_open=True, safety_required=False, archival_exception_approved=True
        )
        == "service_verified_receipt_pending"
    )
    assert (
        failure_policy("CHECK_ERROR", public_open=True, safety_required=False)
        == "return_maintenance_safety_unproven"
    )


def application_pair():
    pair = [image_fixture(), image_fixture()]
    for value, role in zip(pair, ("api", "frontend"), strict=True):
        value[0]["role"] = role
    return pair


def pointer_fixture(tmp_path):
    root = tmp_path / "release-root"
    root.mkdir()
    (root / "releases").mkdir()
    old = root / "releases" / ("a" * 40)
    old.mkdir()
    target = root / "releases" / ("b" * 40)
    target.mkdir()
    (root / "current").symlink_to(old)
    prepared = current.prepare(
        root, "one", "b" * 40, str(old), application_pair(), package_sha256()
    )
    auth = {k: prepared[k] for k in ("attempt", "business_sha", "ops_package_sha256")}
    auth["allowed_actions"] = ["commit_current"]
    auth["prepared_sha256"] = hashlib.sha256(
        json.dumps(prepared, sort_keys=True).encode()
    ).hexdigest()
    public = {
        "attempt": "one",
        "status": "PASS",
        "browser_reports_read": True,
        "temporary_routes_removed": True,
        "continuous_successes": 2,
    }
    return root, old, target, prepared, auth, public


def test_same_identity_used_before_pointer_and_idempotent_commit(tmp_path):
    root, old, target, p, a, e = pointer_fixture(tmp_path)
    assert p["identity_results"][0] == verify_identity(*image_fixture())
    assert (root / "current").resolve() == old
    assert current.commit(p, a, e)["status"] == "PASS"
    assert (root / "current").resolve() == target
    assert current.commit(p, a, e)["actual"] == "already_committed"


def test_pointer_interruption_before_replace_is_recoverable(tmp_path):
    root, old, target, p, a, e = pointer_fixture(tmp_path)
    with patch(
        "scripts.release_ops.current.os.replace", side_effect=OSError("simulated interrupt")
    ):
        with pytest.raises(OSError):
            current.commit(p, a, e)
    assert (root / "current").resolve() == old
    assert current.commit(p, a, e)["status"] == "PASS"


def test_pointer_is_unchanged_on_bad_authorization_and_concurrency(tmp_path):
    root, old, target, p, a, e = pointer_fixture(tmp_path)
    wrong = copy.deepcopy(a)
    wrong["prepared_sha256"] = "bad"
    with pytest.raises(PermissionError):
        current.commit(p, wrong, e)
    with current.release_lock(root, "one"):
        with pytest.raises(BlockingIOError):
            current.commit(p, a, e)
    with pytest.raises(RuntimeError):
        with current.release_lock(root, "other"):
            pass
    assert (root / "current").resolve() == old


def test_changed_symlink_or_target_not_committed(tmp_path):
    root, old, target, p, a, e = pointer_fixture(tmp_path)
    (root / "current").unlink()
    (root / "current").write_text("not a link")
    with pytest.raises(RuntimeError):
        current.commit(p, a, e)
    assert (root / "current").read_text() == "not a link"


def test_config_bytes_alone_do_not_prove_running_configuration():
    assert (
        mounted_configuration(
            "x", "x", "x", started_after_file=False, process_uses_config=True, read_only_mount=True
        )["status"]
        == "INCONCLUSIVE"
    )
    assert (
        mounted_configuration(
            "x", "x", "y", started_after_file=True, process_uses_config=True, read_only_mount=True
        )["status"]
        == "BLOCKED"
    )


def test_action_requires_authorization_and_preserves_safety_cleanup_failures(tmp_path):
    root = tmp_path / "root"
    (root / "releases" / ("b" * 40)).mkdir(parents=True)
    binding = {
        "root": str(root),
        "attempt": "x",
        "business_sha": "b" * 40,
        "ops_package_sha256": package_sha256(),
    }
    auth = {**binding, "allowed_actions": ["isolate"]}
    rec = Recorder(tmp_path / "e", "x")
    with pytest.raises(PermissionError):
        apply_action(binding, auth, "start_api", [], rec)
    calls = []

    def execute(argv):
        calls.append(argv)
        return (
            {"status": "CHECK_ERROR", "exit_code": 1, "stderr": "original step error"}
            if len(calls) == 1
            else {"status": "PASS", "exit_code": 0, "stdout": '{"read_only":true}'}
        )

    result = apply_action(binding, auth, "isolate", [], rec, execute)
    assert len(calls) == 4 and result["read_only_verified"] and result["status"] == "CHECK_ERROR"
    assert (
        json.loads((tmp_path / "e/x/first-failure.json").read_text())["checkpoint_id"]
        == "isolate-0"
    )
    _, planned = commands(root, "b" * 40, "start_api")
    assert planned[0][-1] == "api"
    assert "current" not in " ".join(planned[0])
    assert "--no-deps" in planned[0]


def test_dns_tls_and_connect_errors_are_distinct():
    import socket
    import ssl
    import urllib.error

    from scripts.release_ops.probes import _http_once

    c = Contract("frontend_internal", "http://frontend:3000/login", "normal", marker="登录")
    for error, status, kind in [
        (socket.gaierror("missing"), "INCONCLUSIVE", "dns"),
        (ssl.SSLCertVerificationError("bad cert"), "BLOCKED", "tls_certificate"),
        (ConnectionRefusedError("not started"), "NOT_READY", "connect_timeout_or_refused"),
    ]:
        with patch("scripts.release_ops.probes.urllib.request.build_opener") as factory:
            factory.return_value.open.side_effect = urllib.error.URLError(error)
            r = _http_once(c)
        assert (r["status"], r["transport_class"]) == (status, kind)


def test_real_http_probe_deadline_and_cli_evidence(tmp_path):
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from scripts.release_ops.probes import http_probe

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/login":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write("登录".encode())
            else:
                time.sleep(1)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/login"
        assert (
            http_probe(Contract("frontend_internal", url, "normal", marker="登录"))["status"]
            == "PASS"
        )
        slow = Contract(
            "api_internal", f"http://127.0.0.1:{server.server_port}/ready", "normal", timeout=0.25
        )
        tick = time.monotonic()
        assert http_probe(slow)["status"] == "NOT_READY"
        assert time.monotonic() - tick < 1
        spec = tmp_path / "http.json"
        spec.write_text(
            json.dumps(
                {
                    "contract": {
                        "layer": "normal_public",
                        "url": url,
                        "mode": "normal",
                        "marker": "登录",
                    },
                    "bounds": {"interval": 0.01},
                    "observer_location": "mac_local_synthetic",
                }
            )
        )
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.release_ops",
                "observe-http",
                "--input",
                str(spec),
                "--evidence",
                str(tmp_path / "e"),
                "--attempt",
                "http",
            ],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout)["continuous_successes"] == 2
    finally:
        server.shutdown()
        server.server_close()


def test_rehearsal_cleanup_does_not_replace_original_exception(tmp_path):
    root, old, target, p, a, e = pointer_fixture(tmp_path)
    with (
        patch(
            "scripts.release_ops.current.os.replace", side_effect=OSError("primary replace error")
        ),
        patch(
            "scripts.release_ops.current.Path.unlink", side_effect=PermissionError("cleanup error")
        ),
    ):
        with pytest.raises(OSError, match="primary replace error") as caught:
            current.prepare(root, "one", "b" * 40, str(old), application_pair(), package_sha256())
        assert any("cleanup error" in note for note in caught.value.__notes__)
    assert (root / "current").resolve() == old


def test_unchanged_link_after_target_permission_error(tmp_path):
    root, old, target, p, a, e = pointer_fixture(tmp_path)
    with patch(
        "scripts.release_ops.current.Path.symlink_to", side_effect=PermissionError("no permission")
    ):
        with pytest.raises(PermissionError):
            current.commit(p, a, e)
    assert (root / "current").resolve() == old


def test_static_contract_and_html_word_are_not_false_alarm():
    normal = Contract("normal_public", "https://release.invalid/login", "normal", marker="登录")
    assert (
        response_result(
            normal, 200, {"Content-Type": "text/html"}, "登录 maintenance_read_only".encode()
        )["status"]
        == "PASS"
    )
    static = Contract(
        "static_maintenance",
        "https://release.invalid/login",
        "static_maintenance",
        expected_status=503,
    )
    assert (
        response_result(static, 503, {}, b'{"status":"maintenance_read_only"}')["status"] == "PASS"
    )
    assert (
        response_result(normal, 200, {}, b'{"status":"maintenance_read_only"}')["status"]
        == "BLOCKED"
    )


def test_existing_compose_healthcheck_is_reused():
    from scripts.release_ops.snapshot import observe_health

    fixture = {
        "Id": "isolated",
        "Config": {"Healthcheck": {"Test": ["CMD", "approved-health"]}},
        "State": {
            "Running": True,
            "Health": {"Status": "starting", "Log": [{"ExitCode": 1, "Output": "not ready"}]},
        },
    }
    for health, status in [("starting", "NOT_READY"), ("healthy", "PASS")]:
        fixture["State"]["Health"]["Status"] = health
        with patch(
            "scripts.release_ops.snapshot.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, json.dumps([fixture]), ""),
        ):
            assert observe_health("isolated", ["CMD", "approved-health"])["status"] == status
            assert observe_health("isolated", ["CMD", "wrong-health"])["status"] == "BLOCKED"


def test_control_uses_same_registry_proof_without_fabricated_container():
    approved, blobs, observed = image_fixture()
    approved["role"] = "safety-control"
    observed = {k: observed[k] for k in ("store", "context", "by_reference")}
    observed["scope"] = "daemon_image"
    result = verify_identity(approved, blobs, observed)
    assert result["status"] == "PASS" and result["container_id"] is None
    approved["role"] = "api"
    assert verify_identity(approved, blobs, observed)["status"] == "BLOCKED"


def test_prepare_requires_both_application_roles(tmp_path):
    assert (
        current.prepare(tmp_path, "one", "b" * 40, "old", [image_fixture()], package_sha256())[
            "status"
        ]
        == "BLOCKED"
    )


def test_evidence_storage_error_preserves_primary_failure(tmp_path):
    rec = Recorder(tmp_path, "disk")
    with patch("scripts.release_ops.evidence.write_json", side_effect=OSError("disk full")):
        result = rec.check(
            "first",
            "host",
            "proxy",
            "200",
            "http",
            lambda: {"status": "BLOCKED", "actual": "identity mismatch"},
        )
    assert result["status"] == "CHECK_ERROR"
    assert result["observation_status"] == "BLOCKED"
    assert result["actual"] == "identity mismatch"
    assert result["evidence_sha256"] is None


def test_cli_bad_json_is_recorded_without_apply(tmp_path):
    source = tmp_path / "bad.json"
    source.write_text("{broken")
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.release_ops",
            "observe-http",
            "--input",
            str(source),
            "--attempt",
            "bad-input",
            "--checkpoint",
            "public",
            "--evidence",
            str(tmp_path / "e"),
        ],
        text=True,
        capture_output=True,
    )
    result = json.loads(run.stdout)
    assert run.returncode == 2
    assert result["exception_type"] == "JSONDecodeError"
    assert Path(result["evidence"]).is_file()


def test_one_public_open_attempt_even_if_first_command_fails(tmp_path):
    from scripts.release_ops.actions import REQUIRED, authorize
    from scripts.release_ops.evidence import write_json

    root = tmp_path / "root"
    (root / "releases" / ("b" * 40)).mkdir(parents=True)
    binding = {
        "attempt": "single-open",
        "business_sha": "b" * 40,
        "ops_package_sha256": package_sha256(),
        "root": str(root),
    }
    auth = {**binding, "allowed_actions": ["open_normal_proxy"]}
    rec = Recorder(tmp_path / "e", "single-open")
    gates = []
    for gate in REQUIRED["open_normal_proxy"]:
        value = rec.check(
            gate, "host", "release", True, "synthetic-test", lambda: {"status": "PASS"}
        )
        gates.append({"evidence": value["evidence"], "sha256": value["evidence_sha256"]})
    calls = []

    def fail(argv):
        calls.append(argv)
        return {"status": "CHECK_ERROR", "exit_code": 1, "stderr": "synthetic failure"}

    authorize(binding, auth, "open_normal_proxy", gates)
    result = apply_action(binding, auth, "open_normal_proxy", gates, rec, execute=fail)
    assert result["status"] == "CHECK_ERROR" and len(calls) == 1
    with pytest.raises(FileExistsError):
        apply_action(binding, auth, "open_normal_proxy", gates, rec, execute=fail)
    assert len(calls) == 1
    # Gate data cannot be replaced while retaining its old hash.
    Path(gates[0]["evidence"]).unlink()
    write_json(gates[0]["evidence"], {"status": "PASS"})
    with pytest.raises(PermissionError, match="changed"):
        authorize(binding, auth, "open_normal_proxy", gates)


def test_pointer_interrupt_after_replace_is_read_back_without_second_formula(tmp_path):
    root, old, target, prepared, auth, public = pointer_fixture(tmp_path)
    original = current.os.fsync
    calls = 0

    def fail_directory(fd):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated fsync interruption after replace")
        return original(fd)

    with patch("scripts.release_ops.current.os.fsync", side_effect=fail_directory):
        with pytest.raises(OSError):
            current.commit(prepared, auth, public)
    assert (root / "current").resolve() == target
    assert current.commit(prepared, auth, public)["actual"] == "already_committed"


def test_metadata_is_readonly_and_checks_exact_role_baseline():
    from scripts.release_ops.metadata import database, evaluate_database

    actual = {
        "schema": "0036",
        "role_safe": True,
        "writable_tables": 0,
        "rls_tables": 42,
        "owned_relations": 0,
        "memberships": 0,
        "migration_write": False,
        "critical_rls": True,
    }
    assert evaluate_database("role-readonly", actual, {})["status"] == "PASS"
    assert evaluate_database("role-normal", actual, {})["status"] == "BLOCKED"
    assert evaluate_database("schema", {**actual, "schema": "0035"}, {})["status"] == "BLOCKED"
    with patch("scripts.release_ops.metadata.command", return_value={"status": "PASS"}) as mock:
        database(
            {
                "application_role": "synthetic_app",
                "database_container": "isolated-db",
                "database_user": "synthetic_owner",
                "database_name": "isolated",
            }
        )
    sql = mock.call_args.args[0][-1]
    assert sql.startswith("BEGIN READ ONLY;") and sql.endswith("COMMIT")
    assert "SELECT" in sql and "UPDATE " not in sql and "ALTER " not in sql


def test_compose_gate_checks_normal_flags_and_never_returns_secrets(tmp_path):
    from scripts.release_ops.metadata import observe_metadata

    release = tmp_path / "releases" / ("b" * 40)
    release.mkdir(parents=True)
    (release / "config").write_text("frozen")
    refs = {
        k: "registry.invalid/" + k + "@sha256:" + "d" * 64
        for k in ("api", "frontend", "safe-degrade-control", "safe-degrade-restore-role")
    }
    services = {
        k: {
            "image": v,
            "environment": {
                "EXTERNAL_CALLS_ENABLED": "false",
                "DATABASE_ADMIN_URL": "private-content-never-returned",
            },
        }
        for k, v in refs.items()
    }
    spec = {
        "kind": "compose-frozen",
        "root": str(tmp_path),
        "business_sha": "b" * 40,
        "images": refs,
        "disabled_flags": ["EXTERNAL_CALLS_ENABLED"],
        "configuration_sha256": {"config": hashlib.sha256(b"frozen").hexdigest()},
    }
    for key, profile in zip(list(refs)[2:], ("safe-control", "safe-restore"), strict=True):
        services[key].update(
            profiles=[profile],
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            restart="no",
            networks={"app": {}},
            command=["python", "-m", "scripts.safe_degrade_database", "restrict"]
            if profile == "safe-control"
            else ["python", "-m", "scripts.bootstrap_local_database"],
        )
    normal_services = {**{k: services[k] for k in ("api", "frontend")}, "database": {}, "proxy": {}}
    version = subprocess.CompletedProcess([], 0, "Docker Compose version v2.40.3", "")
    response = subprocess.CompletedProcess([], 0, json.dumps({"services": services}), "")
    normal = subprocess.CompletedProcess([], 0, json.dumps({"services": normal_services}), "")
    with patch(
        "scripts.release_ops.compose.subprocess.run", side_effect=[version, normal, response]
    ):
        value = observe_metadata(spec)
    assert value["status"] == "PASS"
    assert "private-content" not in json.dumps(value)
    wrong = copy.deepcopy(normal_services)
    wrong["api"]["environment"]["EXTERNAL_CALLS_ENABLED"] = "true"
    normal = subprocess.CompletedProcess([], 0, json.dumps({"services": wrong}), "")
    with patch("scripts.release_ops.compose.subprocess.run", side_effect=[version, normal]):
        assert observe_metadata(spec)["status"] == "BLOCKED"


def test_cli_readiness_summary_is_a_durable_named_gate(tmp_path):
    from types import SimpleNamespace

    from scripts.release_ops.__main__ import dispatch

    spec = tmp_path / "health.json"
    spec.write_text(
        json.dumps(
            {
                "container": "synthetic-api",
                "expected_test": ["CMD", "ready"],
                "bounds": {"interval": 0.001},
            }
        )
    )
    args = SimpleNamespace(operation="observe-health", input=str(spec))
    rec = Recorder(tmp_path / "e", "named")
    with patch(
        "scripts.release_ops.__main__.observe_health",
        return_value={"status": "PASS", "target_container_id": "synthetic-id"},
    ):
        result = dispatch(args, rec, "api-ready")
    persisted = json.loads(Path(result["evidence"]).read_text())
    assert persisted["checkpoint_id"] == "api-ready"
    assert persisted["continuous_successes"] == 2
    assert persisted["target_container_id"] == "synthetic-id"


@pytest.mark.parametrize(
    "drift",
    [
        {"owned_relations": 1},
        {"memberships": 1},
        {"migration_write": True},
        {"critical_rls": False},
    ],
)
def test_normal_role_keeps_existing_ownership_and_rls_gates(drift):
    from scripts.release_ops.metadata import evaluate_database

    actual = {
        "schema": "0036",
        "role_safe": True,
        "writable_tables": 42,
        "rls_tables": 37,
        "owned_relations": 0,
        "memberships": 0,
        "migration_write": False,
        "critical_rls": True,
        **drift,
    }
    assert (
        evaluate_database("role-normal", actual, {"writable_tables": 42, "rls_tables": 37})[
            "status"
        ]
        == "BLOCKED"
    )
