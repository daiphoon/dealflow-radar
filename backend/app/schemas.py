from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.event_schema import EventType
from backend.app.investor_analysis_schema import (
    InvestorChangeAnalysisOutput,
    ResearchCandidateAnalysisOutput,
)


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
    link_kind: Literal["public_source", "licensed_provider", "unavailable"]
    detail_available: bool


class EvidenceDetailFieldOut(BaseModel):
    label: str
    value: str


class EvidenceDetailRecordOut(BaseModel):
    title: str
    fields: list[EvidenceDetailFieldOut]
    source_url: str | None = None


class EvidenceDetailOut(BaseModel):
    id: UUID
    company_id: UUID
    company_legal_name: str
    event_id: UUID
    event_title: str
    source_name: str
    source_quality: str
    checked_at: datetime
    heading: str
    description: str
    total_records: int | None
    displayed_records: int
    summary_fields: list[EvidenceDetailFieldOut]
    records: list[EvidenceDetailRecordOut]
    provider_url: str | None
    provider_link_available: bool
    provider_access_notice: str | None


class InvestorChangeAnalysisOut(InvestorChangeAnalysisOutput):
    generated_at: datetime


class ResearchCandidateAnalysisOut(ResearchCandidateAnalysisOutput):
    generated_at: datetime


class FactEvidenceSupportOut(BaseModel):
    evidence_id: UUID
    support_status: Literal[
        "supported",
        "partial",
        "conflicting",
        "pending_review",
        "unsupported",
    ]
    reason_codes: list[str]
    policy_version: str
    assessed_at: datetime


class AtomicFactOut(BaseModel):
    id: UUID
    fact_key: str
    name: str
    value: str
    unit: str | None
    occurrence_count: int
    support_status: Literal[
        "supported",
        "partial",
        "conflicting",
        "pending_review",
        "unsupported",
    ]
    evidence_supports: list[FactEvidenceSupportOut]


class TenderObservationOut(BaseModel):
    observation_id: UUID | None = None
    fact_version: str
    observation_kind: str
    occurred_on: date | None
    date_precision: Literal["day", "unknown"]
    observed_at: datetime
    facts: list[dict[str, str | None]]
    reviewed_at: datetime | None = None
    evidence_ids: list[UUID]
    is_current: bool
    confirmed: bool = False
    evidence_available: bool = True
    can_publish: bool = False


class FinancingFieldsOut(BaseModel):
    subject_name: str
    subject_scope: str
    round: str | None
    amount_text: str | None
    investors: list[str]
    disclosed_on: date | None
    occurred_on: date | None


class FinancingComparisonOut(BaseModel):
    version: str
    relation: Literal["exact_matter", "compatible_evidence", "unlinked"]
    fields: dict[
        str,
        Literal[
            "matched",
            "not_disclosed",
            "additional",
            "not_repeated",
            "different",
            "partial_overlap",
            "related_date",
        ],
    ]
    reasons: list[str]


class FinancingObservationOut(BaseModel):
    kind: Literal["initial", "same_facts", "correction_candidate", "conflicting", "incomplete"]
    fact_version: str
    fields: FinancingFieldsOut
    issues: list[str]
    observed_at: datetime
    evidence_id: UUID
    source_url: str
    source_title: str
    excerpt: str
    confirmed: Literal[False] = False
    comparison: FinancingComparisonOut | None = None


class CuratedVersionOut(BaseModel):
    record_version: str
    observed_at: datetime
    fact_version: str
    reviewed_at: datetime
    as_of_date: str
    date_text: str
    date_precision: str
    date_basis: str
    occurred_date_text: str
    subject_scope: str
    source_grade: str
    content_support: str
    assessment_status: Literal["not_assessed"]
    title: str
    summary: str
    facts: list[dict[str, str | None]]
    sources: list[dict[str, str]]
    evidence_ids: list[UUID]
    confirmed: bool = False
    is_current: bool = False
    evidence_available: bool = True


class EventOut(BaseModel):
    id: UUID
    event_type: str
    event_subtype: str
    occurred_at: datetime | None
    occurred_on: date | None = None
    fact_version: str | None = None
    display_kind: Literal["confirmed_change", "baseline", "unconfirmed"] = "baseline"
    tender_observations: list[TenderObservationOut] = Field(default_factory=list)
    curated_versions: list[CuratedVersionOut] = Field(default_factory=list)
    financing_observations: list[FinancingObservationOut] = Field(default_factory=list)
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
    fact_ledger: list[AtomicFactOut] = Field(default_factory=list)
    visibility_scope: str
    analysis: InvestorChangeAnalysisOut | None = None
    research_analysis: ResearchCandidateAnalysisOut | None = None


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


class CategoryCoverageOut(BaseModel):
    category: EventType
    status: Literal[
        "not_configured",
        "not_checked",
        "blocked",
        "failed",
        "no_records",
        "candidates_only",
        "evidence_obtained",
        "unknown",
    ]
    route: Literal["business_capital", "technology_risk_exit"] | None = None
    last_attempt_at: datetime | None = None
    search_checked_at: datetime | None = None
    evidence_checked_at: datetime | None = None
    cache_reused: bool = False
    evidence_count: int | None = Field(default=None, ge=0)
    blocked_count: int | None = Field(default=None, ge=0)
    failed_count: int | None = Field(default=None, ge=0)
    gaps: list[str] = Field(default_factory=list)


