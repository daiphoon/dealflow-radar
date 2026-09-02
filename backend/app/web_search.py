from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

BAIDU_WEB_SEARCH_ENDPOINT = "https://qianfan.baidubce.com/v2/ai_search/web_search"
BOCHA_WEB_SEARCH_ENDPOINT = "https://api.bochaai.com/v1/web-search"


class SearchProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, external_calls: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.external_calls = external_calls


@dataclass(frozen=True)
class SearchRequest:
    query: str
    max_results: int = 10

    def __post_init__(self) -> None:
        if not self.query.strip() or len(self.query) > 500:
            raise ValueError("search query must contain 1 to 500 characters")
        if not 1 <= self.max_results <= 20:
            raise ValueError("max_results must be between 1 and 20")


@dataclass(frozen=True)
class SearchResult:
    provider_record_id: str | None
    title: str
    url: str
    snippet: str
    source_name: str | None
    published_at: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "provider_record_id": self.provider_record_id,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source_name": self.source_name,
            "published_at": self.published_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> SearchResult:
        url = _safe_https_url(value.get("url"))
        if url is None:
            raise ValueError("cached search result URL is invalid")
        return cls(
            provider_record_id=_optional_text(value.get("provider_record_id"), 200),
            title=_required_text(value.get("title"), 500),
            url=url,
            snippet=_optional_text(value.get("snippet"), 2_000) or "",
            source_name=_optional_text(value.get("source_name"), 200),
            published_at=_optional_text(value.get("published_at"), 80),
        )


@dataclass(frozen=True)
class SearchResponse:
    provider_code: str
    request_id: str | None
    results: list[SearchResult]
    response_hash: str
    external_calls: int = 1


class SearchProvider(Protocol):
    code: str

    def search(self, request: SearchRequest) -> SearchResponse: ...


def _optional_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized[:limit] if normalized else None


def _required_text(value: object, limit: int) -> str:
    return _optional_text(value, limit) or "未提供标题"


def _safe_https_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2_000:
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return value.strip()


