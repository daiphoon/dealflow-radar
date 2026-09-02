import "server-only";

import { cookies } from "next/headers";

import { ACCESS_TOKEN_COOKIE, authProvider } from "@/lib/auth-session";

export type AuthMe = {
  user_id: string;
  tenant_id: string;
  email: string;
  display_name: string;
  auth_provider: string;
  roles: string[];
};

export type CompanyListItem = {
  id: string;
  legal_name: string;
  identity_status: string;
  freshness_status: string;
  last_checked_at: string | null;
  latest_event_title: string | null;
  highest_risk: string | null;
  information_gaps: string[];
};

export type CompanySearchResult = {
  id: string;
  legal_name: string;
  credit_code: string | null;
  registered_region: string | null;
  identity_status: string;
  freshness_status: string;
  last_checked_at: string | null;
};

export type CompanySuggestion = {
  id: string;
  legal_name: string;
  credit_code: string | null;
  registered_region: string | null;
};

export type Evidence = {
  id: string;
  source_name: string;
  source_quality: string;
  title: string;
  canonical_url: string;
  published_at: string | null;
  published_on: string | null;
  observed_at: string;
  excerpt: string;
  url_health_status: string;
  url_http_status: number | null;
  url_checked_at: string | null;
  final_url: string | null;
  visibility_scope: string;
  link_display_allowed: boolean;
  link_kind: "public_source" | "licensed_provider" | "unavailable";
  detail_available: boolean;
};

export type EvidenceDetail = {
  id: string;
  company_id: string;
  company_legal_name: string;
  event_id: string;
  event_title: string;
  source_name: string;
  source_quality: string;
  checked_at: string;
  heading: string;
  description: string;
  total_records: number | null;
  displayed_records: number;
  summary_fields: Array<{ label: string; value: string }>;
  records: Array<{
    title: string;
    fields: Array<{ label: string; value: string }>;
    source_url: string | null;
  }>;
  provider_url: string | null;
  provider_link_available: boolean;
  provider_access_notice: string | null;
};

export type Event = {
  id: string;
  event_type: string;
  event_subtype: string;
  occurred_at: string | null;
  published_at: string | null;
  published_on: string | null;
  direction: string;
  materiality_score: number;
  risk_severity: string;
  confidence_score: string;
  source_quality: string;
  title: string;
  summary: string;
  facts: Array<{ name: string; value: string; unit: string | null }>;
  uncertainties: string[];
  status: string;
  publication_route: string;
  publication_policy_version: string;
  publication_reasons: string[];
  observed_at: string;
  evidence: Evidence[];
  visibility_scope: string;
  analysis: InvestorChangeAnalysis | null;
};

export type InvestorChangeAnalysis = {
  schema_version: "investor-change-analysis-v1";
  headline: string;
  before_value: string;
  after_value: string;
  what_changed: string;
  why_it_matters: string;
  potential_impacts: string[];
  uncertainties: string[];
  evidence_ids: string[];
  confidence: number;
  follow_up_items: string[];
  impact_direction: "positive" | "negative" | "mixed" | "neutral" | "uncertain";
  disclaimer: "模型辅助解读，不构成投资建议。";
  generated_at: string;
};

export type ReviewWorkbenchItem = {
  id: string;
  event_id: string | null;
  entity_mention_id: string | null;
  status: string;
  trigger_rules: string[];
  decision: string | null;
  decision_reason: string | null;
  created_at: string;
  decided_at: string | null;
  company_id: string | null;
  company_legal_name: string | null;
  event: Event | null;
  mention_text: string | null;
  match_rule: string | null;
  match_confidence: string | null;
  resolution_status: string | null;
  identity_candidates: IdentityCandidate[];
};

export type SharingDecision = {
  id: string;
  action: string;
  reason: string;
  actor_user_id: string;
  created_at: string;
  shared_event_id: string | null;
};

export type SharingCandidate = {
  source_event_id: string;
  company_id: string;
  company_legal_name: string;
  company_credit_code: string | null;
  source_scope: string;
  owner_type: string;
  owner_id: string;
  owner_name: string;
  identity_ambiguous: boolean;
  shared_event_id: string | null;
  shared_event_status: string | null;
  event: Event;
  decisions: SharingDecision[];
};

