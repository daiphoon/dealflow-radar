import "server-only";

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

export type Evidence = {
  id: string;
  source_name: string;
  source_quality: string;
  title: string;
  canonical_url: string;
  published_at: string | null;
  observed_at: string;
  excerpt: string;
};

export type Event = {
  id: string;
  event_type: string;
  event_subtype: string;
  occurred_at: string | null;
  published_at: string | null;
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
  observed_at: string;
  evidence: Evidence[];
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
  legal_name: string;
  registered_region: string | null;
  identity_status: string;
  data_as_of: string | null;
  last_checked_at: string | null;
  freshness_status: string;
  information_gaps: string[];
  investments: Investment[];
  events: Event[];
};

const apiBaseUrl = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
const demoUserId =
  process.env.DEMO_USER_ID ?? "ac07da52-7378-5762-a1af-74e43d1baeba";

export class ApiError extends Error {
  constructor(public readonly status: number) {
    super(`API request failed with status ${status}`);
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    cache: "no-store",
    headers: { "X-Demo-User-Id": demoUserId },
  });
  if (!response.ok) {
    throw new ApiError(response.status);
  }
  return (await response.json()) as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "X-Demo-User-Id": demoUserId,
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new ApiError(response.status);
  }
  return (await response.json()) as T;
}

export function getCompanies(): Promise<CompanyListItem[]> {
  return getJson("/api/v1/companies");
}

export function getCompany(companyId: string): Promise<CompanyDetail> {
  return getJson(`/api/v1/companies/${encodeURIComponent(companyId)}`);
}

export function getReviewWorkbench(): Promise<ReviewWorkbenchItem[]> {
  return getJson("/api/v1/reviews/workbench");
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
