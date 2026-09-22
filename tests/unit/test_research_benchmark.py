import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from backend.app.source_fetcher import SourceFetchError
from scripts.research_benchmark import (
    Case,
    Manifest,
    digest,
    execute_case,
    model_payload,
    preview,
    public_url,
    run,
    validate_evidence,
)


def manifest(cases=None, **limits):
    return Manifest.model_validate(
        {
            "version": "research-benchmark-v1",
            "source_sha256": "a" * 64,
            "model": "test-model",
            "input_cny_per_million": "2",
            "output_cny_per_million": "8",
            "limits": {
                "tavily_credits": 4,
                "baidu_calls": 0,
                "bocha_calls": 0,
                "direct_http": 0,
                "model_calls": 1,
                "model_cny": "1",
                **limits,
            },
            "cases": cases
            or [
                {
                    "id": "q1",
                    "company_key": "fictional",
                    "provider": "tavily",
                    "operation": "search",
                    "query": "示例科技 融资",
                }
            ],
        }
    )


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_preview_needs_no_credentials_or_network():
    result = preview(manifest())
    assert result["external_calls"] == 0
    assert result["maximum_reserved"] == {"tavily_credits": "2"}


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com",
        "https://127.0.0.1/x",
        "https://localhost/x",
        "https://a.internal/x",
        "https://u:p@example.com",
        "https://example.com:444",
    ],
)
def test_reject_private_and_nonstandard_source_urls(value):
    with pytest.raises(ValueError):
        public_url(value)


def test_search_manifest_rejects_answer_body():
    with pytest.raises(ValueError):
        Case(
            id="x",
            company_key="f",
            provider="tavily",
            operation="search",
            query="示例科技 融资",
            text="已知答案",
        )


def test_duplicate_ids_and_insufficient_budget_rejected_before_dispatch():
    original = manifest().model_dump(mode="json")
    original["cases"] *= 2
    with pytest.raises(ValueError, match="duplicate"):
        Manifest.model_validate(original)
    with pytest.raises(ValueError, match="budget"):
        preview(manifest(tavily_credits=1))


@pytest.mark.parametrize("gate,hash_value", [("false", None), ("true", "wrong")])
def test_execution_requires_gate_and_frozen_manifest(tmp_path, gate, hash_value):
    m = manifest()
    with pytest.raises(ValueError):
        run(
            m,
            tmp_path,
            {"TAVILY_API_KEY": "test", "RESEARCH_BENCHMARK_EXTERNAL_CALLS_ENABLED": gate},
            expected_hash=hash_value or digest(m.model_dump(mode="json")),
        )
    assert not list(tmp_path.glob("*.receipt.json"))


