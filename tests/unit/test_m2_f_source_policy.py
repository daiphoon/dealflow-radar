"""M2-F public HTTP policy cases use the actual bounded source fetcher."""

import httpx
import pytest

from backend.app.config import SourceMonitoringPolicy
from backend.app.source_fetcher import SourceFetchError, TrustedSourceFetcher

PUBLIC_IP = {"93.184.216.34"}


def _fetcher(handler, *, max_requests=4, max_bytes=100_000):
    policy = SourceMonitoringPolicy(
        robots_mode="advisory_public_http",
        max_requests_per_run=max_requests,
        max_download_bytes_per_run=max_bytes,
        max_response_bytes=50_000,
        min_request_interval_ms=0,
        retry_limit=0,
    )
    return TrustedSourceFetcher(
        policy,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=lambda _host, _port: PUBLIC_IP,
    )


def _check(fetcher):
    return fetcher.check(
        source_type="single_page",
        root_domain="example.com",
        start_url="https://example.com/news/1",
        retention_policy="minimal_excerpt",
        conditional_state={},
        acquisition_route="PUBLIC_STANDARD_HTTP",
    )


@pytest.mark.parametrize(
    ("robots_status", "robots_content_type", "robots_body", "observation"),
    [
        (200, "text/plain", "User-agent: *\nAllow: /\n", "allowed_observed"),
        (200, "text/plain", "User-agent: *\nDisallow: /news/\n", "disallowed_observed"),
        (404, "text/plain", "", "missing"),
        (500, "text/html", "Unavailable", "unavailable"),
        (401, "text/html", "Unauthorized", "unavailable"),
        (403, "text/html", "Forbidden", "unavailable"),
        (200, "text/html", "<html>not robots</html>", "invalid"),
        (200, "text/plain", "not a robots file", "invalid"),
    ],
)
def test_advisory_robots_records_observation_then_reads_public_body(
    robots_status, robots_content_type, robots_body, observation
):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(
                robots_status,
                headers={"content-type": robots_content_type},
                text=robots_body,
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<title>公开资料</title><main>示例公司发布公开产品信息。</main>",
        )

    with _fetcher(handler) as fetcher:
        result = _check(fetcher)
        assert len(result.documents) == 1
        assert result.robots_status == observation
        assert fetcher.robots_observations[-1]["decision"] == "advisory_continue"
        assert fetcher.robots_observations[-1]["policy_version"]
        assert result.request_count == 2
        audit = result.documents[0].metadata["source_acquisition"]
        assert (
            audit["source_response_bytes"] + audit["auxiliary_response_bytes"]
            == result.downloaded_bytes
        )
    assert paths == ["/robots.txt", "/news/1"]


@pytest.mark.parametrize(
    ("body_status", "body_html", "code"),
    [
        (401, "Unauthorized", "authentication_required"),
        (403, "Forbidden", "access_controlled"),
        (429, "Rate limit", "rate_limited"),
        (200, "<title>会员阅读</title><main>开通会员查看全文</main>", "access_controlled"),
        (200, "<title>登录</title><main>请登录账户以查看全文</main>", "authentication_required"),
        (
            200,
            "<title>安全验证</title><main>请完成验证码" + "提示" * 600 + "</main>",
            "captcha_required",
        ),
    ],
)
def test_advisory_does_not_bypass_target_access_controls(body_status, body_html, code):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, headers={"content-type": "text/plain"}, text="User-agent: *\nDisallow: /\n"
            )
        return httpx.Response(body_status, headers={"content-type": "text/html"}, text=body_html)

    with _fetcher(handler) as fetcher:
        with pytest.raises(SourceFetchError) as caught:
            _check(fetcher)
        assert caught.value.code == code
        assert fetcher.request_count == 2


def test_robots_timeout_is_observed_and_every_attempt_is_metered():
    def handler(request):
        if request.url.path == "/robots.txt":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<main>公开信息</main>"
        )

    with _fetcher(handler) as fetcher:
        result = _check(fetcher)
        assert result.request_count == 2
        assert result.robots_status == "unavailable"
        assert fetcher.robots_observations[0]["fetch_error_code"] == "timeout"


@pytest.mark.parametrize("phase", ["robots", "body"])
def test_advisory_never_swallows_private_redirect(phase):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if phase == "robots" or request.url.path != "/robots.txt":
            return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})
        return httpx.Response(404, headers={"content-type": "text/plain"})

    with _fetcher(handler) as fetcher:
        with pytest.raises(SourceFetchError) as caught:
            _check(fetcher)
        assert caught.value.code in {"blocked_host", "domain_not_allowed", "blocked_address"}
        assert len(paths) == (1 if phase == "robots" else 2)


def test_advisory_private_dns_and_cancel_still_stop_before_dispatch():
    with _fetcher(lambda _: pytest.fail("no HTTP")) as fetcher:
        fetcher.resolver = lambda *_: {"169.254.169.254"}
        with pytest.raises(SourceFetchError, match="non-public"):
            _check(fetcher)
        assert fetcher.request_count == 0
    with _fetcher(lambda _: pytest.fail("no HTTP")) as fetcher:
        fetcher.bind_request_guard(lambda: False)
        with pytest.raises(SourceFetchError) as caught:
            _check(fetcher)
        assert caught.value.code == "research_cancelled"
        assert fetcher.request_count == 0


