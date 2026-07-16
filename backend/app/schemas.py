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
    published_on: date | None
    observed_at: datetime
    excerpt: str
    url_health_status: str
    url_http_status: int | None
    url_checked_at: datetime | None
    final_url: str | None
    visibility_scope: str


class EventOut(BaseModel):
    id: UUID
    event_type: str
    event_subtype: str
    occurred_at: datetime | None
    published_at: datetime | None
    published_on: date | None
    direction: str
    materiality_score: int
    risk_severity: str
    confidence_score: Decimal
    source_quality: str
    title: str
    summary: str
    facts: list[dict[str, str | None]]
    uncertainties: list[str]
    status: str
    publication_route: str
    publication_policy_version: str
    publication_reasons: list[str]
    observed_at: datetime
    evidence: list[EvidenceOut]
    visibility_scope: str


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


class CompanySearchResult(BaseModel):
    id: UUID
    legal_name: str
    credit_code: str | None
    registered_region: str | None
    identity_status: str
    freshness_status: str
    last_checked_at: datetime | None


class CompanyDetail(BaseModel):
    id: UUID
    legal_name: str
    credit_code: str | None
    registered_region: str | None
    official_website: str | None
    identity_status: str
    data_as_of: date | None
    last_checked_at: datetime | None
    freshness_status: str
    information_gaps: list[str]
    investments: list[InvestmentOut]
    events: list[EventOut]
    private_events: list[EventOut]
    unconfirmed_leads: list[EventOut]


class ReviewDecisionIn(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject)$")
    reason: str = Field(min_length=3, max_length=1000)


class IdentityResolutionIn(BaseModel):
    verification_id: UUID
    reason: str = Field(min_length=3, max_length=1000)


class IdentityCandidateOut(BaseModel):
    verification_id: UUID
    company_id: UUID
    legal_name: str
    credit_code: str
    registered_region: str | None
    registration_status: str
    verification_status: str
    match_rule: str
    checked_at: datetime
    source_name: str
    canonical_url: str


class IdentityResolutionOut(BaseModel):
    review_id: UUID
    company_id: UUID
    event_id: UUID
    publication_route: str
    review_status: str


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID | None
    entity_mention_id: UUID | None
    status: str
    trigger_rules: list[str]
    decision: str | None
    decision_reason: str | None


class ReviewWorkbenchOut(BaseModel):
    id: UUID
    event_id: UUID | None
    entity_mention_id: UUID | None
    status: str
    trigger_rules: list[str]
    decision: str | None
    decision_reason: str | None
    created_at: datetime
    decided_at: datetime | None
    company_id: UUID | None
    company_legal_name: str | None
    event: EventOut | None
    mention_text: str | None
    match_rule: str | None
    match_confidence: Decimal | None
    resolution_status: str | None
    identity_candidates: list[IdentityCandidateOut] = Field(default_factory=list)


class IngestResult(BaseModel):
    records_seen: int
    documents_created: int
    events_created: int
    reviews_created: int
    external_calls: int = 0
    estimated_cost: Decimal = Decimal("0")


class ResearchImportResult(BaseModel):
    status: str
    research_import_id: UUID
    batch_id: str
    records_seen: int
    documents_created: int
    events_created: int
    reviews_created: int
    resolved_records: int
    unresolved_records: int
    auto_published_records: int = 0
    unconfirmed_records: int = 0
    identity_review_records: int = 0
    external_calls: int = 0
    estimated_cost: Decimal = Decimal("0")


class OfficialIdentityImportResult(BaseModel):
    status: str
    research_import_id: UUID | None = None
    batch_id: str
    records_seen: int
    verifications_created: int
    verified_records: int
    conflict_records: int
    unmatched_records: int
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
