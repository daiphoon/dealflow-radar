import ssl

import httpx
import pytest

from backend.app.config import SourceMonitoringPolicy
from backend.app.source_fetcher import SourceFetchError, TrustedSourceFetcher


class BadEcpoint(ssl.SSLError):
    reason = "BAD_ECPOINT"


class Peer:
    def get_extra_info(self, key):
        return ("93.184.216.34", 443) if key == "server_addr" else None


def setup_fetcher(monkeypatch, *, failure=None, compatibility_failure=False, denied=False, cap=5):
    real_client = httpx.Client
    calls = []
    clients = []

    def factory(**kwargs):
        compatible = "verify" in kwargs
        if compatible:
            context = kwargs["verify"]
            assert context.check_hostname is True
            assert context.verify_mode == ssl.CERT_REQUIRED
            assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False

        def handle(request):
            calls.append((compatible, request.url.host, request.url.path))
            assert not request.headers.get("cookie")
            if not compatible or compatibility_failure:
                cause = failure or BadEcpoint("handshake failed")
                raise httpx.ConnectError("TLS failed") from cause
            if request.url.path == "/robots.txt":
                return httpx.Response(
                    200,
                    headers={"content-type": "text/plain"},
                    text="User-agent: *\n" + ("Disallow: /" if denied else "Allow: /"),
                    extensions={"network_stream": Peer()},
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<title>公开资料</title><main>公司公开资料。</main>",
                extensions={"network_stream": Peer()},
            )

        client = real_client(transport=httpx.MockTransport(handle))
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "Client", factory)
    policy = SourceMonitoringPolicy(
        max_requests_per_run=cap, retry_limit=0, min_request_interval_ms=0
    )
    fetcher = TrustedSourceFetcher(policy, resolver=lambda host, port: {"93.184.216.34"})
    return fetcher, calls, clients


def check(fetcher):
    return fetcher.check(
        source_type="single_page",
        root_domain="example.com",
        start_url="https://example.com/news",
        retention_policy="minimal_excerpt",
        conditional_state={},
    )


def test_bad_ecpoint_uses_verified_compatibility_once_and_counts_every_attempt(monkeypatch):
    fetcher, calls, clients = setup_fetcher(monkeypatch)
    result = check(fetcher)
    assert result.request_count == 3
    assert len(result.documents) == 1
    assert calls == [
        (False, "example.com", "/robots.txt"),
        (True, "example.com", "/robots.txt"),
        (True, "example.com", "/news"),
    ]
    assert result.request_log[0]["error_code"] == "tls_ecpoint_error"
    # Compatibility is not propagated to a different redirect host.
    with pytest.raises(SourceFetchError):
        fetcher._request_once("https://other.example.com/", {}, allow_plain_text=True)
    assert calls[-1][0] is False
    fetcher.close()
    assert all(c.is_closed for c in clients)


@pytest.mark.parametrize(
    "failure",
    [
        ssl.SSLCertVerificationError("certificate failed"),
        ssl.SSLError("unknown SSL error"),
        OSError("network failure"),
    ],
)
def test_certificate_or_unrelated_network_errors_never_trigger_compatibility(monkeypatch, failure):
    fetcher, calls, clients = setup_fetcher(monkeypatch, failure=failure)
    with pytest.raises(SourceFetchError):
        check(fetcher)
    assert len(calls) == 1
    assert len(clients) == 1
    fetcher.close()


def test_compatibility_failure_does_not_loop(monkeypatch):
    fetcher, calls, _ = setup_fetcher(monkeypatch, compatibility_failure=True)
    with pytest.raises(SourceFetchError):
        check(fetcher)
    assert len(calls) == 2
    fetcher.close()


def test_compatibility_does_not_exceed_request_budget(monkeypatch):
    fetcher, calls, clients = setup_fetcher(monkeypatch, cap=1)
    with pytest.raises(SourceFetchError):
        check(fetcher)
    assert len(calls) == len(clients) == fetcher.request_count == 1
    fetcher.close()


def test_compatible_connection_still_obeys_robots(monkeypatch):
    fetcher, calls, _ = setup_fetcher(monkeypatch, denied=True)
    with pytest.raises(SourceFetchError) as caught:
        check(fetcher)
    assert caught.value.code == "robots_disallowed"
    assert len(calls) == 2
    assert all(c[2] == "/robots.txt" for c in calls)
    fetcher.close()