def _response_hash(provider_code: str, results: list[SearchResult]) -> str:
    payload = json.dumps(
        [result.to_dict() for result in results],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(f"{provider_code}:{payload}".encode()).hexdigest()


class _JsonSearchProvider:
    code: str
    endpoint: str

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: int,
        max_response_bytes: int,
        user_agent: str,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError(f"{self.code} API key is required")
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("search provider limits must be positive")
        self._api_key = api_key.strip()
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._user_agent = user_agent
        self._client = client

    def _request_payload(self, request: SearchRequest) -> dict[str, object]:
        raise NotImplementedError

    def _parse_results(self, payload: dict[str, Any], limit: int) -> list[SearchResult]:
        raise NotImplementedError

    def _request_id(self, payload: dict[str, Any]) -> str | None:
        return _optional_text(payload.get("request_id"), 200)

    def search(self, request: SearchRequest) -> SearchResponse:
        owned_client = self._client is None
        client = self._client or httpx.Client(
            timeout=self._timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        try:
            try:
                with client.stream(
                    "POST",
                    self.endpoint,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                        "User-Agent": self._user_agent,
                    },
                    json=self._request_payload(request),
                ) as response:
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > self._max_response_bytes:
                            raise SearchProviderError(
                                "response_too_large",
                                f"{self.code} response exceeded the configured limit",
                                external_calls=1,
                            )
                    status_code = response.status_code
            except SearchProviderError:
                raise
            except httpx.TimeoutException as error:
                raise SearchProviderError(
                    "timeout",
                    f"{self.code} search timed out",
                    external_calls=1,
                ) from error
            except httpx.HTTPError as error:
                raise SearchProviderError(
                    "network_error",
                    f"{self.code} search failed",
                    external_calls=1,
                ) from error
            if status_code != 200:
                error_code = "rate_limited" if status_code == 429 else "http_error"
                raise SearchProviderError(
                    error_code,
                    f"{self.code} search returned HTTP {status_code}",
                    external_calls=1,
                )
            try:
                payload = json.loads(content) if content else {}
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise SearchProviderError(
                    "invalid_json",
                    f"{self.code} search returned invalid JSON",
                    external_calls=1,
                ) from error
            if not isinstance(payload, dict):
                raise SearchProviderError(
                    "invalid_response",
                    f"{self.code} search returned an invalid response",
                    external_calls=1,
                )
            results = self._parse_results(payload, request.max_results)
            return SearchResponse(
                provider_code=self.code,
                request_id=self._request_id(payload),
                results=results,
                response_hash=_response_hash(self.code, results),
                external_calls=1,
            )
        finally:
            if owned_client:
                client.close()


class BaiduSearchProvider(_JsonSearchProvider):
    code = "baidu"
    endpoint = BAIDU_WEB_SEARCH_ENDPOINT

    def _request_payload(self, request: SearchRequest) -> dict[str, object]:
        return {
            "messages": [{"role": "user", "content": request.query}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": request.max_results}],
        }

    def _parse_results(self, payload: dict[str, Any], limit: int) -> list[SearchResult]:
        references = payload.get("references")
        if not isinstance(references, list):
            return []
        results: list[SearchResult] = []
        for item in references:
            if not isinstance(item, dict):
                continue
            url = _safe_https_url(item.get("url"))
            if url is None:
                continue
            results.append(
                SearchResult(
                    provider_record_id=_optional_text(item.get("id"), 200),
                    title=_required_text(item.get("title"), 500),
                    url=url,
                    snippet=_optional_text(item.get("content") or item.get("snippet"), 2_000) or "",
                    source_name=_optional_text(item.get("website"), 200),
                    published_at=_optional_text(item.get("date"), 80),
                )
            )
            if len(results) >= limit:
                break
        return results


class BochaSearchProvider(_JsonSearchProvider):
    code = "bocha"
    endpoint = BOCHA_WEB_SEARCH_ENDPOINT

    def _request_payload(self, request: SearchRequest) -> dict[str, object]:
        return {"query": request.query, "count": request.max_results, "summary": False}

    def _parse_results(self, payload: dict[str, Any], limit: int) -> list[SearchResult]:
        data = payload.get("data")
        web_pages = data.get("webPages") if isinstance(data, dict) else None
        values = web_pages.get("value") if isinstance(web_pages, dict) else None
        if not isinstance(values, list):
            return []
        results: list[SearchResult] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            url = _safe_https_url(item.get("url"))
            if url is None:
                continue
            results.append(
                SearchResult(
                    provider_record_id=_optional_text(item.get("id"), 200),
                    title=_required_text(item.get("name"), 500),
                    url=url,
                    snippet=_optional_text(item.get("snippet"), 2_000) or "",
                    source_name=_optional_text(item.get("siteName"), 200),
                    published_at=_optional_text(
                        item.get("datePublished") or item.get("dateLastCrawled"),
                        80,
                    ),
                )
            )
            if len(results) >= limit:
                break
        return results


class MockSearchProvider:
    def __init__(
        self,
        code: str,
        responses: dict[str, list[SearchResult]] | None = None,
        *,
        failures: dict[str, str] | None = None,
    ) -> None:
        self.code = code
        self.responses = responses or {}
        self.failures = failures or {}
        self.calls: list[SearchRequest] = []

    def search(self, request: SearchRequest) -> SearchResponse:
        self.calls.append(request)
        if request.query in self.failures:
            raise SearchProviderError(
                self.failures[request.query],
                f"mock {self.code} failure",
                external_calls=1,
            )
        results = list(self.responses.get(request.query, []))[: request.max_results]
        return SearchResponse(
            provider_code=self.code,
            request_id=f"mock-{len(self.calls)}",
            results=results,
            response_hash=_response_hash(self.code, results),
            external_calls=1,
        )
