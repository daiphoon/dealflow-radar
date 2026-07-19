"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import {
  ApiError,
  createTrustedSource,
  decideCandidateDocument,
  importCandidateResearch,
  queueTrustedSourceRun,
  updateTrustedSource,
} from "@/lib/api";

const uuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const sourceTypes = new Set(["single_page", "list_page", "rss", "sitemap"]);
const retentionPolicies = new Set(["metadata_only", "minimal_excerpt"]);
const licenseStatuses = new Set([
  "public_access",
  "permission_confirmed",
  "unclear",
  "restricted",
]);
const candidateDecisions = new Set([
  "worth_research",
  "irrelevant",
  "duplicate",
  "source_unavailable",
]);
const eventTypes = new Set([
  "financial_operation",
  "financing_cap_table",
  "contract_commercial",
  "product_technology",
  "governance_people",
  "legal_compliance",
  "capacity_assets",
  "exit_liquidity",
  "information_quality",
]);
const directions = new Set(["positive", "negative", "neutral", "mixed", "unknown"]);
const riskSeverities = new Set(["none", "low", "moderate", "high", "critical"]);
const sourceQualities = new Set(["A", "B", "C", "D"]);
const snakeCasePattern = /^[a-z][a-z0-9_]*$/;
const datePattern = /^\d{4}-\d{2}-\d{2}$/;

function monitoringError(error: unknown): string {
  if (!(error instanceof ApiError)) return "request_failed";
  if (error.status === 403) return "forbidden";
  if (error.status === 409) return "conflict";
  if (error.status === 422) return "invalid_input";
  return "request_failed";
}

export async function submitTrustedSource(formData: FormData): Promise<void> {
  const companyId = String(formData.get("company_id") ?? "");
  const name = String(formData.get("name") ?? "").trim();
  const sourceType = String(formData.get("source_type") ?? "");
  const rootDomain = String(formData.get("root_domain") ?? "").trim().toLowerCase();
  const startUrl = String(formData.get("start_url") ?? "").trim();
  const listPathPrefix = String(formData.get("list_path_prefix") ?? "").trim();
  const accessBasis = String(formData.get("access_basis") ?? "").trim();
  const licenseStatus = String(formData.get("license_status") ?? "").trim();
  const frequency = Number(formData.get("check_frequency_minutes"));
  const retention = String(formData.get("content_retention_policy") ?? "");
  let parsedUrl: URL;
  try {
    parsedUrl = new URL(startUrl);
  } catch {
    redirect("/monitoring?error=invalid_input");
  }
  if (
    !uuidPattern.test(companyId) ||
    name.length < 2 ||
    name.length > 200 ||
    !sourceTypes.has(sourceType) ||
    rootDomain.length < 3 ||
    rootDomain.length > 253 ||
    parsedUrl.protocol !== "https:" ||
    (listPathPrefix.length > 0 &&
      (sourceType !== "list_page" ||
        !listPathPrefix.startsWith("/") ||
        listPathPrefix.startsWith("//") ||
        listPathPrefix.includes("?") ||
        listPathPrefix.includes("#") ||
        listPathPrefix.length > 500)) ||
    accessBasis.length < 3 ||
    accessBasis.length > 2000 ||
    !licenseStatuses.has(licenseStatus) ||
    !Number.isInteger(frequency) ||
    frequency < 60 ||
    frequency > 525_600 ||
    !retentionPolicies.has(retention)
  ) {
    redirect("/monitoring?error=invalid_input");
  }

  try {
    await createTrustedSource({
      company_id: companyId,
      name,
      source_type: sourceType,
      root_domain: rootDomain,
      start_url: startUrl,
      list_path_prefix: listPathPrefix || null,
      access_basis: accessBasis,
      license_status: licenseStatus,
      check_frequency_minutes: frequency,
      content_retention_policy: retention,
    });
  } catch (error) {
    redirect(`/monitoring?error=${monitoringError(error)}`);
  }
  revalidatePath("/monitoring");
  redirect("/monitoring?result=source_created");
}

export async function submitSourceEnabled(formData: FormData): Promise<void> {
  const sourceId = String(formData.get("source_id") ?? "");
  const enabled = String(formData.get("enabled") ?? "") === "true";
  if (!uuidPattern.test(sourceId)) redirect("/monitoring?error=invalid_input");
  try {
    await updateTrustedSource(sourceId, { enabled });
  } catch (error) {
    redirect(`/monitoring?error=${monitoringError(error)}`);
  }
  revalidatePath("/monitoring");
  redirect(`/monitoring?result=${enabled ? "source_enabled" : "source_disabled"}`);
}

export async function submitSourceListPathPrefix(formData: FormData): Promise<void> {
  const sourceId = String(formData.get("source_id") ?? "");
  const prefix = String(formData.get("list_path_prefix") ?? "").trim();
  if (
    !uuidPattern.test(sourceId) ||
    (prefix.length > 0 &&
      (!prefix.startsWith("/") ||
        prefix.startsWith("//") ||
        prefix.includes("?") ||
        prefix.includes("#") ||
        prefix.length > 500))
  ) {
    redirect("/monitoring?error=invalid_input");
  }
  try {
    await updateTrustedSource(sourceId, { list_path_prefix: prefix || null });
  } catch (error) {
    redirect(`/monitoring?error=${monitoringError(error)}`);
  }
  revalidatePath("/monitoring");
  redirect("/monitoring?result=source_scope_updated");
}