export type IdentityCandidate = {
  verification_id: string;
  company_id: string;
  legal_name: string;
  credit_code: string;
  registered_region: string | null;
  registration_status: string;
  verification_status: string;
  verification_basis: string;
  match_rule: string;
  checked_at: string;
  source_name: string;
  canonical_url: string;
};

export type Investment = {
  fund_id: string;
  fund_name: string;
  round_name: string;
  amount: string | null;
  currency: string | null;
  ownership: string | null;
  internal_valuation: string | null;
  visibility_scope: string;
};

export type CompanyDetail = {
  id: string;
  is_platform_shared: boolean;
  legal_name: string;
  credit_code: string | null;
  registered_region: string | null;
  official_website: string | null;
  identity_status: string;
  identity_verification_basis: string | null;
  data_as_of: string | null;
  last_checked_at: string | null;
  freshness_status: string;
  information_gaps: string[];
  investments: Investment[];
  events: Event[];
  platform_unconfirmed_leads: Event[];
  private_events: Event[];
  unconfirmed_leads: Event[];
};

export type PersonalWatchlistItem = {
  id: string;
  company_id: string;
  legal_name: string;
  credit_code: string | null;
  registered_region: string | null;
  identity_status: string;
  freshness_status: string;
  last_checked_at: string | null;
  followed_at: string;
};

export type PersonalCompanyRequest = {
  id: string;
  owner_user_id: string;
  request_type: "inclusion" | "refresh";
  company_id: string | null;
  requested_name: string | null;
  requested_credit_code: string | null;
  status:
    | "pending"
    | "in_review"
    | "identity_queued"
    | "identity_checking"
    | "awaiting_confirmation"
    | "needs_input"
    | "research_queued"
    | "researching"
    | "partial"
    | "budget_deferred"
    | "cancel_requested"
    | "cancelled"
    | "completed"
    | "rejected"
    | "failed";
  research_job_id: string | null;
  research_job_status: string | null;
  research_modules: Record<string, string>;
  queue_position: number | null;
  resolved_legal_name: string | null;
  resolved_credit_code: string | null;
  resolved_registered_region: string | null;
  resolved_registration_status: string | null;
  resolved_registration_authority: string | null;
  identity_checked_at: string | null;
  confirmation_expires_at: string | null;
  confirmed_at: string | null;
  external_calls: number;
  cache_hits: number;
  cancelled_at: string | null;
  cancellation_stage: string | null;
  cancellation_reason: string | null;
  last_error_code: string | null;
  can_confirm: boolean;
  can_cancel: boolean;
  status_message: string;
  reviewed_by_id: string | null;
  reviewed_at: string | null;
  decision_reason: string | null;
  created_at: string;
  updated_at: string;
  reused: boolean;
};

export type PersonalQuotaIncreaseRequest = {
  id: string;
  owner_user_id: string;
  owner_display_name: string;
  owner_email: string;
  requested_daily_extra: number;
  requested_monthly_extra: number;
  request_reason: string;
  status: "pending" | "approved" | "rejected" | "expired";
  approved_daily_extra: number;
  approved_monthly_extra: number;
  effective_until: string | null;
  reviewed_by_id: string | null;
  reviewed_at: string | null;
  decision_reason: string | null;
  created_at: string;
  updated_at: string;
};

export type PersonalQuota = {
  used: number;
  limit: number;
  remaining: number;
};

export type PersonalUsageSummary = {
  period_key: string;
  daily_request_period_key: string;
  searches: PersonalQuota;
  watchlist_companies: PersonalQuota;
  reports: PersonalQuota;
  daily_company_requests: PersonalQuota;
  company_requests: PersonalQuota;
};

export type PersonalCompanyView = {
  company_id: string;
  first_view: boolean;
  previous_viewed_at: string | null;
  window_start_at: string;
  viewed_at: string;
  new_events: Event[];
};

export type PersonalCompanyReportSummary = {
  id: string;
  company_id: string;
  company_legal_name: string;
  report_version: string;
  title: string;
  as_of: string;
  content_hash: string;
  source_event_count: number;
  created_at: string;
};

