"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import {
  ApiError,
  createTrustedSource,
  decideCandidateDocument,
  queueTrustedSourceRun,
  updateTrustedSource,
} from "@/lib/api";

const uuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const sourceTypes = new Set(["single_page", "list_page", "rss", "sitemap"]);
const retentionPolicies = new Set(["metadata_only", "minimal_excerpt"]);
const candidateDecisions = new Set([
  "worth_research",
  "irrelevant",
  "duplicate",
  "source_unavailable",
]);

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
    licenseStatus.length < 2 ||
    licenseStatus.length > 32 ||
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
