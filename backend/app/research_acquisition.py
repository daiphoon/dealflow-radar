"""专业获取适配器。来源准入由配置限定；不把搜索摘要当正文。"""

import hashlib
import ipaddress
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

from backend.app.research_matters import clean_body
from backend.app.source_fetcher import DiscoveredDocument, FetchBatchResult, SourceFetchError
from backend.app.web_search import SearchResult, _JsonSearchProvider


def allowed_url(url, domains):
    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower().rstrip(".")
        if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
            return False
        if not host or "." not in host or host.endswith((".local", ".internal", ".localhost")):
            return False
        try:
            if not ipaddress.ip_address(host).is_global:
                return False
        except ValueError:
            pass
        return any(host == d or host.endswith("." + d) for d in domains)
    except ValueError:
        return False


def source_date(body, provider_date=None):
    """只认发布标记/头部完整年月日；不按 URL 或当天时间补造日期。"""
    candidates = []
    for match in re.finditer(
        r"(?:^|\n)[^\n]{0,35}?(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})日?"
        r"(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?[^\n]{0,20}(?=\n|$)",
        body[:1800],
    ):
        line = match.group()
        if re.search(
            r"(?:完成|签署|计划|成立|协议|今年|IPO|投资|融资|备案|聆讯|上市|认缴|中标|投产)", line
        ) and not re.search(r"发布|来源|作者|编辑", line):
            continue
        try:
            parts = [int(v or 0) for v in match.groups()]
            candidates.append((datetime(*parts, tzinfo=timezone(timedelta(hours=8))), line.strip()))
        except ValueError:
            continue
    unique = {v.date() for v, _ in candidates}
    if len(unique) == 1:
        return candidates[0][0], {"basis": "source_publication_line", "quote": candidates[0][1]}
    if len(unique) > 1:
        return None, {"basis": "conflicting_source_dates"}
    # 索引日期只保留为旁证，不赋给来源发布时间。
    return None, {"basis": "unknown", "index_date": provider_date}


def acquired_document(url, title, body, provider, provider_date=None):
    published, date_basis = source_date(body, provider_date)
    cleaned = clean_body(body)
    if not cleaned:
        raise SourceFetchError("no_fetchable_document", "provider did not return source text")
    return DiscoveredDocument(
        canonical_url=url,
        title=title[:500],
        published_at=published,
        content_hash=hashlib.sha256(cleaned.encode()).hexdigest(),
        excerpt=cleaned[:6000],
        http_status=None,
        etag=None,
        last_modified=None,
        link_health_status="healthy",
        metadata={
            "acquisition_provider": provider,
            "extraction_method": "provider_source_text",
            "publication_date": date_basis,
            "origin_http_status_verified": False,
            "retained_characters": min(6000, len(cleaned)),
            "body_characters": len(cleaned),
        },
    )


class TavilySearchProvider(_JsonSearchProvider):
    code = "tavily"
    endpoint = "https://api.tavily.com/search"

    def _request_payload(self, request):
        return {
            "query": request.query,
            "max_results": request.max_results,
            "search_depth": "advanced",
            "topic": "general",
            "country": "china",
            "include_answer": False,
            "auto_parameters": False,
            "include_raw_content": "text",
            "include_published_date": True,
            **({"time_range": "year"} if request.recent_only else {}),
        }

    def _parse_results(self, payload, limit):
        results = []
        for item in payload.get("results", []):
            if not isinstance(item, dict):
                continue
            try:
                results.append(
                    SearchResult.from_dict(
                        {
                            "title": item.get("title"),
                            "url": item.get("url"),
                            "snippet": item.get("content"),
                            "source_name": None,
                            "published_at": item.get("published_date"),
                            "provider_record_id": item.get("id"),
                            "source_body": item.get("raw_content"),
                            "body_provider": self.code,
                        }
                    )
                )
            except ValueError:
                continue
        return results[:limit]


class TavilyDocumentProvider:
    code = "tavily"

    def __init__(self, api_key, policy, *, client=None):
        if not api_key.strip():
            raise ValueError("Tavily API key required")
        self._key = api_key
        self.policy = policy
        self._client = client or httpx.Client(
            timeout=policy.timeout_seconds, follow_redirects=False
        )
        self._owned = client is None

    def close(self):
        if self._owned:
            self._client.close()

    def extract(self, url, title, max_bytes):
        if not allowed_url(url, self.policy.tavily_allowed_domains):
            raise SourceFetchError(
                "source_not_allowed", "source not enabled for provider acquisition"
            )
        data = bytearray()
        self.request_count = 1
        self.downloaded_bytes = 0
        try:
            with self._client.stream(
                "POST",
                "https://api.tavily.com/extract",
                headers={"Authorization": "Bearer " + self._key},
                json={
                    "urls": [url],
                    "extract_depth": "advanced",
                    "format": "text",
                    "include_usage": True,
                },
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise SourceFetchError(
                        "provider_http_error",
                        "Tavily extract failed",
                        http_status=response.status_code,
                    )
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    self.downloaded_bytes = len(data)
                    if len(data) > min(max_bytes, self.policy.max_response_bytes):
                        raise SourceFetchError(
                            "response_byte_limit", "provider body exceeds budget"
                        )
            payload = json.loads(data)
            matches = [
                r
                for r in payload.get("results", [])
                if r.get("url") == url and isinstance(r.get("raw_content"), str)
            ]
            if not matches:
                raise SourceFetchError("no_fetchable_document", "no requested source body")
            item = matches[0]
            return acquired_document(
                url, item.get("title") or title, item["raw_content"], self.code
            ), len(data)
        except httpx.HTTPError as error:
            raise SourceFetchError("provider_transport_error", "provider request failed") from error
        except (ValueError, TypeError, AttributeError) as error:
            raise SourceFetchError(
                "invalid_provider_response", "provider response malformed"
            ) from error


class ProviderDocumentFetcher:
    """复用 Worker 原有落库和失败记账；缓存正文不再次请求。"""

    def __init__(self, provider, url, title, max_bytes, cached=None, cached_at=None):
        self.provider, self.url, self.title = provider, url, title
        self.max_bytes, self.cached = max_bytes, cached
        self.cached_at = cached_at
        self.request_count = self.downloaded_bytes = 0
        self.guard = lambda: True

    def bind_job_robots_cache(self, cache):
        pass

    def bind_request_guard(self, guard):
        self.guard = guard

    def close(self):
        pass

    def check(self, **kwargs):
        self.guard()
        if self.cached is not None:
            if len(self.cached.source_body.encode()) > self.max_bytes:
                raise SourceFetchError("response_byte_limit", "cached source exceeds budget")
            document = acquired_document(
                self.url,
                self.cached.title,
                self.cached.source_body,
                "tavily_search_cache",
                self.cached.published_at,
            )
        else:
            try:
                document, size = self.provider.extract(self.url, self.title, self.max_bytes)
                self.downloaded_bytes = size
                self.request_count = 1
            except SourceFetchError:
                self.request_count = getattr(self.provider, "request_count", 1)
                self.downloaded_bytes = getattr(self.provider, "downloaded_bytes", 0)
                raise
        return FetchBatchResult(
            [document],
            [],
            [],
            [],
            self.request_count,
            self.downloaded_bytes,
            "provider_acquisition",
            None,
            None,
            document.content_hash,
        )
