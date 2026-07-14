from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventType(StrEnum):
    FINANCIAL_OPERATION = "financial_operation"
    FINANCING_CAP_TABLE = "financing_cap_table"
    CONTRACT_COMMERCIAL = "contract_commercial"
    PRODUCT_TECHNOLOGY = "product_technology"
    GOVERNANCE_PEOPLE = "governance_people"
    LEGAL_COMPLIANCE = "legal_compliance"
    CAPACITY_ASSETS = "capacity_assets"
    EXIT_LIQUIDITY = "exit_liquidity"
    INFORMATION_QUALITY = "information_quality"


class Direction(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class RiskSeverity(StrEnum):
    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class SourceQuality(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class PublishDecision(StrEnum):
    AUTO_PUBLISH = "auto_publish"
    HUMAN_REVIEW = "human_review"
    REJECT = "reject"


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=1000)
    unit: str | None = Field(default=None, max_length=32)


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    document_id: UUID
    quote: str = Field(min_length=1, max_length=1000)
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_offsets(self) -> EvidenceSpan:
        if (
            self.start_offset is not None
            and self.end_offset is not None
            and self.end_offset < self.start_offset
        ):
            raise ValueError("end_offset must not precede start_offset")
        return self


class StrictEventCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"]
    prompt_version: str = Field(min_length=1, max_length=64)
    matched_company_id: UUID | None
    company_match_confidence: float = Field(ge=0, le=1)
    event_type: EventType
    event_subtype: str = Field(min_length=1, max_length=64)
    occurred_at: datetime | None
    published_at: datetime | None
    direction: Direction
    materiality_score: int = Field(ge=0, le=100)
    risk_severity: RiskSeverity
    confidence_score: float = Field(ge=0, le=1)
    source_quality: SourceQuality
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1000)
    facts: list[Fact] = Field(min_length=1, max_length=50)
    uncertainties: list[str] = Field(default_factory=list, max_length=20)
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    counterparty: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    evidence_spans: list[EvidenceSpan] = Field(min_length=1, max_length=20)
    publish_decision: PublishDecision
    requires_human_review: bool

    @model_validator(mode="after")
    def validate_publication(self) -> StrictEventCandidate:
        if (self.amount is None) != (self.currency is None):
            raise ValueError("amount and currency must be provided together")
        if self.publish_decision == PublishDecision.AUTO_PUBLISH:
            if self.matched_company_id is None:
                raise ValueError("auto-publish requires a resolved company")
            if self.requires_human_review:
                raise ValueError("review-required event cannot auto-publish")
            if self.risk_severity in {RiskSeverity.HIGH, RiskSeverity.CRITICAL}:
                raise ValueError("high-risk event cannot auto-publish")
        return self
