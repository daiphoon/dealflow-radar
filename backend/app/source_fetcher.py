from __future__ import annotations

import hashlib
import ipaddress
import posixpath
import re
import socket
import time
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx

from backend.app.config import SourceMonitoringPolicy
from backend.app.pdf_extractor import PdfExtractionError, extract_pdf_safely

ALLOWED_DOCUMENT_MIME_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "application/xml",
    "text/xml",
    "application/rss+xml",
    "application/atom+xml",
    "application/pdf",
}
BLOCKED_FILE_SUFFIXES = {
    ".7z",
    ".apk",
    ".bin",
    ".bz2",
    ".dmg",
    ".docm",
    ".exe",
    ".gz",
    ".iso",
    ".jar",
    ".msi",
    ".pkg",
    ".rar",
    ".scr",
    ".tar",
    ".xlsm",
    ".zip",
}
TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_NAMES = {"from", "spm"}
HTML_IGNORED_TAGS = {"script", "style", "noscript", "svg", "template"}
HTML_SUPPRESSED_TAGS = {"aside", "footer", "form", "nav"}
HTML_BLOCK_TAGS = {
    "article",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "main",
    "p",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}
HTML_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta"}
HTML_BOILERPLATE_MARKERS = {
    "breadcrumb",
    "comment",
    "comments",
    "footer",
    "hotlist",
    "menu",
    "nav",
    "navbar",
    "pagination",
    "ranking",
    "recommend",
    "recommended",
    "related",
    "share",
    "sidebar",
    "social",
    "toolbar",
}