export async function submitSourcePolicy(formData: FormData): Promise<void> {
  const sourceId = String(formData.get("source_id") ?? "");
  const accessBasis = String(formData.get("access_basis") ?? "").trim();
  const licenseStatus = String(formData.get("license_status") ?? "");
  const retention = String(formData.get("content_retention_policy") ?? "");
  if (
    !uuidPattern.test(sourceId) ||
    accessBasis.length < 3 ||
    accessBasis.length > 2000 ||
    !licenseStatuses.has(licenseStatus) ||
    !retentionPolicies.has(retention)
  ) {
    redirect("/monitoring?error=invalid_input");
  }
  try {
    await updateTrustedSource(sourceId, {
      access_basis: accessBasis,
      license_status: licenseStatus,
      content_retention_policy: retention,
    });
  } catch (error) {
    redirect(`/monitoring?error=${monitoringError(error)}`);
  }
  revalidatePath("/monitoring");
  redirect("/monitoring?result=source_policy_updated");
}

export async function submitSourceRun(formData: FormData): Promise<void> {
  const sourceId = String(formData.get("source_id") ?? "");
  const runMode = String(formData.get("run_mode") ?? "dry_run");
  const dryRun = runMode === "dry_run";
  const confirmed = formData.get("confirmed") === "on";
  if (
    !uuidPattern.test(sourceId) ||
    (!dryRun && runMode !== "real") ||
    (!dryRun && !confirmed)
  ) {
    redirect("/monitoring?error=run_confirmation_required");
  }
  try {
    await queueTrustedSourceRun(sourceId, dryRun);
  } catch (error) {
    redirect(`/monitoring?error=${monitoringError(error)}`);
  }
  revalidatePath("/monitoring");
  redirect(`/monitoring?result=${dryRun ? "dry_run_queued" : "real_run_queued"}`);
}

export async function submitCandidateDecision(formData: FormData): Promise<void> {
  const candidateId = String(formData.get("candidate_id") ?? "");
  const decision = String(formData.get("decision") ?? "");
  const reason = String(formData.get("reason") ?? "").trim();
  if (
    !uuidPattern.test(candidateId) ||
    !candidateDecisions.has(decision) ||
    reason.length < 3 ||
    reason.length > 1000
  ) {
    redirect("/monitoring?error=invalid_decision");
  }
  try {
    await decideCandidateDocument(
      candidateId,
      decision as "worth_research" | "irrelevant" | "duplicate" | "source_unavailable",
      reason,
    );
  } catch (error) {
    redirect(`/monitoring?error=${monitoringError(error)}`);
  }
  revalidatePath("/monitoring");
  redirect("/monitoring?result=candidate_decided");
}

export async function submitCandidateResearch(formData: FormData): Promise<void> {
  const candidateId = String(formData.get("candidate_id") ?? "");
  const title = String(formData.get("title") ?? "").trim();
  const evidenceExcerpt = String(formData.get("evidence_excerpt") ?? "").trim();
  const eventType = String(formData.get("event_type") ?? "");
  const eventSubtype = String(formData.get("event_subtype") ?? "").trim();
  const direction = String(formData.get("direction") ?? "");
  const materialityScore = Number(formData.get("materiality_score"));
  const riskSeverity = String(formData.get("risk_severity") ?? "");
  const confidenceScore = Number(formData.get("confidence_score"));
  const sourceQuality = String(formData.get("source_quality") ?? "");
  const factName = String(formData.get("fact_name") ?? "").trim();
  const factValue = String(formData.get("fact_value") ?? "").trim();
  const factUnit = String(formData.get("fact_unit") ?? "").trim();
  const occurredOn = String(formData.get("occurred_on") ?? "").trim();
  const researchReason = String(formData.get("research_reason") ?? "").trim();
  const uncertainties = String(formData.get("uncertainties") ?? "")
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);

  if (
    !uuidPattern.test(candidateId) ||
    title.length < 3 ||
    title.length > 200 ||
    evidenceExcerpt.length < 3 ||
    evidenceExcerpt.length > 1000 ||
    !eventTypes.has(eventType) ||
    eventSubtype.length < 2 ||
    eventSubtype.length > 64 ||
    !snakeCasePattern.test(eventSubtype) ||
    !directions.has(direction) ||
    !Number.isInteger(materialityScore) ||
    materialityScore < 0 ||
    materialityScore > 100 ||
    !riskSeverities.has(riskSeverity) ||
    !Number.isFinite(confidenceScore) ||
    confidenceScore < 0 ||
    confidenceScore > 1 ||
    !sourceQualities.has(sourceQuality) ||
    factName.length < 2 ||
    factName.length > 80 ||
    !snakeCasePattern.test(factName) ||
    factValue.length < 1 ||
    factValue.length > 500 ||
    factUnit.length > 40 ||
    (occurredOn.length > 0 && !datePattern.test(occurredOn)) ||
    uncertainties.length > 20 ||
    uncertainties.some((item) => item.length > 500) ||
    researchReason.length < 3 ||
    researchReason.length > 1000
  ) {
    redirect("/monitoring?error=invalid_research");
  }

  try {
    await importCandidateResearch(candidateId, {
      title,
      evidence_excerpt: evidenceExcerpt,
      event_type: eventType,
      event_subtype: eventSubtype,
      direction,
      materiality_score: materialityScore,
      risk_severity: riskSeverity,
      confidence_score: confidenceScore,
      source_quality: sourceQuality,
      fact_name: factName,
      fact_value: factValue,
      fact_unit: factUnit || null,
      occurred_at: occurredOn ? `${occurredOn}T00:00:00+08:00` : null,
      uncertainties,
      research_reason: researchReason,
    });
  } catch (error) {
    redirect(`/monitoring?error=${monitoringError(error)}`);
  }
  revalidatePath("/monitoring");
  revalidatePath("/reviews");
  redirect("/monitoring?result=candidate_research_imported");
}
