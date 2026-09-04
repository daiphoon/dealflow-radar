from __future__ import annotations

import io
from collections.abc import Callable

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.app.config import SourceMonitoringPolicy
from backend.app.source_fetcher import (
    SourceFetchError,
    TrustedSourceFetcher,
    UrlSafetyError,
    canonicalize_source_url,
)

PUBLIC_ADDRESS = {"93.184.216.34"}


def _pdf_bytes(text: str, *, pages: int = 1) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    for index in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
        )
        stream = DecodedStreamObject()
        value = text if index == 0 else f"Page {index + 1}"
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({value}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": "Official filing"})
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _policy(**overrides: int) -> SourceMonitoringPolicy:
    values = {
        "max_requests_per_run": 10,
        "max_download_bytes_per_run": 100_000,
        "max_response_bytes": 50_000,
        "timeout_seconds": 2,
        "retry_limit": 0,
        "max_redirects": 2,
        "min_request_interval_ms": 0,
        "worker_lease_seconds": 60,
    }
    values.update(overrides)
    return SourceMonitoringPolicy(**values)


def _fetcher(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    policy: SourceMonitoringPolicy | None = None,
    resolver: Callable[[str, int], set[str]] | None = None,
) -> TrustedSourceFetcher:
    return TrustedSourceFetcher(
        policy or _policy(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=resolver or (lambda _host, _port: PUBLIC_ADDRESS),
    )


@pytest.mark.parametrize(
    ("url", "root_domain", "code"),
    [
        ("http://example.com/news", "example.com", "blocked_scheme"),
        ("file:///etc/passwd", "example.com", "blocked_scheme"),
        ("https://localhost/news", "localhost", "blocked_host"),
        ("https://127.0.0.1/news", "127.0.0.1", "blocked_host"),
        ("https://user:secret@example.com/news", "example.com", "userinfo_not_allowed"),
        ("https://other.example/news", "example.com", "domain_not_allowed"),
        ("https://example.com/download.exe", "example.com", "blocked_file_type"),
        ("https://example.com/news", "exam_ple.com", "invalid_root_domain"),
    ],
)
def test_url_configuration_blocks_unsafe_targets(url: str, root_domain: str, code: str) -> None:
    with pytest.raises(UrlSafetyError) as caught:
        canonicalize_source_url(url, root_domain)
    assert caught.value.code == code


def test_single_page_obeys_robots_and_extracts_minimal_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"].startswith("DealflowRadarSourceMonitor/")
        assert not request.headers.get("cookie")
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                text="User-agent: *\nAllow: /\n",
            )
        return httpx.Response(
            200,
            headers={
                "content-type": "text/html; charset=utf-8",
                "etag": '"page-v1"',
                "last-modified": "Thu, 16 Jul 2026 08:00:00 GMT",
            },
            text=(
                "<html><head><title>可信新闻</title>"
                '<meta property="article:published_time" content="2026-07-16T08:00:00Z">'
                "</head><body><script>secret()</script><p>公开的最小正文。</p></body></html>"
            ),
        )

    fetcher = _fetcher(handler)
    result = fetcher.check(
        source_type="single_page",
        root_domain="example.com",
        start_url="https://example.com/news/1",
        retention_policy="minimal_excerpt",
        conditional_state={},
    )
    assert result.request_count == 2
    assert result.robots_status == "checked"
    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.title == "可信新闻"
    assert "公开的最小正文" in (document.excerpt or "")
    assert "secret" not in (document.excerpt or "")
    assert (document.excerpt or "").count("公开的最小正文") == 1
    assert document.etag == '"page-v1"'
    assert document.published_at is not None
    assert document.metadata["extraction_method"] == "clean_body"


def test_single_page_prefers_main_content_and_removes_navigation_and_related_cards() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, headers={"content-type": "text/plain"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                "<html><head><title>目标公司公告</title></head><body>"
                "<header>首页 融资 产品 联系我们</header>"
                "<nav>热门融资榜单</nav>"
                "<main><h1>目标公司公告</h1><p>目标公司完成产品注册。</p>"
                '<section class="related news"><p>其他公司完成融资。</p></section>'
                "</main><footer>版权和推荐阅读</footer></body></html>"
            ),
        )

    result = _fetcher(handler).check(
        source_type="single_page",
        root_domain="example.com",
        start_url="https://example.com/news/quality",
        retention_policy="minimal_excerpt",
        conditional_state={},
    )

    document = result.documents[0]
    assert document.metadata["extraction_method"] == "main_content"
    assert "目标公司完成产品注册" in (document.excerpt or "")
    assert "热门融资榜单" not in (document.excerpt or "")
    assert "其他公司完成融资" not in (document.excerpt or "")
    assert "版权和推荐阅读" not in (document.excerpt or "")