export type PersonalCompanyReport = PersonalCompanyReportSummary & {
  markdown: string;
  source_event_ids: string[];
  reused: boolean;
};

export type TrustedSource = {
  id: string;
  company_id: string;
  company_legal_name: string;
  company_identity_status: string;
  name: string;
  source_type: string;
  root_domain: string;
  start_url: string;
  list_path_prefix: string | null;
  enabled: boolean;
  access_basis: string;
  license_status: string;
  check_frequency_minutes: number;
  content_retention_policy: string;
  visibility_scope: string;
  last_checked_at: string | null;
  last_success_at: string | null;
  last_failure_code: string | null;
  last_http_status: number | null;
  consecutive_failures: number;
  created_at: string;
  updated_at: string;
};

export type SourceCheckRun = {
  id: string;
  company_id: string;
  trusted_source_id: string;
  source_name: string;
  trigger_type: string;
  scheduled_for: string | null;
  status: string;
  dry_run: boolean;
  policy_version: string;
  request_count: number;
  downloaded_bytes: number;
  new_count: number;
  changed_count: number;
  unchanged_count: number;
  duplicate_count: number;
  failure_count: number;
  external_calls: number;
  paid_api_calls: number;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: string;
  robots_status: string | null;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

export type CandidateDocument = {
  id: string;
  company_id: string;
  company_legal_name: string;
  trusted_source_id: string;
  source_name: string;
  canonical_url: string;
  title: string;
  published_at: string | null;
  first_discovered_at: string;
  last_observed_at: string;
  content_hash: string;
  change_type: string;
  link_health_status: string;
  http_status: number | null;
  excerpt: string | null;
  license_status: string;
  current_source_license_status: string;
  processing_status: string;
  identity_status_at_discovery: string;
  visibility_scope: string;
  handoff_payload: Record<string, unknown>;
  processed_at: string | null;
  decision_reason: string | null;
};

export type CandidateResearchImport = {
  candidate_id: string;
  research_import_id: string;
  raw_document_id: string;
  private_event_id: string;
  status: string;
  reused: boolean;
  auto_published: false;
  shared_fact_created: false;
  external_calls: 0;
};

const apiBaseUrl = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
const demoUserId =
  process.env.DEMO_USER_ID ?? "ac07da52-7378-5762-a1af-74e43d1baeba";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: unknown = null,
  ) {
    super(`API request failed with status ${status}`);
  }
}

async function apiError(response: Response): Promise<ApiError> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    return new ApiError(response.status, payload.detail ?? null);
  } catch {
    return new ApiError(response.status);
  }
}

async function authenticationHeaders(): Promise<Record<string, string>> {
  if (authProvider === "cloudbase") {
    const accessToken = (await cookies()).get(ACCESS_TOKEN_COOKIE)?.value;
    return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
  }
  return { "X-Demo-User-Id": demoUserId };
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    cache: "no-store",
    headers: await authenticationHeaders(),
  });
  if (!response.ok) {
    throw await apiError(response);
  }
  return (await response.json()) as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: "POST",
    cache: "no-store",
    headers: {
      ...(await authenticationHeaders()),
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw await apiError(response);
  }
  return (await response.json()) as T;
}

async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: "PATCH",
    cache: "no-store",
    headers: {
      ...(await authenticationHeaders()),
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw await apiError(response);
  }
  return (await response.json()) as T;
}

async function deleteRequest(path: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: "DELETE",
    cache: "no-store",
    headers: await authenticationHeaders(),
  });
  if (!response.ok) {
    throw await apiError(response);
  }
}

export function getCompanies(): Promise<CompanyListItem[]> {
  return getJson("/api/v1/companies");
}

export function getAuthenticationMe(): Promise<AuthMe> {
  return getJson("/api/v1/auth/me");
}

export function searchCompanies(query: string): Promise<CompanySearchResult[]> {
  const params = new URLSearchParams({ q: query });
  return getJson(`/api/v1/companies/search?${params.toString()}`);
}

export function getCompanySuggestions(
  query: string,
  limit = 8,
): Promise<CompanySuggestion[]> {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  return getJson(`/api/v1/companies/suggestions?${params.toString()}`);
}

