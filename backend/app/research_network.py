"""不计费的执行环境闸门；复用正式 Fetcher，绝不放宽地址或 peer 检查。"""

import hashlib
from datetime import UTC, datetime
from urllib.parse import urlsplit

from backend.app.config import SourceMonitoringPolicy
from backend.app.source_fetcher import SourceFetchError, TrustedSourceFetcher

PREFLIGHT_VERSION = "research-network-v1"
PREFLIGHT_TARGETS = (
    "https://example.com/",
    "https://www.python.org/",
    "https://www.iana.org/",
)
PREFLIGHT_POLICY = SourceMonitoringPolicy(
    max_requests_per_run=12,
    max_download_bytes_per_run=256_000,
    max_response_bytes=64_000,
    timeout_seconds=5,
    retry_limit=0,
    max_redirects=2,
    min_request_interval_ms=0,
)
NETWORK_FAILURES = {
    "execution_environment_non_public_dns",
    "dns_resolution_failed",
    "mixed_public_private_dns",
    "peer_verification_failed",
    "dns_rebinding_detected",
    "https_unreachable",
    "tls_verification_failed",
}


def public_failure_class(code):
    if code in NETWORK_FAILURES | {
        "blocked_network",
        "blocked_address",
        "peer_address_unavailable",
    }:
        return "network_environment_blocked"
    if code == "robots_disallowed":
        return "robots_denied"
    if code in {"authentication_required", "access_controlled", "captcha_required"}:
        return "access_controlled"
    if code == "rate_limited":
        return "rate_limited"
    if code in {"dynamic_rendering_required", "static_body_missing"}:
        return "dynamic_content_unavailable"
    if code in {
        "web_research_budget_deferred",
        "http_limit_reached",
        "byte_limit_reached",
        "document_limit_reached",
        "max_elapsed_seconds_reached",
        "model_budget_exhausted",
        "request_limit_exceeded",
        "run_byte_limit_exceeded",
        "execution_time_limit_reached",
    }:
        return "budget_deferred"
    return "source_unreachable" if code else None


def fetch_failure_metadata(url, category, code, log, request_count, http_status):
    target_hash = hashlib.sha256(url.encode()).hexdigest()
    return {
        "hostname": urlsplit(url).hostname,
        "category": category,
        "canonical_url_hash": target_hash,
        "dns": next((row["dns"] for row in reversed(log) if row.get("dns")), None),
        "http_dispatched": any(row.get("http_dispatched") for row in log),
        "target_http_dispatched": any(
            row.get("http_dispatched") and row.get("canonical_url_hash") == target_hash
            for row in log
        ),
        "actual_http_requests": request_count,
        "http_status": http_status,
        "failure_class": public_failure_class(code),
        "checked_at": datetime.now(UTC).isoformat(),
        "source_acquisition_status": "body_unavailable",
        "checks": [
            {
                k: row[k]
                for k in (
                    "hostname",
                    "canonical_url_hash",
                    "dns",
                    "peer_verified",
                    "peer_class",
                    "http_dispatched",
                    "status",
                    "error_code",
                    "checked_at",
                    "acquisition_phase",
                )
                if k in row
            }
            for row in log
        ],
    }


def _network_code(error, log):
    if error.code == "blocked_network":
        dns = next((r.get("dns", {}) for r in reversed(log) if r.get("dns")), {})
        if dns.get("public_count", 0) and dns.get("non_public_count", 0):
            return "mixed_public_private_dns"
        if dns.get("public_count", 0) and not dns.get("non_public_count", 0):
            return "peer_verification_failed"
        return "execution_environment_non_public_dns"
    if error.code == "peer_address_unavailable":
        return "peer_verification_failed"
    return error.code if error.code in NETWORK_FAILURES else "https_unreachable"


def run_research_network_preflight(*, fetcher_factory=TrustedSourceFetcher):
    """固定三站，最多 12 次 HTTP/256KB；不保存正文，不含 Provider 或数据库。"""
    checked_at = datetime.now(UTC).isoformat()
    result = {
        "version": PREFLIGHT_VERSION,
        "status": "research_network_ready",
        "checked_at": checked_at,
        "targets": [],
        "http_requests": 0,
        "bytes": 0,
    }
    with fetcher_factory(PREFLIGHT_POLICY) as fetcher:
        # 注入测试客户端不能被当成已验证真实执行环境；离线测试模拟底层传输。
        if not fetcher._owns_client or fetcher.allow_private_test_hosts or fetcher.allow_test_http:
            result["status"] = "peer_verification_failed"
            return result
        for url in PREFLIGHT_TARGETS:
            try:
                probe = fetcher.probe(url)
                result["targets"].append(probe)
            except SourceFetchError as error:
                result["status"] = _network_code(error, fetcher.request_log)
                result["targets"].append(
                    {
                        "hostname": url.split("/")[2],
                        "status": result["status"],
                        "failure_class": public_failure_class(error.code),
                    }
                )
                break
        result["http_requests"] = fetcher.request_count
        result["bytes"] = fetcher.downloaded_bytes
        result["checks"] = [
            {
                k: row[k]
                for k in (
                    "hostname",
                    "canonical_url_hash",
                    "dns",
                    "peer_verified",
                    "peer_class",
                    "tls_hostname_verified",
                    "status",
                    "error_code",
                    "http_dispatched",
                    "checked_at",
                )
                if k in row
            }
            for row in fetcher.request_log
        ]
    return result
