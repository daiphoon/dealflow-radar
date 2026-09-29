import ssl

import httpx
import pytest

from backend.app.research_network import run_research_network_preflight
from backend.app.source_fetcher import TrustedSourceFetcher

PUBLIC = "93.184.216.34"


class Peer:
    def __init__(self, address):
        self.address = address

    def get_extra_info(self, name):
        return (self.address, 443) if self.address else None


def probe(monkeypatch, addresses, *, peer=PUBLIC, dispatch=None):
    calls = []

    def transport(_, request):
        calls.append(str(request.url))
        if dispatch:
            return dispatch(request)
        robots = request.url.path == "/robots.txt"
        return httpx.Response(
            404 if robots else 200,
            content=b"" if robots else b"<html>public test</html>",
            headers={"content-type": "text/plain" if robots else "text/html"},
            extensions={"network_stream": Peer(peer)},
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", transport)
    result = run_research_network_preflight(
        fetcher_factory=lambda policy: TrustedSourceFetcher(
            policy,
            resolver=addresses if callable(addresses) else lambda *_: addresses,
        )
    )
    return result, calls


@pytest.mark.parametrize("address", [PUBLIC, "2606:4700:4700::1111"])
def test_public_ipv4_ipv6_with_verified_peer_ready(monkeypatch, address):
    result, calls = probe(monkeypatch, [address], peer=address)
    assert result["status"] == "research_network_ready"
    assert len(calls) == 6 and result["http_requests"] == 6
    assert all(row["peer_verified"] and row["tls_hostname_verified"] for row in result["checks"])


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.1.1",
        "169.254.169.254",
        "224.0.0.1",
        "198.18.0.1",
        "198.19.255.254",
        "192.0.2.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::ffff:127.0.0.1",
    ],
)
def test_non_public_dns_never_dispatched(monkeypatch, address):
    result, calls = probe(monkeypatch, [address])
    assert result["status"] == "execution_environment_non_public_dns"
    assert result["http_requests"] == 0 and calls == []
    assert address not in str(result)


def test_mixed_dns_fails_closed(monkeypatch):
    result, calls = probe(monkeypatch, [PUBLIC, "10.0.0.1"])
    assert result["status"] == "mixed_public_private_dns" and not calls


def test_dns_resolution_empty(monkeypatch):
    result, calls = probe(monkeypatch, [])
    assert result["status"] == "dns_resolution_failed" and not calls


@pytest.mark.parametrize(
    "peer,code",
    [
        (None, "peer_verification_failed"),
        ("10.0.0.1", "peer_verification_failed"),
        ("1.1.1.1", "dns_rebinding_detected"),
    ],
)
def test_unverified_or_mismatched_peer_denied(monkeypatch, peer, code):
    result, calls = probe(monkeypatch, [PUBLIC], peer=peer)
    assert result["status"] == code and len(calls) == 1


def test_post_dispatch_non_public_dns_rebinding(monkeypatch):
    answers = iter([[PUBLIC], ["10.0.0.1"]])
    result, calls = probe(monkeypatch, lambda *_: next(answers))
    assert result["status"] == "dns_rebinding_detected" and len(calls) == 1


def test_redirect_to_non_public_same_domain_checked_before_dispatch(monkeypatch):
    def redirect(request):
        return httpx.Response(
            302,
            headers={"location": "https://private.example.com/"},
            extensions={"network_stream": Peer(PUBLIC)},
        )

    result, calls = probe(
        monkeypatch,
        lambda host, _: ["10.0.0.1"] if host.startswith("private") else [PUBLIC],
        dispatch=redirect,
    )
    assert result["status"] == "execution_environment_non_public_dns" and len(calls) == 1


def test_tls_hostname_verification_failure_is_distinct(monkeypatch):
    def fail(_):
        try:
            raise ssl.SSLCertVerificationError("mock hostname mismatch")
        except ssl.SSLCertVerificationError as error:
            raise httpx.ConnectError("TLS failed") from error

    result, calls = probe(monkeypatch, [PUBLIC], dispatch=fail)
    assert result["status"] == "tls_verification_failed" and len(calls) == 1


def test_injected_client_does_not_bypass_formal_gate():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        result = run_research_network_preflight(
            fetcher_factory=lambda policy: TrustedSourceFetcher(
                policy, client=client, resolver=lambda *_: [PUBLIC]
            )
        )
    assert result["status"] == "peer_verification_failed"