export function getCompany(companyId: string): Promise<CompanyDetail> {
  return getJson(`/api/v1/companies/${encodeURIComponent(companyId)}`);
}

export function getEvidenceDetail(evidenceId: string): Promise<EvidenceDetail> {
  return getJson(`/api/v1/evidence/${encodeURIComponent(evidenceId)}`);
}

export function getPersonalUsage(): Promise<PersonalUsageSummary> {
  return getJson("/api/v1/me/usage");
}

export function getPersonalWatchlist(): Promise<PersonalWatchlistItem[]> {
  return getJson("/api/v1/me/watchlist");
}

export function addPersonalWatchlistCompany(companyId: string): Promise<PersonalWatchlistItem> {
  return postJson(`/api/v1/me/watchlist/${encodeURIComponent(companyId)}`, {});
}

export function removePersonalWatchlistCompany(companyId: string): Promise<void> {
  return deleteRequest(`/api/v1/me/watchlist/${encodeURIComponent(companyId)}`);
}

export function getPersonalCompanyRequests(): Promise<PersonalCompanyRequest[]> {
  return getJson("/api/v1/me/company-requests");
}

export function getPlatformCompanyRequests(): Promise<PersonalCompanyRequest[]> {
  return getJson("/api/v1/platform/company-requests");
}

export function approvePlatformCompanyRequestResearch(
  requestId: string,
  payload: { company_id: string | null; reason: string },
): Promise<PersonalCompanyRequest> {
  return postJson(
    `/api/v1/platform/company-requests/${encodeURIComponent(requestId)}/approve-research`,
    payload,
  );
}

export function createPersonalInclusionRequest(payload: {
  company_name: string | null;
  credit_code: string | null;
}): Promise<PersonalCompanyRequest> {
  return postJson("/api/v1/me/company-requests/inclusion", payload);
}

export function createPersonalRefreshRequest(companyId: string): Promise<PersonalCompanyRequest> {
  return postJson(`/api/v1/me/company-requests/refresh/${encodeURIComponent(companyId)}`, {});
}

export function cancelPersonalCompanyRequest(
  requestId: string,
  reason: string | null,
): Promise<PersonalCompanyRequest> {
  return postJson(`/api/v1/me/company-requests/${encodeURIComponent(requestId)}/cancel`, {
    reason,
  });
}

export function getPersonalQuotaIncreaseRequests(): Promise<PersonalQuotaIncreaseRequest[]> {
  return getJson("/api/v1/me/quota-increase-requests");
}

export function createPersonalQuotaIncreaseRequest(payload: {
  requested_daily_extra: number;
  requested_monthly_extra: number;
  reason: string;
}): Promise<PersonalQuotaIncreaseRequest> {
  return postJson("/api/v1/me/quota-increase-requests", payload);
}

export function getPlatformQuotaIncreaseRequests(): Promise<PersonalQuotaIncreaseRequest[]> {
  return getJson("/api/v1/platform/quota-increase-requests");
}

export function decidePlatformQuotaIncreaseRequest(
  requestId: string,
  payload: {
    status: "approved" | "rejected";
    approved_daily_extra: number;
    approved_monthly_extra: number;
    effective_until: string | null;
    reason: string;
  },
): Promise<PersonalQuotaIncreaseRequest> {
  return patchJson(
    `/api/v1/platform/quota-increase-requests/${encodeURIComponent(requestId)}`,
    payload,
  );
}

export function recordPersonalCompanyView(companyId: string): Promise<PersonalCompanyView> {
  return postJson(`/api/v1/me/companies/${encodeURIComponent(companyId)}/view`, {});
}

export function createPersonalCompanyReport(
  companyId: string,
  idempotencyKey: string,
): Promise<PersonalCompanyReport> {
  return postJson(`/api/v1/me/companies/${encodeURIComponent(companyId)}/reports`, {
    idempotency_key: idempotencyKey,
  });
}

export function getPersonalCompanyReports(): Promise<PersonalCompanyReportSummary[]> {
  return getJson("/api/v1/me/reports");
}

