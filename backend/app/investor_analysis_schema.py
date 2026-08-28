from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

INVESTOR_ANALYSIS_SCHEMA_VERSION = "investor-change-analysis-v1"
INVESTOR_ANALYSIS_PROMPT_VERSION = "investor-change-zh-v1"
INVESTOR_ANALYSIS_DISCLAIMER = "模型辅助解读，不构成投资建议。"
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