def test_robots_denial_stops_before_page_request() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="User-agent: *\nDisallow: /private\n",
        )

    fetcher = _fetcher(handler)
    with pytest.raises(SourceFetchError) as caught:
        fetcher.check(
            source_type="single_page",
            root_domain="example.com",
            start_url="https://example.com/private/report",
            retention_policy="metadata_only",
            conditional_state={},
        )
    assert caught.value.code == "robots_disallowed"
    assert paths == ["/robots.txt"]


def test_robots_redirected_to_html_fails_closed() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(302, headers={"location": "/"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body>not robots rules</body></html>",
        )

    fetcher = _fetcher(handler)
    with pytest.raises(SourceFetchError) as caught:
        fetcher.check(
            source_type="single_page",
            root_domain="example.com",
            start_url="https://example.com/news",
            retention_policy="metadata_only",
            conditional_state={},
        )

    assert caught.value.code == "robots_disallowed"
    assert paths == ["/robots.txt", "/"]


def test_dns_private_address_and_rebinding_are_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                404,
                headers={"content-type": "text/plain"},
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<title>Rebind</title><p>must be discarded</p>",
        )

    private_fetcher = _fetcher(handler, resolver=lambda _host, _port: {"10.0.0.1"})
    with pytest.raises(UrlSafetyError) as private_error:
        private_fetcher.check(
            source_type="single_page",
            root_domain="example.com",
            start_url="https://example.com/news",
            retention_policy="metadata_only",
            conditional_state={},
        )
    assert private_error.value.code == "blocked_network"
    assert private_fetcher.request_count == 0

    answers = iter([PUBLIC_ADDRESS, PUBLIC_ADDRESS, PUBLIC_ADDRESS, {"169.254.169.254"}])
    rebound_fetcher = _fetcher(handler, resolver=lambda _host, _port: next(answers))
    with pytest.raises(UrlSafetyError) as rebound_error:
        rebound_fetcher.check(
            source_type="single_page",
            root_domain="example.com",
            start_url="https://example.com/news",
            retention_policy="metadata_only",
            conditional_state={},
        )
    assert rebound_error.value.code == "blocked_network"
    assert rebound_fetcher.request_count == 2


@pytest.mark.parametrize(
    ("peer_address", "allowed_addresses", "code"),
    [
        ("169.254.169.254", PUBLIC_ADDRESS, "blocked_network"),
        ("1.1.1.1", PUBLIC_ADDRESS, "dns_rebinding_detected"),
    ],
)
def test_connected_peer_must_be_public_and_match_preflight_dns(
    peer_address: str,
    allowed_addresses: set[str],
    code: str,
) -> None:
    class NetworkStream:
        @staticmethod
        def get_extra_info(name: str) -> object:
            return (peer_address, 443) if name == "server_addr" else None

    fetcher = TrustedSourceFetcher(_policy())
    try:
        response = httpx.Response(
            200,
            extensions={"network_stream": NetworkStream()},
        )
        with pytest.raises(UrlSafetyError) as caught:
            fetcher._check_peer_address(response, allowed_addresses)
        assert caught.value.code == code
    finally:
        fetcher.close()


def test_redirect_escape_and_redirect_limit_are_blocked() -> None:
    def outside_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, headers={"content-type": "text/plain"})
        return httpx.Response(302, headers={"location": "https://evil.example/private"})

    outside = _fetcher(outside_handler)
    with pytest.raises(UrlSafetyError) as outside_error:
        outside.check(
            source_type="single_page",
            root_domain="example.com",
            start_url="https://example.com/news",
            retention_policy="metadata_only",
            conditional_state={},
        )
    assert outside_error.value.code == "domain_not_allowed"

    def loop_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, headers={"content-type": "text/plain"})
        return httpx.Response(302, headers={"location": f"/next{request.url.path}"})

    loop = _fetcher(loop_handler, policy=_policy(max_redirects=1))
    with pytest.raises(SourceFetchError) as loop_error:
        loop.check(
            source_type="single_page",
            root_domain="example.com",
            start_url="https://example.com/start",
            retention_policy="metadata_only",
            conditional_state={},
        )
    assert loop_error.value.code == "redirect_limit_exceeded"


