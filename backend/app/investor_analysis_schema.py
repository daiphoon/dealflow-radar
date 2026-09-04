from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from backend.app.event_schema import EventType

INVESTOR_ANALYSIS_SCHEMA_VERSION = "investor-change-analysis-v1"
INVESTOR_ANALYSIS_PROMPT_VERSION = "investor-change-zh-v1"
INVESTOR_ANALYSIS_DISCLAIMER = "模型辅助解读，不构成投资建议。"
RESEARCH_CANDIDATE_ANALYSIS_SCHEMA_VERSION = "research-candidate-analysis-v1"
RESEARCH_CANDIDATE_ANALYSIS_PROMPT_VERSION = "bounded-research-candidate-zh-v1"
RESEARCH_CANDIDATE_POLICY_VERSION = "bounded-web-quality-v4"
RESEARCH_CANDIDATE_ANALYSIS_DISCLAIMER = "模型辅助解读，内容仍待核实，不构成投资建议。"
_NUMERIC_TOKEN = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?%?")
_CREDIT_CODE_TOKEN = re.compile(r"(?<![A-Z0-9])[0-9A-Z]{18}(?![A-Z0-9])")
_FORBIDDEN_ADVICE = (
    "建议买入",
    "建议卖出",
    "建议投资",
    "建议认购",
    "可以买入",
    "可以投资",
    "值得投资",
    "保证收益",
    "确定上涨",
    "稳赚",
    "保本",
)
AnalysisListItem = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
]


class InvestorEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: UUID
    source_name: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(min_length=1, max_length=2000)
    observed_at: str = Field(min_length=1, max_length=80)


class InvestorChangeAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    company_name: str = Field(min_length=1, max_length=240)
    event_type: str = Field(min_length=1, max_length=64)
    field_label: str = Field(min_length=1, max_length=300)
    before_value: str = Field(min_length=1, max_length=1000)
    after_value: str = Field(min_length=1, max_length=1000)
    deterministic_summary: str = Field(min_length=1, max_length=2000)
    uncertainties: list[str] = Field(default_factory=list, max_length=8)
    evidence: list[InvestorEvidenceInput] = Field(min_length=1, max_length=5)


class InvestorChangeAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["investor-change-analysis-v1"]
    headline: str = Field(min_length=1, max_length=120)
    before_value: str = Field(min_length=1, max_length=1000)
    after_value: str = Field(min_length=1, max_length=1000)
    what_changed: str = Field(min_length=1, max_length=600)
    why_it_matters: str = Field(min_length=1, max_length=800)
    potential_impacts: list[AnalysisListItem] = Field(max_length=3)
    uncertainties: list[AnalysisListItem] = Field(max_length=4)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=5)
    confidence: float = Field(ge=0, le=1)
    follow_up_items: list[AnalysisListItem] = Field(max_length=4)
    impact_direction: Literal["positive", "negative", "mixed", "neutral", "uncertain"]
    disclaimer: Literal["模型辅助解读，不构成投资建议。"]

    @field_validator(
        "headline",
        "what_changed",
        "why_it_matters",
        mode="after",
    )
    @classmethod
    def require_single_paragraph(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("analysis text must not be blank")
        return normalized

    @field_validator("evidence_ids")
    @classmethod
    def require_unique_evidence_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("evidence_ids must be unique")
        return value


class LLMProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis: InvestorChangeAnalysisOutput
    external_calls: int = Field(default=1, ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost: Decimal = Field(ge=0)
    response_id: str | None = Field(default=None, max_length=200)


class ResearchEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: UUID
    source_name: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    excerpt: str = Field(min_length=1, max_length=2000)
    published_at: str | None = Field(default=None, max_length=80)
    observed_at: str = Field(min_length=1, max_length=80)


class ResearchCandidateAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    company_name: str = Field(min_length=1, max_length=240)
    credit_code: str = Field(min_length=18, max_length=18)
    event_type: EventType
    deterministic_title: str = Field(min_length=1, max_length=200)
    deterministic_summary: str = Field(min_length=1, max_length=2000)
    evidence: list[ResearchEvidenceInput] = Field(min_length=1, max_length=5)


class ResearchCandidateAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["research-candidate-analysis-v1"]
    event_id: UUID
    event_type: EventType
    headline: str = Field(min_length=1, max_length=120)
    what_changed: str = Field(min_length=1, max_length=600)
    why_it_matters: str = Field(min_length=1, max_length=800)
    potential_impacts: list[AnalysisListItem] = Field(max_length=3)
    uncertainties: list[AnalysisListItem] = Field(max_length=4)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=5)
    confidence: float = Field(ge=0, le=1)
    follow_up_items: list[AnalysisListItem] = Field(max_length=4)
    impact_direction: Literal["positive", "negative", "mixed", "neutral", "uncertain"]
    disclaimer: Literal["模型辅助解读，内容仍待核实，不构成投资建议。"]

    @field_validator("headline", "what_changed", "why_it_matters", mode="after")
    @classmethod
    def require_single_paragraph(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("analysis text must not be blank")
        return normalized

    @field_validator("evidence_ids")
    @classmethod
    def require_unique_evidence_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("evidence_ids must be unique")
        return value


class ResearchLLMProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis: ResearchCandidateAnalysisOutput
    external_calls: int = Field(default=1, ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost: Decimal = Field(ge=0)
    response_id: str | None = Field(default=None, max_length=200)


def validate_research_candidate_output(
    request: ResearchCandidateAnalysisRequest,
    output: ResearchCandidateAnalysisOutput,
) -> None:
    if output.event_id != request.event_id or output.event_type != request.event_type:
        raise ValueError("analysis changed the bound event identity")
    allowed_evidence_ids = {item.evidence_id for item in request.evidence}
    if not set(output.evidence_ids) <= allowed_evidence_ids:
        raise ValueError("analysis referenced evidence outside the request")
    source_text = " ".join(
        [
            request.company_name,
            request.credit_code,
            request.event_type.value,
            request.deterministic_title,
            request.deterministic_summary,
            *(
                value
                for evidence in request.evidence
                for value in (
                    evidence.source_name,
                    evidence.title,
                    evidence.excerpt,
                    evidence.published_at or "",
                    evidence.observed_at,
                )
            ),
        ]
    )
    output_text = " ".join(
        [
            output.headline,
            output.what_changed,
            output.why_it_matters,
            *output.potential_impacts,
            *output.uncertainties,
            *output.follow_up_items,
        ]
    )
    invented_numbers = set(_NUMERIC_TOKEN.findall(output_text)) - set(
        _NUMERIC_TOKEN.findall(source_text)
    )
    if invented_numbers:
        raise ValueError("analysis introduced numbers absent from evidence")
    allowed_credit_codes = {request.credit_code.upper()}
    invented_credit_codes = (
        set(_CREDIT_CODE_TOKEN.findall(output_text.upper())) - allowed_credit_codes
    )
    if invented_credit_codes:
        raise ValueError("analysis introduced a company identity absent from the request")
    if any(phrase in output_text for phrase in _FORBIDDEN_ADVICE):
        raise ValueError("analysis contains prohibited investment advice")