def test_redirect_to_login_does_not_replay_cookie():
    paths = []

    def handler(request):
        assert not request.headers.get("cookie")
        paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/news/1":
            return httpx.Response(
                302, headers={"location": "/login", "set-cookie": "session=fixture"}
            )
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<title>Sign in</title>"
        )

    with _fetcher(handler) as fetcher:
        with pytest.raises(SourceFetchError) as caught:
            _check(fetcher)
        assert caught.value.code == "authentication_required"
        assert fetcher.request_count == 3
    assert paths == ["/robots.txt", "/news/1", "/login"]


def test_public_js_shell_is_distinct_from_challenge():
    from types import SimpleNamespace

    from backend.app.research_subject import BusinessExcerptSelector

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<title>公开产品</title><div id="app"></div>',
        )

    with _fetcher(handler) as fetcher:
        with pytest.raises(SourceFetchError) as caught:
            fetcher.check(
                source_type="single_page",
                root_domain="example.com",
                start_url="https://example.com/news/1",
                retention_policy="minimal_excerpt",
                conditional_state={},
                acquisition_route="PUBLIC_STANDARD_HTTP",
                excerpt_selector=BusinessExcerptSelector(
                    SimpleNamespace(legal_name="示例公司", aliases=())
                ),
            )
        assert caught.value.code == "dynamic_rendering_required"


def test_public_article_about_security_is_not_a_captcha_gate():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<title>公开技术进展</title><main>示例公司发布验证码安全技术产品。</main>",
        )

    with _fetcher(handler) as fetcher:
        assert len(_check(fetcher).documents) == 1


@pytest.mark.parametrize("source_type", ["pdf", "rss", "atom"])
def test_public_structured_and_pdf_paths_keep_bounded_existing_parser(source_type):
    from tests.unit.test_source_fetcher import _pdf_bytes

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, headers={"content-type": "text/plain"}, text="User-agent: *\nDisallow: /\n"
            )
        if source_type == "pdf":
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=_pdf_bytes("Official Company released a product."),
            )
        body = (
            "<rss><channel><item><title>公开资料</title><link>https://example.com/news/2</link><description>公开摘要</description></item></channel></rss>"
            if source_type == "rss"
            else '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>公开资料</title><link href="https://example.com/news/2"/><summary>公开摘要</summary></entry></feed>'
        )
        return httpx.Response(
            200,
            headers={
                "content-type": "application/rss+xml"
                if source_type == "rss"
                else "application/atom+xml"
            },
            text=body,
        )

    with _fetcher(handler) as fetcher:
        result = fetcher.check(
            source_type="single_page" if source_type == "pdf" else "rss",
            root_domain="example.com",
            start_url="https://example.com/news/1",
            retention_policy="minimal_excerpt",
            conditional_state={},
            acquisition_route="PUBLIC_STANDARD_HTTP",
        )
        assert len(result.documents) == 1
        assert result.request_count == 2


def test_cached_robots_observation_and_byte_budget_are_not_hidden():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(
            200,
            headers={
                "content-type": "text/plain" if request.url.path == "/robots.txt" else "text/html"
            },
            text="User-agent: *\nDisallow: /\n"
            if request.url.path == "/robots.txt"
            else "<main>公开资料</main>",
        )

    with _fetcher(handler) as fetcher:
        _check(fetcher)
        second = _check(fetcher)
        assert fetcher.robots_observations[-1]["from_cache"]
        assert fetcher.robots_observations[-1]["matched_rule"] == "Disallow: /"
        assert second.request_count == 3
    assert paths.count("/robots.txt") == 1


@pytest.mark.parametrize("peer", ["169.254.169.254", "1.1.1.1", None])
def test_robots_peer_failure_is_never_advisory(peer):
    class Stream:
        def get_extra_info(self, name):
            return (peer, 443) if name == "server_addr" and peer else None

    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="User-agent: *\nAllow: /\n",
            extensions={"network_stream": Stream()},
        )

    with _fetcher(handler) as fetcher:
        fetcher._owns_client = True  # Mock transport also exercises the production peer guard.
        with pytest.raises(SourceFetchError) as caught:
            _check(fetcher)
        assert caught.value.code in {
            "blocked_network",
            "dns_rebinding_detected",
            "peer_address_unavailable",
        }
        assert paths == ["/robots.txt"]


def test_robots_run_byte_exhaustion_stops_without_dispatching_body():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(
            200, headers={"content-type": "text/plain"}, text="User-agent: *\nAllow: /\n"
        )

    with _fetcher(handler, max_bytes=8) as fetcher:
        with pytest.raises(SourceFetchError) as caught:
            _check(fetcher)
        assert caught.value.code == "run_byte_limit_exceeded"
        assert paths == ["/robots.txt"]


def test_advisory_requires_explicit_route_and_budget_for_body_request():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, headers={"content-type": "text/plain"}, text="User-agent: *\nDisallow: /\n"
            )
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<main>公开资料</main>"
        )

    with _fetcher(handler) as fetcher:
        with pytest.raises(SourceFetchError) as caught:
            fetcher.check(
                source_type="single_page",
                root_domain="example.com",
                start_url="https://example.com/news/1",
                retention_policy="minimal_excerpt",
                conditional_state={},
            )
        assert caught.value.code == "robots_disallowed"
        assert paths == ["/robots.txt"]

    paths.clear()
    with _fetcher(handler, max_requests=1) as fetcher:
        with pytest.raises(SourceFetchError) as caught:
            _check(fetcher)
        assert caught.value.code == "request_limit_exceeded"
        assert fetcher.request_count == 1
        assert paths == ["/robots.txt"]
