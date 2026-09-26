"""Layer-specific readiness; no redirects, credentials or business endpoints."""

import hashlib
import json
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from .evidence import location


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class Contract:
    layer: str
    url: str
    mode: str
    expected_status: int = 200
    marker: str = ""
    redirect_path: str = ""
    timeout: float = 3

    def validate(self):
        parsed = urlsplit(self.url)
        if (
            parsed.scheme not in ("http", "https")
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("probe URL must omit credentials, query and fragment")
        if self.layer not in {
            "api_internal",
            "frontend_internal",
            "normal_public",
            "safe_degrade",
            "anonymous",
            "static_maintenance",
        }:
            raise ValueError("unknown probe layer")
        if self.timeout <= 0 or self.timeout > 10:
            raise ValueError("request timeout outside reviewed bound")
        if self.layer == "api_internal" and (parsed.path != "/ready" or self.mode != "normal"):
            raise ValueError("API internal readiness must use /ready")
        if self.layer in ("frontend_internal", "normal_public") and (
            parsed.path != "/login" or self.mode != "normal" or not self.marker
        ):
            raise ValueError("normal frontend route requires /login and frozen page marker")
        if self.layer == "safe_degrade" and (
            self.mode != "safe_degrade" or parsed.path != "/health"
        ):
            raise ValueError("maintenance health has a separate contract")
        if self.layer == "static_maintenance" and (
            self.mode != "static_maintenance" or self.expected_status != 503
        ):
            raise ValueError("static maintenance has a separate 503 contract")
        if self.layer == "anonymous" and self.expected_status not in (401, 302, 303, 307):
            raise ValueError("anonymous rejection must be explicit")


def response_result(contract, status, headers, raw):
    """Body stays local; only digest/markers and sanitized Location leave the probe."""
    contract.validate()
    headers = {k.lower(): v for k, v in headers.items()}
    result = {
        "http_status": status,
        "body_sha256": hashlib.sha256(raw).hexdigest(),
        "body_bytes": len(raw),
        "location": location(headers.get("location", "")),
        "transport_class": "http",
        "url": contract.url,
        "dns_sni": urlsplit(contract.url).hostname,
        "host": urlsplit(contract.url).netloc,
        "origin": None,
        "entry_mode": contract.mode,
    }
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    maintenance = (
        headers.get("x-maintenance-mode") == "read-only"
        or raw.strip() == b"maintenance_read_only"
        or (isinstance(payload, dict) and payload.get("status") == "maintenance_read_only")
    )
    if contract.layer == "static_maintenance":
        return {
            **result,
            "status": "PASS" if status == 503 and maintenance else "BLOCKED",
            "actual": "static_maintenance_only",
        }
    if contract.layer == "safe_degrade":
        return {
            **result,
            "status": "PASS" if status == 200 and maintenance else "BLOCKED",
            "actual": "maintenance_only_not_normal_readiness",
        }
    if maintenance:
        return {**result, "status": "BLOCKED", "actual": "unexpected_maintenance_entry"}
    if status in (502, 503, 504):
        return {**result, "status": "NOT_READY", "actual": "upstream_not_ready"}
    if status != contract.expected_status:
        return {**result, "status": "BLOCKED", "actual": "unexpected_http_status"}
    if contract.layer == "anonymous":
        target = urlsplit(headers.get("location", ""))
        host = urlsplit(contract.url)
        if status != 401 and (
            target.path != contract.redirect_path
            or (target.hostname and target.hostname != host.hostname)
        ):
            return {**result, "status": "BLOCKED", "actual": "unexpected_auth_redirect"}
    elif contract.layer == "api_internal":
        try:
            value = json.loads(raw)
        except ValueError as exc:
            return {
                **result,
                "status": "CHECK_ERROR",
                "exception_type": type(exc).__name__,
                "actual": "invalid_ready_payload",
            }
        if value.get("status") != "ready" or value.get("database") != "reachable":
            return {**result, "status": "NOT_READY", "actual": "database_not_ready"}
    elif contract.marker.encode() not in raw or "text/html" not in headers.get("content-type", ""):
        return {**result, "status": "BLOCKED", "actual": "wrong_frontend_document"}
    return {**result, "status": "PASS", "actual": "expected_layer_response"}


def _http_once(contract):
    contract.validate()
    opener = urllib.request.build_opener(NoRedirect)
    try:
        try:
            response = opener.open(
                urllib.request.Request(
                    contract.url, headers={"User-Agent": "DealflowReleaseProbe/1"}
                ),
                timeout=contract.timeout,
            )
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            raw = response.read(512001)
            if len(raw) > 512000:
                return {"status": "CHECK_ERROR", "actual": "probe_response_exceeds_bound"}
            return response_result(contract, response.status, dict(response.headers), raw)
    except (urllib.error.URLError, OSError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        if isinstance(reason, ssl.SSLCertVerificationError):
            status, category = "BLOCKED", "tls_certificate"
        elif isinstance(reason, socket.gaierror):
            status, category = "INCONCLUSIVE", "dns"
        elif isinstance(reason, (ConnectionRefusedError, TimeoutError, socket.timeout)):
            status, category = "NOT_READY", "connect_timeout_or_refused"
        else:
            status, category = "INCONCLUSIVE", "transport"
        return {
            "status": status,
            "transport_class": category,
            "exception_type": type(reason).__name__,
            "actual": str(reason),
            "url": contract.url,
            "dns_sni": urlsplit(contract.url).hostname,
            "host": urlsplit(contract.url).netloc,
            "origin": None,
            "entry_mode": contract.mode,
        }


def wait_ready(
    probe,
    record,
    *,
    deadline=60,
    interval=2,
    max_attempts=12,
    consecutive=2,
    clock=time.monotonic,
    sleep=time.sleep,
):
    if not (
        0 < deadline <= 120
        and 0 < interval <= 10
        and 1 <= max_attempts <= 30
        and 2 <= consecutive <= max_attempts
    ):
        raise ValueError("readiness bounds invalid")
    end = clock() + deadline
    streak = 0
    last = None
    for index in range(max_attempts):
        remaining = end - clock()
        if remaining <= 0:
            break
        last = record(index, lambda: probe(min(3, remaining)))
        if clock() > end:
            break
        if last["status"] == "PASS":
            streak += 1
            if streak >= consecutive:
                return {**last, "continuous_successes": streak, "attempts": index + 1}
        elif last["status"] == "NOT_READY":
            streak = 0
        else:
            return last
        sleep(min(interval, max(0, end - clock())))
    return {
        "status": "NOT_READY",
        "actual": "readiness_deadline_or_attempt_limit",
        "last_evidence": last,
        "continuous_successes": streak,
    }


def mounted_configuration(
    expected_sha, host_sha, mounted_sha, *, started_after_file, process_uses_config, read_only_mount
):
    if host_sha != expected_sha or mounted_sha != expected_sha:
        return {"status": "BLOCKED", "actual": "configuration_digest_mismatch"}
    if not all((started_after_file, process_uses_config, read_only_mount)):
        return {"status": "INCONCLUSIVE", "actual": "runtime_configuration_adoption_unproven"}
    return {"status": "PASS", "actual": "frozen_config_mounted_and_started"}


def failure_policy(status, *, public_open, safety_required=True, archival_exception_approved=False):
    if status == "PASS":
        return "advance"
    if status == "BLOCKED":
        return "isolate_with_approved_safety_sequence"
    if status == "NOT_READY":
        return "bounded_wait_keep_maintenance"
    if not safety_required and public_open and archival_exception_approved:
        return "service_verified_receipt_pending"
    if public_open:
        return "return_maintenance_safety_unproven"
    return "pause_collect_evidence_keep_maintenance"


def http_probe(contract):
    """A child-process deadline also bounds slow-drip bodies and DNS/SSL calls."""
    contract.validate()
    try:
        run = subprocess.run(
            [sys.executable, "-m", "scripts.release_ops.http_worker"],
            input=json.dumps(asdict(contract)),
            text=True,
            capture_output=True,
            timeout=contract.timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "NOT_READY",
            "transport_class": "request_deadline",
            "exception_type": "TimeoutExpired",
            "actual": "total_request_deadline",
        }
    if run.returncode:
        return {
            "status": "CHECK_ERROR",
            "exit_code": run.returncode,
            "exception_type": "HTTPWorkerError",
            "stderr": run.stderr,
        }
    try:
        return json.loads(run.stdout)
    except ValueError as exc:
        return {
            "status": "CHECK_ERROR",
            "exception_type": type(exc).__name__,
            "actual": "invalid_probe_worker_response",
        }
