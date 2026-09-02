from __future__ import annotations

import json

import httpx
import pytest

from backend.app.web_search import (
    BAIDU_WEB_SEARCH_ENDPOINT,
    BOCHA_WEB_SEARCH_ENDPOINT,
    BaiduSearchProvider,
    BochaSearchProvider,
    SearchProviderError,
    SearchRequest,
)


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_baidu_provider_maps_documented_shape_and_drops_unsafe_urls() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "request_id": "baidu-request-1",
                "references": [
                    {
                        "id": "result-1",
                        "title": "示例公司完成新一轮融资",
                        "url": "https://news.example.com/financing?utm_source=test",
                        "content": "示例公司宣布完成融资。",
                        "website": "示例财经网",
                        "date": "2026-09-01",
                    },
                    {
                        "id": "unsafe",
                        "title": "不安全结果",
                        "url": "http://127.0.0.1/private",
                        "content": "不得进入候选。",
                    },
                ],
            },
        )

    provider = BaiduSearchProvider(
        "test-key",
        timeout_seconds=2,
        max_response_bytes=4096,
        user_agent="DealflowRadarTest/1.0",
        client=_client(handler),
    )
    response = provider.search(SearchRequest("示例公司 融资", max_results=5))

    assert response.provider_code == "baidu"
    assert response.request_id == "baidu-request-1"
    assert len(response.results) == 1
    assert response.results[0].source_name == "示例财经网"
    assert requests[0].url == BAIDU_WEB_SEARCH_ENDPOINT
    assert requests[0].headers["authorization"] == "Bearer test-key"
    assert json.loads(requests[0].read()) == {
        "messages": [{"role": "user", "content": "示例公司 融资"}],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [{"type": "web", "top_k": 5}],
    }


def test_bocha_provider_maps_documented_shape_without_requesting_summary() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "webPages": {
                        "value": [
                            {
                                "id": "bocha-result-1",
                                "name": "示例公司中标公告",
                                "url": "https://notice.example.cn/result/1",
                                "snippet": "示例公司成为中标人。",
                                "datePublished": "2026-08-31",
                                "siteName": "示例公告网",
                            }
                        ]
                    }
                }
            },
        )

    provider = BochaSearchProvider(
        "test-key",
        timeout_seconds=2,
        max_response_bytes=4096,
        user_agent="DealflowRadarTest/1.0",
        client=_client(handler),
    )
    response = provider.search(SearchRequest("示例公司 中标", max_results=3))

    assert response.provider_code == "bocha"
    assert response.results[0].title == "示例公司中标公告"
    assert requests[0].url == BOCHA_WEB_SEARCH_ENDPOINT
    assert json.loads(requests[0].read()) == {
        "query": "示例公司 中标",
        "count": 3,
        "summary": False,
    }


@pytest.mark.parametrize("status,code", [(429, "rate_limited"), (503, "http_error")])
def test_provider_returns_auditable_error_without_retry(status: int, code: str) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": "provider error"})

    provider = BaiduSearchProvider(
        "test-key",
        timeout_seconds=2,
        max_response_bytes=4096,
        user_agent="DealflowRadarTest/1.0",
        client=_client(handler),
    )
    with pytest.raises(SearchProviderError) as caught:
        provider.search(SearchRequest("示例公司"))

    assert caught.value.code == code
    assert caught.value.external_calls == 1
    assert calls == 1


def test_provider_rejects_oversized_or_redirected_response() -> None:
    oversized = BaiduSearchProvider(
        "test-key",
        timeout_seconds=2,
        max_response_bytes=32,
        user_agent="DealflowRadarTest/1.0",
        client=_client(lambda _: httpx.Response(200, content=b"x" * 33)),
    )
    with pytest.raises(SearchProviderError, match="configured limit") as caught:
        oversized.search(SearchRequest("示例公司"))
    assert caught.value.code == "response_too_large"

    redirecting = BochaSearchProvider(
        "test-key",
        timeout_seconds=2,
        max_response_bytes=4096,
        user_agent="DealflowRadarTest/1.0",
        client=_client(lambda _: httpx.Response(302, headers={"location": "https://evil.example"})),
    )
    with pytest.raises(SearchProviderError) as redirected:
        redirecting.search(SearchRequest("示例公司"))
    assert redirected.value.code == "http_error"