export function getPersonalCompanyReport(reportId: string): Promise<PersonalCompanyReport> {
  return getJson(`/api/v1/me/reports/${encodeURIComponent(reportId)}`);
}

export function getReviewWorkbench(): Promise<ReviewWorkbenchItem[]> {
  return getJson("/api/v1/reviews/workbench");
}

export function getSharingCandidates(): Promise<SharingCandidate[]> {
  return getJson("/api/v1/sharing-candidates");
}

export function decideReview(
  reviewId: string,
  decision: "approve" | "reject",
  reason: string,
): Promise<unknown> {
  return postJson(`/api/v1/reviews/${encodeURIComponent(reviewId)}/decision`, {
    decision,
    reason,
  });
}

export function resolveIdentityReview(
  reviewId: string,
  verificationId: string,
  reason: string,
): Promise<unknown> {
  return postJson(`/api/v1/reviews/${encodeURIComponent(reviewId)}/identity-resolution`, {
    verification_id: verificationId,
    reason,
  });
}

export function promoteSharingCandidate(
  sourceEventId: string,
  payload: {
    title: string;
    summary: string;
    reason: string;
    evidence_ids: string[];
    confirm_evidence_support: boolean;
    confirm_unchecked_links: boolean;
  },
): Promise<unknown> {
  return postJson(`/api/v1/events/${encodeURIComponent(sourceEventId)}/sharing/promotion`, payload);
}

export function rejectSharingCandidate(sourceEventId: string, reason: string): Promise<unknown> {
  return postJson(`/api/v1/events/${encodeURIComponent(sourceEventId)}/sharing/rejection`, {
    reason,
  });
}

export function retractSharedEvent(sharedEventId: string, reason: string): Promise<unknown> {
  return postJson(`/api/v1/shared-events/${encodeURIComponent(sharedEventId)}/retraction`, {
    reason,
  });
}

export function getTrustedSources(): Promise<TrustedSource[]> {
  return getJson("/api/v1/trusted-sources");
}

export function createTrustedSource(payload: {
  company_id: string;
  name: string;
  source_type: string;
  root_domain: string;
  start_url: string;
  list_path_prefix?: string | null;
  access_basis: string;
  license_status: string;
  check_frequency_minutes: number;
  content_retention_policy: string;
}): Promise<TrustedSource> {
  return postJson("/api/v1/trusted-sources", payload);
}

export function updateTrustedSource(
  sourceId: string,
  payload: {
    enabled?: boolean;
    list_path_prefix?: string | null;
    access_basis?: string;
    license_status?: string;
    content_retention_policy?: string;
  },
): Promise<TrustedSource> {
  return patchJson(`/api/v1/trusted-sources/${encodeURIComponent(sourceId)}`, payload);
}

export function queueTrustedSourceRun(
  sourceId: string,
  dryRun: boolean,
): Promise<SourceCheckRun> {
  return postJson(`/api/v1/trusted-sources/${encodeURIComponent(sourceId)}/runs`, {
    dry_run: dryRun,
  });
}

export function getTrustedSourceRuns(): Promise<SourceCheckRun[]> {
  return getJson("/api/v1/trusted-source-runs");
}

export function getCandidateDocuments(): Promise<CandidateDocument[]> {
  return getJson("/api/v1/candidate-documents");
}

export function decideCandidateDocument(
  candidateId: string,
  decision: "worth_research" | "irrelevant" | "duplicate" | "source_unavailable",
  reason: string,
): Promise<unknown> {
  return postJson(`/api/v1/candidate-documents/${encodeURIComponent(candidateId)}/decision`, {
    decision,
    reason,
  });
}

export function importCandidateResearch(
  candidateId: string,
  payload: {
    title: string;
    evidence_excerpt: string;
    event_type: string;
    event_subtype: string;
    direction: string;
    materiality_score: number;
    risk_severity: string;
    confidence_score: number;
    source_quality: string;
    fact_name: string;
    fact_value: string;
    fact_unit: string | null;
    occurred_at: string | null;
    uncertainties: string[];
    research_reason: string;
  },
): Promise<CandidateResearchImport> {
  return postJson(
    `/api/v1/candidate-documents/${encodeURIComponent(candidateId)}/research-import`,
    payload,
  );
}
