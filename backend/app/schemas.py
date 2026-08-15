from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    link_display_allowed: bool


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


class CompanySuggestion(BaseModel):
    id: UUID
    legal_name: str
    credit_code: str | None
    registered_region: str | None


class CompanyDetail(BaseModel):
    id: UUID
    is_platform_shared: bool
    legal_name: str
    credit_code: str | None
    registered_region: str | None
    official_website: str | None
    identity_status: str
    identity_verification_basis: str | None
    data_as_of: date | None
    last_checked_at: datetime | None
    freshness_status: str
    information_gaps: list[str]
    investments: list[InvestmentOut]
    events: list[EventOut]
    private_events: list[EventOut]
    unconfirmed_leads: list[EventOut]


class PersonalWatchlistItemOut(BaseModel):
    id: UUID
    company_id: UUID
    legal_name: str
    credit_code: str | None
    registered_region: str | None
    identity_status: str
    freshness_status: str
    last_checked_at: datetime | None
    followed_at: datetime


class PersonalInclusionRequestIn(BaseModel):
    company_name: str | None = Field(default=None, min_length=2, max_length=240)
    credit_code: str | None = Field(default=None, min_length=2, max_length=32)

    @model_validator(mode="after")
    def require_company_identifier(self) -> PersonalInclusionRequestIn:
        if not (self.company_name and self.company_name.strip()) and not (
            self.credit_code and self.credit_code.strip()
        ):
            raise ValueError("company_name or credit_code is required")
        return self


class PersonalCompanyRequestDecisionIn(BaseModel):
    status: Literal["in_review", "completed", "rejected"]
    reason: str = Field(min_length=3, max_length=1000)


class PersonalCompanyRequestOut(BaseModel):
    id: UUID
    owner_user_id: UUID
    request_type: Literal["inclusion", "refresh"]
    company_id: UUID | None
    requested_name: str | None
    requested_credit_code: str | None
    status: Literal["pending", "in_review", "completed", "rejected"]
    reviewed_by_id: UUID | None
    reviewed_at: datetime | None
    decision_reason: str | None
    created_at: datetime
    reused: bool = False


class PersonalQuotaOut(BaseModel):
    used: int
    limit: int
    remaining: int


class PersonalUsageSummaryOut(BaseModel):
    period_key: str
    searches: PersonalQuotaOut
    watchlist_companies: PersonalQuotaOut
    reports: PersonalQuotaOut
    company_requests: PersonalQuotaOut


class PersonalCompanyViewOut(BaseModel):
    company_id: UUID
    first_view: bool
    previous_viewed_at: datetime | None
    viewed_at: datetime
    new_events: list[EventOut]


class PersonalReportCreateIn(BaseModel):
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")


class PersonalCompanyReportSummaryOut(BaseModel):
    id: UUID
    company_id: UUID
    company_legal_name: str
    report_version: str
    title: str
    as_of: datetime
    content_hash: str
    source_event_count: int
    created_at: datetime


class PersonalCompanyReportOut(PersonalCompanyReportSummaryOut):
    markdown: str
    source_event_ids: list[UUID]
    reused: bool = False


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
    verification_basis: str
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


class SharingDecisionOut(BaseModel):
    id: UUID
    action: str
    reason: str
    actor_user_id: UUID
    created_at: datetime
    shared_event_id: UUID | None


class SharingCandidateOut(BaseModel):
    source_event_id: UUID
    company_id: UUID
    company_legal_name: str
    company_credit_code: str | None
    source_scope: str
    owner_type: str
    owner_id: UUID
    owner_name: str
    identity_ambiguous: bool
    shared_event_id: UUID | None
    shared_event_status: str | None
    event: EventOut
    decisions: list[SharingDecisionOut]


class SharingPromotionIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    summary: str = Field(min_length=3, max_length=2000)
    reason: str = Field(min_length=3, max_length=1000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    confirm_evidence_support: bool
    confirm_unchecked_links: bool = False


class SharingRejectionIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class SharingRetractionIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class SharingActionOut(BaseModel):
    action: Literal["promote", "reject", "retract"]
    decision_id: UUID
    source_event_id: UUID | None
    shared_event_id: UUID | None
    shared_event_status: str | None
    reused_shared_event: bool = False


class TrustedSourceCreate(BaseModel):
    company_id: UUID
    name: str = Field(min_length=2, max_length=200)
    source_type: Literal["single_page", "list_page", "rss", "sitemap"]
    root_domain: str = Field(min_length=3, max_length=253)
    start_url: str = Field(min_length=8, max_length=1000)
    list_path_prefix: str | None = Field(default=None, min_length=2, max_length=500)
    access_basis: str = Field(min_length=3, max_length=2000)
    license_status: Literal["public_access", "permission_confirmed", "unclear", "restricted"]
    check_frequency_minutes: int = Field(default=10_080, ge=60, le=525_600)
    content_retention_policy: Literal["metadata_only", "minimal_excerpt"] = "metadata_only"


class TrustedSourceUpdate(BaseModel):
    enabled: bool | None = None
    list_path_prefix: str | None = Field(default=None, min_length=2, max_length=500)
    access_basis: str | None = Field(default=None, min_length=3, max_length=2000)
    license_status: (
        Literal["public_access", "permission_confirmed", "unclear", "restricted"] | None
    ) = None
    content_retention_policy: Literal["metadata_only", "minimal_excerpt"] | None = None


class TrustedSourceOut(BaseModel):
    id: UUID
    company_id: UUID
    company_legal_name: str
    company_identity_status: str
    name: str
    source_type: str
    root_domain: str
    start_url: str
    list_path_prefix: str | None
    enabled: bool
    access_basis: str
    license_status: str
    check_frequency_minutes: int
    content_retention_policy: str
    visibility_scope: str
    last_checked_at: datetime | None
    last_success_at: datetime | None
    last_failure_code: str | None
    last_http_status: int | None
    consecutive_failures: int
    created_at: datetime
    updated_at: datetime


class SourceCheckRequest(BaseModel):
    dry_run: bool = True


class SourceCheckBatchRequest(BaseModel):
    source_ids: list[UUID] = Field(min_length=1, max_length=10)
    dry_run: bool = True


class SourceCheckRunOut(BaseModel):
    id: UUID
    company_id: UUID
    trusted_source_id: UUID
    source_name: str
    trigger_type: str
    scheduled_for: datetime | None
    status: str
    dry_run: bool
    policy_version: str
    request_count: int
    downloaded_bytes: int
    new_count: int
    changed_count: int
    unchanged_count: int
    duplicate_count: int
    failure_count: int
    external_calls: int
    paid_api_calls: int
    input_tokens: int
    output_tokens: int
    estimated_cost: Decimal
    robots_status: str | None
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class CandidateDocumentOut(BaseModel):
    id: UUID
    company_id: UUID
    company_legal_name: str
    trusted_source_id: UUID
    source_name: str
    canonical_url: str
    title: str
    published_at: datetime | None
    first_discovered_at: datetime
    last_observed_at: datetime
    content_hash: str
    change_type: str
    link_health_status: str
    http_status: int | None
    excerpt: str | None
    license_status: str
    current_source_license_status: str
    processing_status: str
    identity_status_at_discovery: str
    visibility_scope: str
    handoff_payload: dict[str, object]
    processed_at: datetime | None
    decision_reason: str | None


class CandidateDocumentDecisionIn(BaseModel):
    decision: Literal["worth_research", "irrelevant", "duplicate", "source_unavailable"]
    reason: str = Field(min_length=3, max_length=1000)


class CandidateDocumentDecisionOut(BaseModel):
    candidate_id: UUID
    processing_status: str
    handoff_payload: dict[str, object]
    event_created: bool = False
    shared_fact_created: bool = False


class CandidateResearchImportIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    evidence_excerpt: str = Field(min_length=3, max_length=1000)
    event_type: Literal[
        "financial_operation",
        "financing_cap_table",
        "contract_commercial",
        "product_technology",
        "governance_people",
        "legal_compliance",
        "capacity_assets",
        "exit_liquidity",
        "information_quality",
    ]
    event_subtype: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    direction: Literal["positive", "negative", "neutral", "mixed", "unknown"]
    materiality_score: int = Field(ge=0, le=100)
    risk_severity: Literal["none", "low", "moderate", "high", "critical"]
    confidence_score: float = Field(ge=0, le=1)
    source_quality: Literal["A", "B", "C", "D"]
    fact_name: str = Field(min_length=2, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    fact_value: str = Field(min_length=1, max_length=500)
    fact_unit: str | None = Field(default=None, max_length=40)
    occurred_at: datetime | None = None
    uncertainties: list[str] = Field(default_factory=list, max_length=20)
    research_reason: str = Field(min_length=3, max_length=1000)


class CandidateResearchImportOut(BaseModel):
    candidate_id: UUID
    research_import_id: UUID
    raw_document_id: UUID
    private_event_id: UUID
    status: str
    reused: bool
    auto_published: bool = False
    shared_fact_created: bool = False
    external_calls: int = 0


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


class AuthEmailVerificationIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class AuthEmailVerificationOut(BaseModel):
    verification_id: str
    expires_in: int


class AuthEmailLoginIn(BaseModel):
    verification_id: str = Field(min_length=8, max_length=2000)
    verification_code: str = Field(pattern=r"^\d{6}$")


class AuthTokenRefreshIn(BaseModel):
    refresh_token: str = Field(min_length=8, max_length=8192)


class AuthTokenOut(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: Literal["Bearer"] = "Bearer"


class AuthMeOut(BaseModel):
    user_id: UUID
    tenant_id: UUID
    email: str
    display_name: str
    auth_provider: str
    roles: list[str]
