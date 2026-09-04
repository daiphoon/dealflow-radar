from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from backend.app.config import InvestorAnalysisPolicy
from backend.app.deepseek import DeepSeekInvestorAnalysisProvider, DeepSeekProviderError
from backend.app.investor_analysis_schema import (
    INVESTOR_ANALYSIS_DISCLAIMER,
    INVESTOR_ANALYSIS_SCHEMA_VERSION,
    RESEARCH_CANDIDATE_ANALYSIS_DISCLAIMER,
    RESEARCH_CANDIDATE_ANALYSIS_SCHEMA_VERSION,
    InvestorChangeAnalysisOutput,
    InvestorChangeAnalysisRequest,
    InvestorEvidenceInput,
    ResearchCandidateAnalysisOutput,
    ResearchCandidateAnalysisRequest,
    ResearchEvidenceInput,
)

ROOT = Path(__file__).resolve().parents[2]


def _request() -> InvestorChangeAnalysisRequest:
    return InvestorChangeAnalysisRequest(
        event_id=uuid4(),
        company_name="示例变化科技有限公司",
        event_type="financing_cap_table",
        field_label="工商登记持股比例",
        before_value="20%",
        after_value="25%",
        deterministic_summary="工商登记持股比例由20%变为25%。",
        uncertainties=["实际控制关系尚不确定。"],
        evidence=[
            InvestorEvidenceInput(
                evidence_id=uuid4(),
                source_name="授权工商数据源",
                excerpt="工商登记持股比例由20%变为25%。",
                observed_at="2026-08-28T08:00:00+00:00",
            )
        ],
    )


def _analysis(request: InvestorChangeAnalysisRequest) -> InvestorChangeAnalysisOutput:
    return InvestorChangeAnalysisOutput(
        schema_version=INVESTOR_ANALYSIS_SCHEMA_VERSION,
        headline="工商登记持股比例发生变化",
        before_value=request.before_value,
        after_value=request.after_value,
        what_changed="工商登记持股比例由20%变为25%。",
        why_it_matters="该变化可能影响股东表决权和公司治理判断。",
        potential_impacts=["需要重新核对治理关系。"],
        uncertainties=["实际控制关系尚不确定。"],
        evidence_ids=[request.evidence[0].evidence_id],
        confidence=0.9,
        follow_up_items=["关注后续股东变更。"],
        impact_direction="uncertain",
        disclaimer=INVESTOR_ANALYSIS_DISCLAIMER,
    )


def _research_request() -> ResearchCandidateAnalysisRequest:
    return ResearchCandidateAnalysisRequest(
        event_id=uuid4(),
        company_name="苏州涌现智能科技有限公司",
        credit_code="91320509MA26R3RW5C",
        event_type="financing_cap_table",
        deterministic_title="公司完成融资公告",
        deterministic_summary="公开页面提到公司已完成融资。",
        evidence=[
            ResearchEvidenceInput(
                evidence_id=uuid4(),
                source_name="公司官方网站",
                title="公司完成融资公告",
                excerpt="苏州涌现智能科技有限公司已完成融资，相关事项已经公告。",
                published_at="2026-09-04T08:00:00+00:00",
                observed_at="2026-09-04T09:00:00+00:00",
            )
        ],
    )


def _research_analysis(
    request: ResearchCandidateAnalysisRequest,
) -> ResearchCandidateAnalysisOutput:
    return ResearchCandidateAnalysisOutput(
        schema_version=RESEARCH_CANDIDATE_ANALYSIS_SCHEMA_VERSION,
        event_id=request.event_id,
        event_type=request.event_type,
        headline="公司出现新的融资进展线索",
        what_changed="公开页面提到公司已经完成融资。",
        why_it_matters="该线索可能影响股东对公司资本实力的判断。",
        potential_impacts=["需要继续核对融资对股权结构的影响。"],
        uncertainties=["当前证据没有确认融资金额和投资方。"],
        evidence_ids=[request.evidence[0].evidence_id],
        confidence=0.82,
        follow_up_items=["继续核对公司正式公告。"],
        impact_direction="uncertain",
        disclaimer=RESEARCH_CANDIDATE_ANALYSIS_DISCLAIMER,
    )


def test_provider_uses_bounded_non_thinking_json_request() -> None:
    request = _request()
    captured: dict[str, object] = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        captured["url"] = str(http_request.url)
        captured["authorization"] = http_request.headers["Authorization"]
        captured["payload"] = json.loads(http_request.content)
        return httpx.Response(
            200,
            json={
                "id": "response-001",
                "choices": [{"message": {"content": _analysis(request).model_dump_json()}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    policy = InvestorAnalysisPolicy(
        input_cost_per_million_tokens=Decimal("1"),
        output_cost_per_million_tokens=Decimal("2"),
    )
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    provider = DeepSeekInvestorAnalysisProvider(
        api_key="test-secret",
        policy=policy,
        client=client,
    )

    result = provider.analyze_investor_change(request)

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == "Bearer test-secret"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["stream"] is False
    assert result.analysis.before_value == "20%"
    assert result.analysis.after_value == "25%"
    assert result.external_calls == 1
    assert result.estimated_cost == Decimal("0.0002")


def test_provider_retries_invalid_json_once_and_accounts_for_both_attempts() -> None:
    request = _request()
    calls = 0

    def handler(_http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = "not-json" if calls == 1 else _analysis(request).model_dump_json()
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    provider = DeepSeekInvestorAnalysisProvider(
        api_key="test-secret",
        policy=InvestorAnalysisPolicy(retry_limit=1),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = provider.analyze_investor_change(request)

    assert calls == 2
    assert result.external_calls == 2
    assert result.input_tokens == 20
    assert result.output_tokens == 10


def test_research_provider_repairs_one_invalid_output_and_keeps_evidence_binding() -> None:
    request = _research_request()
    calls = 0
    prompts: list[dict[str, object]] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(http_request.content)
        prompts.append(json.loads(payload["messages"][1]["content"]))
        content = "not-json" if calls == 1 else _research_analysis(request).model_dump_json()
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 6},
            },
        )

    provider = DeepSeekInvestorAnalysisProvider(
        api_key="test-secret",
        policy=InvestorAnalysisPolicy(retry_limit=1),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = provider.analyze_research_candidate(request)

    assert calls == 2
    assert result.external_calls == 2
    assert result.analysis.event_id == request.event_id
    assert result.analysis.evidence_ids == [request.evidence[0].evidence_id]
    assert "repair_instruction" not in prompts[0]
    assert prompts[1]["previous_rejection_code"] == "invalid_model_output"


def test_provider_rejects_oversized_prompt_without_external_call() -> None:
    calls = 0

    def handler(_http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    provider = DeepSeekInvestorAnalysisProvider(
        api_key="test-secret",
        policy=InvestorAnalysisPolicy(max_input_characters=10),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(DeepSeekProviderError, match="character limit") as error:
        provider.analyze_investor_change(_request())

    assert error.value.external_calls == 0
    assert calls == 0


def test_committed_output_schema_matches_required_contract() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "investor_change_analysis.schema.json").read_text(encoding="utf-8")
    )

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(InvestorChangeAnalysisOutput.model_fields)
    assert schema["properties"]["schema_version"]["const"] == (INVESTOR_ANALYSIS_SCHEMA_VERSION)
    assert schema["properties"]["disclaimer"]["const"] == INVESTOR_ANALYSIS_DISCLAIMER

    research_schema = json.loads(
        (ROOT / "schemas" / "research_candidate_analysis.schema.json").read_text(encoding="utf-8")
    )
    assert research_schema == ResearchCandidateAnalysisOutput.model_json_schema()