def test_conditional_304_and_response_size_limit() -> None:
    def unchanged_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, headers={"content-type": "text/plain"})
        assert request.headers["if-none-match"] == '"known"'
        return httpx.Response(304, headers={"etag": '"known"'})

    unchanged = _fetcher(unchanged_handler)
    result = unchanged.check(
        source_type="single_page",
        root_domain="example.com",
        start_url="https://example.com/news",
        retention_policy="metadata_only",
        conditional_state={
            "https://example.com/news": {
                "etag": '"known"',
                "last_modified": None,
                "content_hash": "a" * 64,
            }
        },
    )
    assert result.documents == []
    assert result.unchanged_urls == ["https://example.com/news"]
    assert result.start_content_hash == "a" * 64

    def large_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, headers={"content-type": "text/plain"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "1000"},
            content=b"x" * 1000,
        )

    large = _fetcher(large_handler, policy=_policy(max_response_bytes=100))
    with pytest.raises(SourceFetchError) as large_error:
        large.check(
            source_type="single_page",
            root_domain="example.com",
            start_url="https://example.com/large",
            retention_policy="metadata_only",
            conditional_state={},
        )
    assert large_error.value.code == "response_too_large"
    assert large.request_log[-1]["error_code"] == "response_too_large"
    assert large.request_log[-1]["status"] == 200


def test_retry_limit_and_pdf_signature_check_are_audited() -> None:
    attempts = 0

    def timeout_then_success(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404, headers={"content-type": "text/plain"})
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"not accepted",
        )

    fetcher = _fetcher(timeout_then_success, policy=_policy(retry_limit=1))
    with pytest.raises(SourceFetchError) as caught:
        fetcher.check(
            source_type="single_page",
            root_domain="example.com",
            start_url="https://example.com/news",
            retention_policy="metadata_only",
            conditional_state={},
        )
    assert caught.value.code == "invalid_pdf_signature"
    assert fetcher.request_count == 3
    assert [entry.get("error_code") for entry in fetcher.request_log] == [
        "timeout",
        None,
        None,
    ]


def test_official_pdf_is_parsed_with_signature_page_and_text_limits() -> None:
    body = _pdf_bytes("Official Company completed financing on 2026-09-04.")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, headers={"content-type": "text/plain"})
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=body,
        )

    result = _fetcher(handler).check(
        source_type="single_page",
        root_domain="example.com",
        start_url="https://example.com/official.pdf",
        retention_policy="minimal_excerpt",
        conditional_state={},
    )

    document = result.documents[0]
    assert document.title == "Official filing"
    assert "completed financing" in (document.excerpt or "")
    assert document.published_at is not None
    assert document.metadata["document_format"] == "pdf"
    assert document.metadata["page_count"] == 1
    assert document.metadata["published_at_basis"] == "single_explicit_document_date"


def test_pdf_page_limit_fails_before_text_extraction() -> None:
    body = _pdf_bytes("Official filing", pages=2)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, headers={"content-type": "text/plain"})
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=body,
        )

    fetcher = _fetcher(handler, policy=_policy(max_pdf_pages=1))
    with pytest.raises(SourceFetchError) as caught:
        fetcher.check(
            source_type="single_page",
            root_domain="example.com",
            start_url="https://example.com/too-many-pages.pdf",
            retention_policy="minimal_excerpt",
            conditional_state={},
        )
    assert caught.value.code == "pdf_page_limit_exceeded"


def test_pdf_with_multiple_dates_does_not_guess_publication_date() -> None:
    body = _pdf_bytes("Period 2025-01-01 to 2026-09-04.")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, headers={"content-type": "text/plain"})
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=body,
        )

    result = _fetcher(handler).check(
        source_type="single_page",
        root_domain="example.com",
        start_url="https://example.com/multiple-dates.pdf",
        retention_policy="minimal_excerpt",
        conditional_state={},
    )
    assert result.documents[0].published_at is None
    assert result.documents[0].metadata["published_at_basis"] is None