class ResearchResultOut(BaseModel):
    outcome: Literal["no_usable_evidence", "candidates_available"]
    finished_at: datetime | None
    message: str
    limitations: list[str]
    coverage_summary: list[str] = Field(default_factory=list)
    category_coverage: list[CategoryCoverageOut] = Field(default_factory=list)


class CurrentCompanyContentOut(BaseModel):
    confirmed_changes: int = Field(ge=0)
    baseline_facts: int = Field(ge=0)
    unconfirmed_leads: int = Field(ge=0)


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
    platform_unconfirmed_leads: list[EventOut]
    private_events: list[EventOut]
    unconfirmed_leads: list[EventOut]
    personal_research_result: ResearchResultOut | None = None


class WatchlistMonitorOut(BaseModel):
    status: str
    categories: list[str]
    last_attempt_at: datetime | None = None
    last_successful_check_at: datetime | None = None
    next_check_at: datetime | None = None


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
    monitoring: WatchlistMonitorOut | None = None


class PersonalInclusionRequestIn(BaseModel):
    company_name: str | None = Field(default=None, min_length=2, max_length=240)
    credit_code: str | None = Field(
        default=None,
        min_length=18,
        max_length=18,
        pattern=r"^[0-9A-Za-z]{18}$",
    )

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


class PersonalCompanyRequestResearchApprovalIn(BaseModel):
    company_id: UUID | None = None
    reason: str = Field(min_length=3, max_length=1000)


class PersonalCompanyRequestCancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


PersonalCompanyRequestStatus = Literal[
    "pending",
    "in_review",
    "identity_queued",
    "identity_checking",
    "awaiting_confirmation",
    "needs_input",
    "research_queued",
    "researching",
    "partial",
    "budget_deferred",
    "cancel_requested",
    "cancelled",
    "completed",
    "rejected",
    "failed",
]


class PersonalCompanyRequestOut(BaseModel):
    id: UUID
    owner_user_id: UUID
    request_type: Literal["inclusion", "refresh"]
    company_id: UUID | None
    requested_name: str | None
    requested_credit_code: str | None
    status: PersonalCompanyRequestStatus
    research_job_id: UUID | None
    research_job_status: str | None
    research_modules: dict[str, str]
    research_result: ResearchResultOut | None = None
    current_company_content: CurrentCompanyContentOut | None = None
    queue_position: int | None
    resolved_legal_name: str | None
    resolved_credit_code: str | None
    resolved_registered_region: str | None
    resolved_registration_status: str | None
    resolved_registration_authority: str | None
    identity_checked_at: datetime | None
    confirmation_expires_at: datetime | None
    confirmed_at: datetime | None
    external_calls: int
    cache_hits: int
    cancelled_at: datetime | None
    cancellation_stage: str | None
    cancellation_reason: str | None
    last_error_code: str | None
    can_confirm: bool
    can_cancel: bool
    status_message: str
    reviewed_by_id: UUID | None
    reviewed_at: datetime | None
    decision_reason: str | None
    created_at: datetime
    updated_at: datetime
    reused: bool = False


class PersonalQuotaIncreaseRequestIn(BaseModel):
    requested_daily_extra: int = Field(default=0, ge=0, le=100)
    requested_monthly_extra: int = Field(default=0, ge=0, le=1000)
    reason: str = Field(min_length=5, max_length=500)

    @model_validator(mode="after")
    def require_positive_increase(self) -> PersonalQuotaIncreaseRequestIn:
        if self.requested_daily_extra == 0 and self.requested_monthly_extra == 0:
            raise ValueError("at least one requested quota increase must be positive")
        return self


class PersonalQuotaIncreaseDecisionIn(BaseModel):
    status: Literal["approved", "rejected"]
    approved_daily_extra: int = Field(default=0, ge=0, le=100)
    approved_monthly_extra: int = Field(default=0, ge=0, le=1000)
    effective_until: datetime | None = None
    reason: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def validate_decision(self) -> PersonalQuotaIncreaseDecisionIn:
        if self.status == "approved":
            if self.approved_daily_extra == 0 and self.approved_monthly_extra == 0:
                raise ValueError("approved quota increase must be positive")
            if self.effective_until is None:
                raise ValueError("approved quota increase requires an expiry")
        return self


class PersonalQuotaIncreaseRequestOut(BaseModel):
    id: UUID
    owner_user_id: UUID
    owner_display_name: str
    owner_email: str
    requested_daily_extra: int
    requested_monthly_extra: int
    request_reason: str
    status: Literal["pending", "approved", "rejected", "expired"]
    approved_daily_extra: int
    approved_monthly_extra: int
    effective_until: datetime | None
    reviewed_by_id: UUID | None
    reviewed_at: datetime | None
    decision_reason: str | None
    created_at: datetime
    updated_at: datetime


class PersonalQuotaOut(BaseModel):
    used: int
    limit: int
    remaining: int


class PersonalUsageSummaryOut(BaseModel):
    period_key: str
    daily_request_period_key: str
    searches: PersonalQuotaOut
    watchlist_companies: PersonalQuotaOut
    reports: PersonalQuotaOut
    daily_company_requests: PersonalQuotaOut
    company_requests: PersonalQuotaOut


class PersonalCompanyViewOut(BaseModel):
    company_id: UUID
    first_view: bool
    previous_viewed_at: datetime | None
    window_start_at: datetime
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
    source_observation_id: UUID | None = None
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
    observation_id: UUID | None = None
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


class AuthPhoneVerificationIn(BaseModel):
    phone_number: str = Field(min_length=7, max_length=32)


class AuthPhoneLoginIn(BaseModel):
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