def test_resume_does_not_repeat_success_or_unknown_spend(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("contains-secret", request=request)
        return httpx.Response(200, json={"results": []})

    m = manifest()
    env = {"TAVILY_API_KEY": "test", "RESEARCH_BENCHMARK_EXTERNAL_CALLS_ENABLED": "true"}
    for _ in range(2):
        result = run(
            m,
            tmp_path,
            env,
            expected_hash=digest(m.model_dump(mode="json")),
            client=client(handler),
        )
    assert len(calls) == 1
    assert result["statuses"] == {"failed_or_spend_unknown": 1}
    assert result["reserved"] == {"tavily_credits": "2"}
    assert "contains-secret" not in (tmp_path / "q1.receipt.json").read_text()


def test_cannot_reuse_output_directory_with_changed_manifest(tmp_path):
    m = manifest()
    env = {"TAVILY_API_KEY": "test", "RESEARCH_BENCHMARK_EXTERNAL_CALLS_ENABLED": "true"}
    transport = client(lambda r: httpx.Response(200, json={"results": []}))
    run(m, tmp_path, env, expected_hash=digest(m.model_dump(mode="json")), client=transport)
    m.max_results = 3
    with pytest.raises(ValueError, match="different manifest"):
        run(m, tmp_path, env, expected_hash=digest(m.model_dump(mode="json")), client=transport)


def test_tavily_contract_keeps_body_separate_from_answer_and_snippet():
    requests = []

    def handler(request):
        requests.append(json.loads(request.read()))
        return httpx.Response(
            200,
            json={
                "answer": "不得保存的生成答案",
                "usage": {"credits": 2},
                "results": [
                    {
                        "url": "https://example.com/news",
                        "content": "摘要",
                        "raw_content": "原文内容",
                        "title": "新闻",
                    }
                ],
            },
        )

    m = manifest()
    result = execute_case(m, m.cases[0], {"TAVILY_API_KEY": "test"}, client(handler))
    assert requests[0]["include_answer"] is False
    assert requests[0]["auto_parameters"] is False
    assert result["results"][0]["excerpt"] == "原文内容"
    assert result["results"][0]["snippet"] == "摘要"
    assert "不得保存" not in json.dumps(result, ensure_ascii=False)


def test_oversized_provider_response_fails_closed():
    m = manifest()
    with pytest.raises(RuntimeError, match="byte limit"):
        execute_case(
            m,
            m.cases[0],
            {"TAVILY_API_KEY": "test"},
            client(lambda r: httpx.Response(200, content=b"x" * 2_000_001)),
        )


def test_unsafe_result_does_not_discard_other_valid_tavily_results():
    m = manifest()
    result = execute_case(
        m,
        m.cases[0],
        {"TAVILY_API_KEY": "test"},
        client(
            lambda r: httpx.Response(
                200,
                json={
                    "results": [
                        {"url": "http://example.com/a", "raw_content": "不接收"},
                        {"url": "https://example.com/b", "raw_content": "有效正文"},
                    ]
                },
            )
        ),
    )
    assert result["rejected_result_count"] == 1
    assert result["results"][0]["excerpt"] == "有效正文"


def model_case():
    return Case(
        id="doc1",
        company_key="f",
        provider="deepseek",
        operation="understand",
        names=["示例科技"],
        text="示例科技宣布计划上市，认缴500万元设立基金。",
    )


def output():
    return {
        "events": [
            {
                "subject": "示例科技",
                "subject_quote": "示例科技宣布计划上市",
                "subtype": "fund_commitment",
                "action_quote": "认缴500万元设立基金",
                "amount": {"value": "500万元", "quote": "认缴500万元"},
            }
        ]
    }


def test_exact_quotes_and_unknown_date_retained():
    result = validate_evidence(output(), model_case())
    assert result["events"][0]["event_date"] is None


@pytest.mark.parametrize(
    "field,value", [("subject", "别家公司"), ("action_quote", "已实缴500万元")]
)
def test_hallucinated_subject_or_action_rejected(field, value):
    data = output()
    data["events"][0][field] = value
    with pytest.raises(ValueError):
        validate_evidence(data, model_case())


def test_unsupported_field_value_rejected_even_if_quote_real():
    data = output()
    data["events"][0]["amount"]["value"] = "600万元"
    with pytest.raises(ValueError):
        validate_evidence(data, model_case())


def test_model_payload_contains_no_tools_and_caps_output():
    m = manifest()
    payload = model_payload(m, model_case())
    assert "tools" not in payload
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 1800
    assert m.limits.model_cny == Decimal("1")


def test_truncated_model_output_not_accepted():
    m = manifest()
    response = {
        "model": "test-model",
        "usage": {"prompt_tokens": 100, "completion_tokens": 5},
        "choices": [{"finish_reason": "length", "message": {"content": '{"events":[]}'}}],
    }
    result = execute_case(
        m,
        model_case(),
        {"DEEPSEEK_API_KEY": "test"},
        client(lambda r: httpx.Response(200, json=response)),
    )
    assert result["validation"] == "rejected"
    assert result["usage"]["prompt_tokens"] == 100


@pytest.mark.parametrize("failure", [False, True])
def test_direct_control_preserves_unknown_date_and_actual_requests(monkeypatch, failure):
    closed = []

    class Fetcher:
        request_count = 0 if failure else 2
        request_log = []

        def __init__(self, policy):
            assert policy.max_requests_per_run == 4
            assert policy.retry_limit == 0

        def check(self, **kwargs):
            assert kwargs["retention_policy"] == "minimal_excerpt"
            if failure:
                raise SourceFetchError("blocked_network", "not public")
            return SimpleNamespace(
                documents=[
                    SimpleNamespace(
                        canonical_url="https://example.com/news",
                        title="报道",
                        excerpt="示例科技完成融资",
                        published_at=None,
                        metadata={},
                    )
                ],
                request_count=2,
                downloaded_bytes=200,
            )

        def close(self):
            closed.append(True)

    monkeypatch.setattr("scripts.research_benchmark.TrustedSourceFetcher", Fetcher)
    case = Case(
        id="read",
        company_key="fictional",
        operation="extract",
        provider="direct",
        url="https://example.com/news",
        names=["示例科技"],
    )
    result = execute_case(manifest(), case, {}, client(lambda r: pytest.fail("unexpected API")))
    assert closed == [True]
    assert result["external_calls"] == (0 if failure else 2)
    if failure:
        assert result["error_code"] == "blocked_network"
        assert result["results"] == []
    else:
        assert result["results"][0]["published_at"] is None
