from __future__ import annotations

import json
from decimal import Decimal

import httpx
from pydantic import ValidationError

from backend.app.config import InvestorAnalysisPolicy
from backend.app.investor_analysis_schema import (
    InvestorChangeAnalysisOutput,
    InvestorChangeAnalysisRequest,
    LLMProviderResult,
)
from backend.app.providers import LLMProviderError


class DeepSeekProviderError(LLMProviderError):
    pass


class DeepSeekInvestorAnalysisProvider:
    code = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        policy: InvestorAnalysisPolicy,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("DeepSeek API key must not be blank")
        self.model = policy.model
        self._api_key = api_key.strip()
        self._policy = policy
        self._client = client or httpx.Client(
            timeout=policy.timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @staticmethod
    def _system_prompt() -> str:
        return (
            "你是未上市公司投后监测分析助手。只能解释输入中已经给出的变化与证据，"
            "不得补充事实、金额、比例、日期、主体、因果关系或投资结论。"
            "使用适合四十至六十岁投资者阅读的简体中文，避免中英混杂。"
            "输出必须是 json，并完全符合提供的 JSON Schema。"
            "before_value 和 after_value 必须逐字复制输入；evidence_ids 只能引用输入 ID。"
            "不确定时明确写入 uncertainties，不得用推测替代未知信息。"
        )

    @staticmethod
    def _user_prompt(request: InvestorChangeAnalysisRequest) -> str:
        schema = InvestorChangeAnalysisOutput.model_json_schema()
        payload = request.model_dump(mode="json")
        return json.dumps(
            {
                "task": "基于下列确定性变化和证据，生成投资者变化解读 json。",
                "input": payload,
                "output_json_schema": schema,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def _estimated_cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        million = Decimal("1000000")
        return (
            Decimal(input_tokens) * self._policy.input_cost_per_million_tokens / million
            + Decimal(output_tokens) * self._policy.output_cost_per_million_tokens / million
        )

    def analyze_investor_change(
        self,
        request: InvestorChangeAnalysisRequest,
    ) -> LLMProviderResult:
        user_prompt = self._user_prompt(request)
        if len(user_prompt) > self._policy.max_input_characters:
            raise DeepSeekProviderError("analysis input exceeds configured character limit")
        external_calls = 0
        total_input_tokens = 0
        total_output_tokens = 0
        last_error = "invalid_model_output"
        for _ in range(self._policy.retry_limit + 1):
            external_calls += 1
            try:
                response = self._client.post(
                    self._policy.endpoint_url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": self._system_prompt()},
                            {"role": "user", "content": user_prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "thinking": {"type": "disabled"},
                        "temperature": 0.1,
                        "max_tokens": self._policy.max_output_tokens,
                        "stream": False,
                    },
                )
            except httpx.HTTPError as error:
                raise DeepSeekProviderError(
                    "DeepSeek request failed",
                    external_calls=external_calls,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                ) from error
            if len(response.content) > self._policy.max_response_bytes:
                raise DeepSeekProviderError(
                    "DeepSeek response exceeds configured byte limit",
                    external_calls=external_calls,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                )
            if response.status_code != 200:
                raise DeepSeekProviderError(
                    f"DeepSeek returned HTTP {response.status_code}",
                    external_calls=external_calls,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                )
            try:
                payload = response.json()
                usage = payload.get("usage", {})
                input_tokens = int(usage.get("prompt_tokens") or 0)
                output_tokens = int(usage.get("completion_tokens") or 0)
                total_input_tokens += input_tokens
                total_output_tokens += output_tokens
                content = payload["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    last_error = "empty_model_output"
                    continue
                analysis = InvestorChangeAnalysisOutput.model_validate_json(content)
                return LLMProviderResult(
                    analysis=analysis,
                    external_calls=external_calls,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    estimated_cost=self._estimated_cost(
                        total_input_tokens,
                        total_output_tokens,
                    ),
                    response_id=(str(payload.get("id"))[:200] if payload.get("id") else None),
                )
            except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError):
                last_error = "invalid_model_output"
                continue
        raise DeepSeekProviderError(
            last_error,
            external_calls=external_calls,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            estimated_cost=self._estimated_cost(
                total_input_tokens,
                total_output_tokens,
            ),
        )