class SourceFetchError(Exception):
    def __init__(self, code: str, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class UrlSafetyError(SourceFetchError):
    pass


@dataclass(frozen=True)
class DiscoveredDocument:
    canonical_url: str
    title: str
    published_at: datetime | None
    content_hash: str
    excerpt: str | None
    http_status: int | None
    etag: str | None
    last_modified: str | None
    link_health_status: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class FetchBatchResult:
    documents: list[DiscoveredDocument]
    unchanged_urls: list[str]
    failures: list[dict[str, object]]
    request_log: list[dict[str, object]]
    request_count: int
    downloaded_bytes: int
    robots_status: str
    start_etag: str | None
    start_last_modified: str | None
    start_content_hash: str | None
    partial: bool = False


@dataclass(frozen=True)
class _FetchedResponse:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str | None
    body: bytes
    etag: str | None
    last_modified: str | None


class _HtmlMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.main_text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.canonical_href: str | None = None
        self.published_value: str | None = None
        self._in_title = False
        self._in_head = False
        self._ignored_depth = 0
        self._suppressed_depth = 0
        self._main_depth = 0
        self.found_main_content = False
        self._current_link: str | None = None
        self._current_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        attributes = {key.lower(): value for key, value in attrs if value is not None}
        if lowered in HTML_IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if self._suppressed_depth:
            if lowered not in HTML_VOID_TAGS:
                self._suppressed_depth += 1
            return
        if lowered == "head":
            self._in_head = True
        if lowered == "title":
            self._in_title = True
        if lowered == "link" and "canonical" in attributes.get("rel", "").lower():
            self.canonical_href = attributes.get("href")
        if lowered == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            if key in {
                "article:published_time",
                "date",
                "datepublished",
                "publishdate",
                "pubdate",
            }:
                self.published_value = attributes.get("content") or self.published_value
        if lowered == "time" and attributes.get("datetime"):
            self.published_value = attributes["datetime"]
        if self._is_boilerplate_container(lowered, attributes):
            self._suppressed_depth = 1
            return
        if self._main_depth:
            if lowered not in HTML_VOID_TAGS:
                self._main_depth += 1
        elif lowered in {"article", "main"}:
            self._main_depth = 1
            self.found_main_content = True
        if lowered == "a" and attributes.get("href"):
            self._current_link = attributes["href"]
            self._current_link_text = []
        if lowered in HTML_BLOCK_TAGS:
            self._append_boundary()

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in HTML_IGNORED_TAGS:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if self._suppressed_depth:
            if lowered not in HTML_VOID_TAGS:
                self._suppressed_depth -= 1
            return
        if lowered in HTML_BLOCK_TAGS:
            self._append_boundary()
        if lowered == "title":
            self._in_title = False
        if lowered == "a" and self._current_link is not None:
            self.links.append(
                (self._current_link, _normalize_text(" ".join(self._current_link_text)))
            )
            self._current_link = None
            self._current_link_text = []
        if self._main_depth and lowered not in HTML_VOID_TAGS:
            self._main_depth -= 1
        if lowered == "head":
            self._in_head = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth or self._suppressed_depth:
            return
        normalized = _normalize_text(data)
        if not normalized:
            return
        if self._in_title:
            self.title_parts.append(normalized)
            return
        if self._in_head:
            return
        self.text_parts.append(normalized)
        if self._main_depth:
            self.main_text_parts.append(normalized)
        if self._current_link is not None:
            self._current_link_text.append(normalized)

    def _append_boundary(self) -> None:
        if not self._in_head and self.text_parts and self.text_parts[-1] != "\n":
            self.text_parts.append("\n")
        if self._main_depth and self.main_text_parts and self.main_text_parts[-1] != "\n":
            self.main_text_parts.append("\n")

    def _is_boilerplate_container(
        self,
        tag: str,
        attributes: dict[str, str],
    ) -> bool:
        if tag in HTML_SUPPRESSED_TAGS or (tag == "header" and not self._main_depth):
            return True
        descriptor = " ".join(
            attributes.get(name, "") for name in ("id", "class", "role", "aria-label")
        ).casefold()
        tokens = {token for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", descriptor) if token}
        return bool(tokens & HTML_BOILERPLATE_MARKERS)


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _normalize_document_text(parts: list[str]) -> str:
    value = " ".join(parts)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n+ *", "\n", value)
    return value.strip()


def _sha256(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def normalize_root_domain(value: str) -> str:
    candidate = value.strip().strip(".").lower()
    if not candidate or "/" in candidate or "@" in candidate or ":" in candidate:
        raise UrlSafetyError("invalid_root_domain", "root domain must be a hostname")
    try:
        normalized = candidate.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise UrlSafetyError("invalid_root_domain", "root domain is not valid IDNA") from error
    if normalized == "localhost" or normalized.endswith(".localhost"):
        raise UrlSafetyError("blocked_host", "localhost is not allowed")
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        pass
    else:
        raise UrlSafetyError("blocked_host", "IP literals are not allowed as root domains")
    if len(normalized) > 253 or any(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
        for label in normalized.split(".")
    ):
        raise UrlSafetyError("invalid_root_domain", "root domain is malformed")
    return normalized


def normalize_list_path_prefix(value: str) -> str:
    candidate = value.strip()
    decoded = unquote(candidate)
    if (
        len(candidate) < 2
        or len(candidate) > 500
        or not candidate.startswith("/")
        or candidate.startswith("//")
        or any(character in candidate for character in ("?", "#", "\\"))
        or any(ord(character) < 32 for character in candidate)
        or ".." in decoded
    ):
        raise UrlSafetyError(
            "invalid_list_path_prefix",
            "list path prefix must be a safe absolute URL path",
        )
    return candidate.rstrip("/")


def canonicalize_source_url(
    value: str,
    root_domain: str,
    *,
    allow_test_http: bool = False,
    allow_private_test_hosts: bool = False,
) -> str:
    normalized_root = normalize_root_domain(root_domain)
    parsed = urlsplit(value.strip())
    allowed_schemes = {"https"}
    if allow_test_http:
        allowed_schemes.add("http")
    if parsed.scheme.lower() not in allowed_schemes:
        raise UrlSafetyError("blocked_scheme", "only HTTPS sources are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise UrlSafetyError("userinfo_not_allowed", "URL user information is not allowed")
    if not parsed.hostname:
        raise UrlSafetyError("missing_host", "URL hostname is required")
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise UrlSafetyError("invalid_host", "URL hostname is not valid IDNA") from error
    if not allow_private_test_hosts and (host == "localhost" or host.endswith(".localhost")):
        raise UrlSafetyError("blocked_host", "localhost is not allowed")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not allow_private_test_hosts:
            raise UrlSafetyError("blocked_host", "IP literal URLs are not allowed")
    if host != normalized_root and not host.endswith(f".{normalized_root}"):
        raise UrlSafetyError("domain_not_allowed", "URL is outside the configured root domain")
    try:
        port = parsed.port
    except ValueError as error:
        raise UrlSafetyError("invalid_port", "URL port is invalid") from error
    if not allow_private_test_hosts and port not in {None, 443}:
        raise UrlSafetyError("blocked_port", "only the standard HTTPS port is allowed")
    path = parsed.path or "/"
    lowered_path = path.lower()
    if any(lowered_path.endswith(suffix) for suffix in BLOCKED_FILE_SUFFIXES):
        raise UrlSafetyError("blocked_file_type", "executable or archive URL is not allowed")
    query_items = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_NAMES
        and not key.lower().startswith(TRACKING_QUERY_PREFIXES)
    ]
    query = urlencode(sorted(query_items))
    default_port = port is None or (parsed.scheme.lower() == "https" and port == 443)
    netloc = host if default_port else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))


def _default_resolver(host: str, port: int) -> set[str]:
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise UrlSafetyError("dns_resolution_failed", "hostname could not be resolved") from error
    return {answer[4][0] for answer in answers}


def _is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = value.strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(candidate)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _decode_body(body: bytes, content_type: str | None) -> str:
    charset_match = re.search(r"charset=([\w-]+)", content_type or "", flags=re.IGNORECASE)
    encodings = [charset_match.group(1)] if charset_match else []
    encodings.extend(["utf-8", "gb18030"])
    for encoding in encodings:
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def _html_document(
    response: _FetchedResponse,
    root_domain: str,
    *,
    keep_excerpt: bool,
    allow_test_http: bool,
    allow_private_test_hosts: bool,
) -> tuple[DiscoveredDocument, _HtmlMetadataParser]:
    text = _decode_body(response.body, response.content_type)
    parser = _HtmlMetadataParser()
    parser.feed(text)
    body_text = _normalize_document_text(parser.text_parts)
    main_text = _normalize_document_text(parser.main_text_parts)
    use_main = parser.found_main_content and len(main_text) >= 10
    visible_text = main_text if use_main else body_text
    title = _normalize_text(" ".join(parser.title_parts)) or response.final_url
    canonical_url = response.final_url
    if parser.canonical_href:
        try:
            canonical_url = canonicalize_source_url(
                urljoin(response.final_url, parser.canonical_href),
                root_domain,
                allow_test_http=allow_test_http,
                allow_private_test_hosts=allow_private_test_hosts,
            )
        except UrlSafetyError:
            canonical_url = response.final_url
    hash_input = visible_text or response.body
    document = DiscoveredDocument(
        canonical_url=canonical_url,
        title=title[:500],
        published_at=_parse_datetime(parser.published_value),
        content_hash=_sha256(hash_input),
        excerpt=visible_text[:1500] if keep_excerpt and visible_text else None,
        http_status=response.status_code,
        etag=response.etag,
        last_modified=response.last_modified,
        link_health_status="healthy",
        metadata={
            "content_type": response.content_type or "unknown",
            "extraction_method": "main_content" if use_main else "clean_body",
            "extracted_text_length": len(visible_text),
        },
    )
    return document, parser


def _pdf_published_at(text: str) -> tuple[datetime | None, str | None]:
    matches = {
        tuple(item)
        for item in re.findall(
            r"(20\d{2})[\u5e74\-/.](\d{1,2})[\u6708\-/.](\d{1,2})\u65e5?",
            text[:5_000],
        )
    }
    if len(matches) == 1:
        value = "-".join(part.zfill(2) for part in next(iter(matches)))
        return _parse_datetime(value), "single_explicit_document_date"
    return None, None


def _pdf_document(
    response: _FetchedResponse,
    *,
    keep_excerpt: bool,
    policy: SourceMonitoringPolicy,
) -> DiscoveredDocument:
    try:
        extracted = extract_pdf_safely(
            response.body,
            max_pages=policy.max_pdf_pages,
            max_text_chars=policy.max_pdf_text_chars,
            timeout_seconds=policy.pdf_parse_timeout_seconds,
        )
    except PdfExtractionError as error:
        raise SourceFetchError(error.code, str(error), http_status=response.status_code) from error
    published_at, date_basis = _pdf_published_at(extracted.text)
    path_name = unquote(posixpath.basename(urlsplit(response.final_url).path)).removesuffix(".pdf")
    title = _normalize_text(extracted.title or path_name) or response.final_url
    return DiscoveredDocument(
        canonical_url=response.final_url,
        title=title[:500],
        published_at=published_at,
        content_hash=_sha256(extracted.text),
        excerpt=extracted.text[:1500] if keep_excerpt else None,
        http_status=response.status_code,
        etag=response.etag,
        last_modified=response.last_modified,
        link_health_status="healthy",
        metadata={
            "content_type": "application/pdf",
            "document_format": "pdf",
            "extraction_method": "pypdf_isolated_subprocess",
            "extracted_text_length": len(extracted.text),
            "page_count": extracted.page_count,
            "text_truncated": extracted.truncated,
            "published_at_basis": date_basis,
            "pdf_creation_date": extracted.creation_date,
        },
    )


def _document_from_response(
    response: _FetchedResponse,
    root_domain: str,
    *,
    keep_excerpt: bool,
    policy: SourceMonitoringPolicy,
    allow_test_http: bool,
    allow_private_test_hosts: bool,
) -> tuple[DiscoveredDocument, _HtmlMetadataParser | None]:
    content_type = (response.content_type or "").split(";", 1)[0].strip().lower()
    if content_type == "application/pdf":
        return _pdf_document(response, keep_excerpt=keep_excerpt, policy=policy), None
    return _html_document(
        response,
        root_domain,
        keep_excerpt=keep_excerpt,
        allow_test_http=allow_test_http,
        allow_private_test_hosts=allow_private_test_hosts,
    )


def _safe_xml_root(body: bytes) -> ElementTree.Element:
    lowered = body[:4096].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise SourceFetchError("unsafe_xml", "XML document type declarations are not allowed")
    try:
        return ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise SourceFetchError("invalid_xml", "source XML is malformed") from error


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


class TrustedSourceFetcher:
    def __init__(
        self,
        policy: SourceMonitoringPolicy,
        *,
        client: httpx.Client | None = None,
        resolver: Callable[[str, int], set[str]] = _default_resolver,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        allow_test_http: bool = False,
        allow_private_test_hosts: bool = False,
    ) -> None:
        self.policy = policy
        self.client = client or httpx.Client(
            timeout=policy.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client = client is None
        self.resolver = resolver
        self.sleep = sleep
        self.monotonic = monotonic
        self.allow_test_http = allow_test_http
        self.allow_private_test_hosts = allow_private_test_hosts
        self.request_log: list[dict[str, object]] = []
        self.request_count = 0
        self.downloaded_bytes = 0
        self._last_request_at: dict[str, float] = {}
        self._robots: dict[str, tuple[str, urllib.robotparser.RobotFileParser | None]] = {}

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> TrustedSourceFetcher:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _check_dns(self, url: str) -> set[str]:
        if self.allow_private_test_hosts:
            return set()
        parsed = urlsplit(url)
        host = parsed.hostname
        if host is None:
            raise UrlSafetyError("missing_host", "URL hostname is required")
        addresses = self.resolver(host, parsed.port or 443)
        if not addresses:
            raise UrlSafetyError("dns_resolution_failed", "hostname returned no addresses")
        if any(not _is_public_address(address) for address in addresses):
            raise UrlSafetyError("blocked_network", "hostname resolves to a non-public address")
        return {str(ipaddress.ip_address(address)) for address in addresses}

    def _check_peer_address(self, response: httpx.Response, allowed_addresses: set[str]) -> None:
        if self.allow_private_test_hosts or not self._owns_client:
            return
        stream = response.extensions.get("network_stream")
        peer = stream.get_extra_info("server_addr") if stream is not None else None
        if not isinstance(peer, tuple) or not peer or not isinstance(peer[0], str):
            raise UrlSafetyError(
                "peer_address_unavailable",
                "connected peer address could not be verified",
            )
        peer_address = ipaddress.ip_address(peer[0])
        if not _is_public_address(str(peer_address)):
            raise UrlSafetyError("blocked_network", "connected peer is not a public address")
        if str(peer_address) not in allowed_addresses:
            raise UrlSafetyError(
                "dns_rebinding_detected",
                "connected peer did not match the preflight DNS result",
            )

    def _rate_limit(self, url: str) -> None:
        host = urlsplit(url).hostname or ""
        now = self.monotonic()
        previous = self._last_request_at.get(host)
        interval = self.policy.min_request_interval_ms / 1000
        if previous is not None and now - previous < interval:
            self.sleep(interval - (now - previous))
        self._last_request_at[host] = self.monotonic()

    def _request_once(
        self,
        url: str,
        headers: dict[str, str],
        *,
        allow_plain_text: bool,
    ) -> _FetchedResponse:
        if self.request_count >= self.policy.max_requests_per_run:
            raise SourceFetchError("request_limit_exceeded", "run request limit reached")
        allowed_addresses = self._check_dns(url)
        self._rate_limit(url)
        self.client.cookies.clear()
        request_headers = {
            "User-Agent": self.policy.user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/rss+xml,"
                "application/atom+xml,application/xml,application/pdf,"
                "text/xml;q=0.9,*/*;q=0.1"
            ),
            "Cookie": "",
            **headers,
        }
        started = self.monotonic()
        try:
            request = self.client.build_request("GET", url, headers=request_headers)
            response = self.client.send(request, stream=True, follow_redirects=False)
        except httpx.TimeoutException as error:
            self.request_count += 1
            self.request_log.append(
                {
                    "url": url,
                    "status": None,
                    "bytes": 0,
                    "elapsed_ms": max(0, round((self.monotonic() - started) * 1000)),
                    "content_type": None,
                    "location": None,
                    "error_code": "timeout",
                }
            )
            raise SourceFetchError("timeout", "source request timed out") from error
        except httpx.TransportError as error:
            self.request_count += 1
            self.request_log.append(
                {
                    "url": url,
                    "status": None,
                    "bytes": 0,
                    "elapsed_ms": max(0, round((self.monotonic() - started) * 1000)),
                    "content_type": None,
                    "location": None,
                    "error_code": "network_error",
                }
            )
            raise SourceFetchError("network_error", "source request failed") from error
        self.request_count += 1
        status_code = response.status_code
        content_type_header = response.headers.get("content-type")
        content_type = (
            content_type_header.split(";", 1)[0].strip().lower() if content_type_header else None
        )
        body = b""
        try:
            self._check_peer_address(response, allowed_addresses)
            is_redirect = status_code in {301, 302, 303, 307, 308}
            if status_code not in {304} and not is_redirect and status_code < 400:
                allowed_types = set(ALLOWED_DOCUMENT_MIME_TYPES)
                if allow_plain_text:
                    allowed_types.add("text/plain")
                if content_type not in allowed_types:
                    raise SourceFetchError(
                        "unsupported_content_type",
                        f"unsupported content type: {content_type or 'missing'}",
                        http_status=status_code,
                    )
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        declared_length = int(content_length)
                    except ValueError:
                        declared_length = 0
                    if declared_length > self.policy.max_response_bytes:
                        raise SourceFetchError(
                            "response_too_large",
                            "response exceeds per-page byte limit",
                            http_status=status_code,
                        )
                    if (
                        self.downloaded_bytes + declared_length
                        > self.policy.max_download_bytes_per_run
                    ):
                        raise SourceFetchError(
                            "run_byte_limit_exceeded",
                            "response exceeds run byte limit",
                            http_status=status_code,
                        )
                chunks: list[bytes] = []
                response_bytes = 0
                for chunk in response.iter_bytes():
                    response_bytes += len(chunk)
                    if response_bytes > self.policy.max_response_bytes:
                        raise SourceFetchError(
                            "response_too_large",
                            "response exceeds per-page byte limit",
                            http_status=status_code,
                        )
                    if (
                        self.downloaded_bytes + response_bytes
                        > self.policy.max_download_bytes_per_run
                    ):
                        raise SourceFetchError(
                            "run_byte_limit_exceeded",
                            "response exceeds run byte limit",
                            http_status=status_code,
                        )
                    chunks.append(chunk)
                body = b"".join(chunks)
                self.downloaded_bytes += len(body)
            self._check_dns(url)
            elapsed_ms = max(0, round((self.monotonic() - started) * 1000))
            self.request_log.append(
                {
                    "url": url,
                    "status": status_code,
                    "bytes": len(body),
                    "elapsed_ms": elapsed_ms,
                    "content_type": content_type,
                    "location": response.headers.get("location"),
                }
            )
            return _FetchedResponse(
                requested_url=url,
                final_url=str(response.url),
                status_code=status_code,
                content_type=content_type_header,
                body=body,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )
        except SourceFetchError as error:
            self.request_log.append(
                {
                    "url": url,
                    "status": status_code,
                    "bytes": len(body),
                    "elapsed_ms": max(0, round((self.monotonic() - started) * 1000)),
                    "content_type": content_type,
                    "location": response.headers.get("location"),
                    "error_code": error.code,
                }
            )
            raise
        finally:
            response.close()
            self.client.cookies.clear()

    def _request_with_retries(
        self,
        url: str,
        headers: dict[str, str],
        *,
        allow_plain_text: bool,
    ) -> _FetchedResponse:
        attempt = 0
        while True:
            try:
                response = self._request_once(
                    url,
                    headers,
                    allow_plain_text=allow_plain_text,
                )
            except SourceFetchError as error:
                if (
                    error.code not in {"timeout", "network_error"}
                    or attempt >= self.policy.retry_limit
                ):
                    raise
                attempt += 1
                continue
            if response.status_code < 500 or attempt >= self.policy.retry_limit:
                return response
            attempt += 1

    def _fetch_following_redirects(
        self,
        url: str,
        root_domain: str,
        *,
        headers: dict[str, str] | None = None,
        allow_plain_text: bool = False,
    ) -> _FetchedResponse:
        current = canonicalize_source_url(
            url,
            root_domain,
            allow_test_http=self.allow_test_http,
            allow_private_test_hosts=self.allow_private_test_hosts,
        )
        redirects = 0
        while True:
            response = self._request_with_retries(
                current,
                headers or {},
                allow_plain_text=allow_plain_text,
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = next(
                (
                    entry.get("location")
                    for entry in reversed(self.request_log)
                    if entry.get("url") == current
                ),
                None,
            )
            if not isinstance(location, str) or not location.strip():
                raise SourceFetchError("invalid_redirect", "redirect location is missing")
            redirects += 1
            if redirects > self.policy.max_redirects:
                raise SourceFetchError("redirect_limit_exceeded", "redirect limit exceeded")
            current = canonicalize_source_url(
                urljoin(current, location),
                root_domain,
                allow_test_http=self.allow_test_http,
                allow_private_test_hosts=self.allow_private_test_hosts,
            )

    def _robots_check(self, target_url: str, root_domain: str) -> str:
        parsed = urlsplit(target_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        cached = self._robots.get(origin)
        if cached is None:
            robots_url = f"{origin}/robots.txt"
            response = self._fetch_following_redirects(
                robots_url,
                root_domain,
                allow_plain_text=True,
            )
            if response.status_code == 404:
                cached = ("not_found_allow", None)
            elif response.status_code in {401, 403}:
                cached = ("denied", None)
            elif response.status_code >= 400:
                cached = ("unavailable_deny", None)
            elif (response.content_type or "").split(";", 1)[0].strip().lower() != "text/plain":
                cached = ("invalid_content_type_deny", None)
            else:
                parser = urllib.robotparser.RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(_decode_body(response.body, response.content_type).splitlines())
                cached = ("checked", parser)
            self._robots[origin] = cached
        status, parser = cached
        allowed = status == "not_found_allow" or (
            status == "checked"
            and parser is not None
            and parser.can_fetch(self.policy.user_agent, target_url)
        )
        if not allowed:
            raise SourceFetchError("robots_disallowed", "robots.txt does not allow this request")
        return status

    def _fetch_document_url(
        self,
        url: str,
        root_domain: str,
        conditional: dict[str, str] | None = None,
    ) -> tuple[_FetchedResponse, str]:
        canonical = canonicalize_source_url(
            url,
            root_domain,
            allow_test_http=self.allow_test_http,
            allow_private_test_hosts=self.allow_private_test_hosts,
        )
        robots_status = self._robots_check(canonical, root_domain)
        response = self._fetch_following_redirects(
            canonical,
            root_domain,
            headers=conditional,
        )
        return response, robots_status

    @staticmethod
    def _conditional_headers(state: dict[str, str | None] | None) -> dict[str, str]:
        if not state:
            return {}
        headers: dict[str, str] = {}
        if state.get("etag"):
            headers["If-None-Match"] = str(state["etag"])
        if state.get("last_modified"):
            headers["If-Modified-Since"] = str(state["last_modified"])
        return headers

    def _response_or_error(self, response: _FetchedResponse) -> None:
        if response.status_code == 404:
            raise SourceFetchError("http_not_found", "source returned HTTP 404", http_status=404)
        if response.status_code >= 400:
            raise SourceFetchError(
                "http_error",
                f"source returned HTTP {response.status_code}",
                http_status=response.status_code,
            )

    def check(
        self,
        *,
        source_type: str,
        root_domain: str,
        start_url: str,
        list_path_prefix: str | None = None,
        retention_policy: str,
        conditional_state: dict[str, dict[str, str | None]],
    ) -> FetchBatchResult:
        keep_excerpt = retention_policy == "minimal_excerpt"
        documents: list[DiscoveredDocument] = []
        unchanged_urls: list[str] = []
        failures: list[dict[str, object]] = []
        partial = False
        canonical_start = canonicalize_source_url(
            start_url,
            root_domain,
            allow_test_http=self.allow_test_http,
            allow_private_test_hosts=self.allow_private_test_hosts,
        )
        response, robots_status = self._fetch_document_url(
            canonical_start,
            root_domain,
            self._conditional_headers(conditional_state.get(canonical_start)),
        )
        if response.status_code == 304:
            unchanged_urls.append(canonical_start)
            return FetchBatchResult(
                documents=[],
                unchanged_urls=unchanged_urls,
                failures=[],
                request_log=self.request_log,
                request_count=self.request_count,
                downloaded_bytes=self.downloaded_bytes,
                robots_status=robots_status,
                start_etag=response.etag or conditional_state.get(canonical_start, {}).get("etag"),
                start_last_modified=(
                    response.last_modified
                    or conditional_state.get(canonical_start, {}).get("last_modified")
                ),
                start_content_hash=conditional_state.get(canonical_start, {}).get("content_hash"),
            )
        self._response_or_error(response)
        start_hash = _sha256(response.body)

        if source_type == "single_page":
            document, _ = _document_from_response(
                response,
                root_domain,
                keep_excerpt=keep_excerpt,
                policy=self.policy,
                allow_test_http=self.allow_test_http,
                allow_private_test_hosts=self.allow_private_test_hosts,
            )
            documents.append(document)
        elif source_type == "list_page":
            if (response.content_type or "").split(";", 1)[0].strip().lower() == "application/pdf":
                raise SourceFetchError(
                    "unexpected_pdf_source_type",
                    "a list source must be an HTML document",
                )
            _, parser = _html_document(
                response,
                root_domain,
                keep_excerpt=False,
                allow_test_http=self.allow_test_http,
                allow_private_test_hosts=self.allow_private_test_hosts,
            )
            start_path = urlsplit(canonical_start).path.rstrip("/")
            path_prefix = (
                normalize_list_path_prefix(list_path_prefix)
                if list_path_prefix is not None
                else start_path.rsplit("/", 1)[0] or "/"
            )
            safe_links: list[str] = []
            for raw_href, _ in parser.links:
                try:
                    link = canonicalize_source_url(
                        urljoin(response.final_url, raw_href),
                        root_domain,
                        allow_test_http=self.allow_test_http,
                        allow_private_test_hosts=self.allow_private_test_hosts,
                    )
                except UrlSafetyError:
                    continue
                if link == canonical_start:
                    continue
                if link not in safe_links:
                    safe_links.append(link)
            if list_path_prefix is not None:
                links = [
                    link for link in safe_links if urlsplit(link).path.startswith(f"{path_prefix}/")
                ]
            elif path_prefix == "/":
                link_groups: dict[str, list[str]] = {}
                for link in safe_links:
                    parent = posixpath.dirname(urlsplit(link).path.rstrip("/")) or "/"
                    if parent != "/":
                        link_groups.setdefault(parent, []).append(link)
                repeated_groups = {
                    parent: grouped_links
                    for parent, grouped_links in link_groups.items()
                    if len(grouped_links) >= 2
                }
                links = (
                    max(
                        repeated_groups.items(),
                        key=lambda item: (len(item[1]), item[0].count("/")),
                    )[1]
                    if repeated_groups
                    else []
                )
            else:
                links = [
                    link
                    for link in safe_links
                    if urlsplit(link).path == path_prefix
                    or urlsplit(link).path.startswith(f"{path_prefix}/")
                ]
            for link in links:
                try:
                    child, _ = self._fetch_document_url(
                        link,
                        root_domain,
                        self._conditional_headers(conditional_state.get(link)),
                    )
                    if child.status_code == 304:
                        unchanged_urls.append(link)
                        continue
                    self._response_or_error(child)
                    document, _ = _document_from_response(
                        child,
                        root_domain,
                        keep_excerpt=keep_excerpt,
                        policy=self.policy,
                        allow_test_http=self.allow_test_http,
                        allow_private_test_hosts=self.allow_private_test_hosts,
                    )
                    documents.append(document)
                except SourceFetchError as error:
                    if error.code == "request_limit_exceeded":
                        partial = True
                        break
                    failures.append(
                        {"url": link, "code": error.code, "http_status": error.http_status}
                    )
        elif source_type == "rss":
            root = _safe_xml_root(response.body)
            for entry in root.iter():
                if _local_name(entry.tag) not in {"item", "entry"}:
                    continue
                values: dict[str, str] = {}
                for child in list(entry):
                    name = _local_name(child.tag)
                    if name == "link":
                        values[name] = child.attrib.get("href") or (child.text or "")
                    elif name in {
                        "title",
                        "pubdate",
                        "published",
                        "updated",
                        "description",
                        "summary",
                    }:
                        values[name] = child.text or ""
                raw_link = values.get("link", "").strip()
                if not raw_link:
                    continue
                try:
                    link = canonicalize_source_url(
                        urljoin(response.final_url, raw_link),
                        root_domain,
                        allow_test_http=self.allow_test_http,
                        allow_private_test_hosts=self.allow_private_test_hosts,
                    )
                except UrlSafetyError as error:
                    failures.append({"url": raw_link, "code": error.code, "http_status": None})
                    continue
                title = _normalize_text(values.get("title", "")) or link
                summary = _normalize_text(values.get("description") or values.get("summary") or "")
                published = _parse_datetime(
                    values.get("pubdate") or values.get("published") or values.get("updated")
                )
                hash_payload = "|".join(
                    [link, title, published.isoformat() if published else "", summary]
                )
                documents.append(
                    DiscoveredDocument(
                        canonical_url=link,
                        title=title[:500],
                        published_at=published,
                        content_hash=_sha256(hash_payload),
                        excerpt=summary[:500] if keep_excerpt and summary else None,
                        http_status=None,
                        etag=None,
                        last_modified=None,
                        link_health_status="unchecked",
                        metadata={"discovered_via": "feed"},
                    )
                )
        elif source_type == "sitemap":
            root = _safe_xml_root(response.body)
            links = [
                _normalize_text(element.text or "")
                for element in root.iter()
                if _local_name(element.tag) == "loc" and _normalize_text(element.text or "")
            ]
            for raw_link in links:
                try:
                    link = canonicalize_source_url(
                        raw_link,
                        root_domain,
                        allow_test_http=self.allow_test_http,
                        allow_private_test_hosts=self.allow_private_test_hosts,
                    )
                    child, _ = self._fetch_document_url(
                        link,
                        root_domain,
                        self._conditional_headers(conditional_state.get(link)),
                    )
                    if child.status_code == 304:
                        unchanged_urls.append(link)
                        continue
                    self._response_or_error(child)
                    document, _ = _document_from_response(
                        child,
                        root_domain,
                        keep_excerpt=keep_excerpt,
                        policy=self.policy,
                        allow_test_http=self.allow_test_http,
                        allow_private_test_hosts=self.allow_private_test_hosts,
                    )
                    documents.append(document)
                except SourceFetchError as error:
                    if error.code == "request_limit_exceeded":
                        partial = True
                        break
                    failures.append(
                        {"url": raw_link, "code": error.code, "http_status": error.http_status}
                    )
        else:
            raise SourceFetchError("unsupported_source_type", "unsupported source type")

        return FetchBatchResult(
            documents=documents,
            unchanged_urls=unchanged_urls,
            failures=failures,
            request_log=self.request_log,
            request_count=self.request_count,
            downloaded_bytes=self.downloaded_bytes,
            robots_status=robots_status,
            start_etag=response.etag,
            start_last_modified=response.last_modified,
            start_content_hash=start_hash,
            partial=partial,
        )