def test_root_level_list_prefers_repeated_content_directory_over_navigation() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404, headers={"content-type": "text/plain"})
        if request.url.path == "/newslist.html":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    '<a href="/about">关于</a><a href="/products">产品</a>'
                    '<a href="/ndetail/1.html">新闻一</a>'
                    '<a href="/ndetail/2.html">新闻二</a>'
                ),
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=f"<title>{request.url.path}</title><p>新闻正文</p>",
        )

    result = _fetcher(handler).check(
        source_type="list_page",
        root_domain="example.com",
        start_url="https://example.com/newslist.html",
        retention_policy="metadata_only",
        conditional_state={},
    )

    assert len(result.documents) == 2
    assert "/about" not in requested_paths
    assert "/products" not in requested_paths
    assert requested_paths[-2:] == ["/ndetail/1.html", "/ndetail/2.html"]


def test_explicit_list_path_prefix_excludes_larger_navigation_group() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404, headers={"content-type": "text/plain"})
        if request.url.path == "/NEWS":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    '<a href="/FR/1.html">产品一</a>'
                    '<a href="/FR/2.html">产品二</a>'
                    '<a href="/FR/3.html">产品三</a>'
                    '<a href="/Companynews/10.html">新闻一</a>'
                    '<a href="/Companynews/11.html">新闻二</a>'
                ),
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=f"<title>{request.url.path}</title><p>正文</p>",
        )

    result = _fetcher(handler).check(
        source_type="list_page",
        root_domain="example.com",
        start_url="https://example.com/NEWS",
        list_path_prefix="/Companynews",
        retention_policy="metadata_only",
        conditional_state={},
    )

    assert len(result.documents) == 2
    assert all(not path.startswith("/FR/") for path in requested_paths)
    assert requested_paths[-2:] == ["/Companynews/10.html", "/Companynews/11.html"]


@pytest.mark.parametrize("source_type", ["rss", "sitemap", "list_page"])
def test_structured_source_types_discover_only_allowed_pages(source_type: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                text="User-agent: *\nAllow: /\n",
            )
        if source_type == "rss" and request.url.path == "/feed.xml":
            return httpx.Response(
                200,
                headers={"content-type": "application/rss+xml"},
                text=(
                    "<rss><channel><item><title>RSS 事件</title>"
                    "<link>https://example.com/news/rss-1</link>"
                    "<pubDate>Thu, 16 Jul 2026 08:00:00 GMT</pubDate>"
                    "<description>RSS 摘要</description></item></channel></rss>"
                ),
            )
        if source_type == "sitemap" and request.url.path == "/sitemap.xml":
            return httpx.Response(
                200,
                headers={"content-type": "application/xml"},
                text=("<urlset><url><loc>https://example.com/news/map-1</loc></url></urlset>"),
            )
        if source_type == "list_page" and request.url.path == "/news/list":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    '<title>列表</title><a href="/news/list/item-1">栏目文章</a>'
                    '<a href="https://evil.example/trap">外部链接</a>'
                ),
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=f"<title>{request.url.path}</title><p>正文</p>",
        )

    start_urls = {
        "rss": "https://example.com/feed.xml",
        "sitemap": "https://example.com/sitemap.xml",
        "list_page": "https://example.com/news/list",
    }
    fetcher = _fetcher(handler)
    result = fetcher.check(
        source_type=source_type,
        root_domain="example.com",
        start_url=start_urls[source_type],
        retention_policy="minimal_excerpt",
        conditional_state={},
    )
    assert len(result.documents) == 1
    assert result.documents[0].canonical_url.startswith("https://example.com/")
    assert all("evil.example" not in item.canonical_url for item in result.documents)


def test_timeout_is_bounded_and_recorded_as_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    fetcher = _fetcher(handler)
    with pytest.raises(SourceFetchError) as caught:
        fetcher.check(
            source_type="single_page",
            root_domain="example.com",
            start_url="https://example.com/news",
            retention_policy="metadata_only",
            conditional_state={},
        )
    assert caught.value.code == "timeout"
    assert fetcher.request_count == 1
