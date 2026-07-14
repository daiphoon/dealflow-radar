from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EvidenceOut(BaseModel):
    id: UUID
    source_name: str
    source_quality: str
    title: str
    canonical_url: str
    published_at: datetime | None
    observed_at: datetime
    excerpt: str


class EventOut(BaseModel):
    id: UUID
    event_type: str
    event_subtype: str
    occurred_at: datetime | None
    published_at: datetime | None
    direction: str
    materiality_score: int
    risk_severity: str
    confidence_score: Decimal
    source_quality: str
    title: str
    summary: str
    status: str
    observed_at: datetime
    evidence: list[EvidenceOut]


class InvestmentOut(BaseModel):
    fund_id: UUID
    fund_name: str
    round_name: str
    amount: Decimal | None
    currency: str | None
    ownership: Decimal | None
    internal_valuation: Decimal | None
    visibility_scope: str


class CompanyListItem(BaseModel):
    id: UUID
    legal_name: str
    identity_status: str
    freshness_status: str
    last_checked_at: datetime | None
    latest_event_title: str | None
    highest_risk: str | None
    information_gaps: list[str]


class CompanyDetail(BaseModel):
    id: UUID
    legal_name: str
    registered_region: str | None
    identity_status: str
    data_as_of: date | None
    last_checked_at: datetime | None
    freshness_status: str
    information_gaps: list[str]
    investments: list[InvestmentOut]
    events: list[EventOut]


class ReviewDecisionIn(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject)$")
    reason: str = Field(min_length=3, max_length=1000)


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    status: str
    trigger_rules: list[str]
    decision: str | None
    decision_reason: str | None


class IngestResult(BaseModel):
    records_seen: int
    documents_created: int
    events_created: int
    reviews_created: int
    external_calls: int = 0
    estimated_cost: Decimal = Decimal("0")


class RefreshResult(BaseModel):
    status: str
    job_id: UUID | None = None
    provider: str = "mock"
    estimated_searches: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_cost: Decimal = Decimal("0")
