"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import {
  ApiError,
  addPersonalWatchlistCompany,
  cancelPersonalCompanyRequest,
  createPersonalCompanyReport,
  createPersonalInclusionRequest,
  createPersonalQuotaIncreaseRequest,
  createPersonalRefreshRequest,
  recordPersonalCompanyView,
  removePersonalWatchlistCompany,
} from "@/lib/api";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const sha256Pattern = /^[0-9a-f]{64}$/;

function actionError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) return "limit_reached";
    if (error.status === 404) return "not_available";
    if (error.status === 409 && error.detail === "company already available") {
      return "already_available";
    }
    if (error.status === 409 || error.status === 422) return "invalid_input";
    if (error.status === 401) return "unauthorized";
  }
  return "request_failed";
}

export async function followCompany(formData: FormData): Promise<void> {
  const companyId = String(formData.get("company_id") ?? "");
  if (!uuidPattern.test(companyId)) redirect("/?error=invalid_input");
  try {
    await addPersonalWatchlistCompany(companyId);
  } catch (error) {
    redirect(`/companies/${companyId}?error=${actionError(error)}`);
  }
  revalidatePath("/watchlist");
  revalidatePath(`/companies/${companyId}`);
  redirect(`/companies/${companyId}?result=followed`);
}

export async function unfollowCompany(formData: FormData): Promise<void> {
  const companyId = String(formData.get("company_id") ?? "");
  const returnTo =
    formData.get("return_to") === "watchlist" ? "/watchlist" : `/companies/${companyId}`;
  if (!uuidPattern.test(companyId)) redirect("/?error=invalid_input");
  try {
    await removePersonalWatchlistCompany(companyId);
  } catch (error) {
    redirect(`${returnTo}?error=${actionError(error)}`);
  }
  revalidatePath("/watchlist");
  revalidatePath(`/companies/${companyId}`);
  redirect(`${returnTo}?result=unfollowed`);
}

export async function requestCompanyInclusion(formData: FormData): Promise<void> {
  const companyName = String(formData.get("company_name") ?? "").trim();
  const creditCode = String(formData.get("credit_code") ?? "").trim();
  const returnQuery = String(formData.get("return_query") ?? "").trim();
  const suffix = returnQuery ? `&q=${encodeURIComponent(returnQuery)}` : "";
  if (
    (!companyName && !creditCode) ||
    (creditCode.length > 0 && !/^[0-9A-Za-z]{18}$/.test(creditCode))
  ) {
    redirect(`/?error=invalid_input${suffix}`);
  }
  let result: string;
  try {
    const request = await createPersonalInclusionRequest({
      company_name: companyName || null,
      credit_code: creditCode || null,
    });
    result = request.reused ? "request_reused" : "inclusion_requested";
  } catch (error) {
    redirect(`/?error=${actionError(error)}${suffix}`);
  }
  revalidatePath("/watchlist");
  redirect(`/watchlist?result=${result}`);
}

export async function requestCompanyRefresh(formData: FormData): Promise<void> {
  const companyId = String(formData.get("company_id") ?? "");
  if (!uuidPattern.test(companyId)) redirect("/?error=invalid_input");
  let result: string;
  try {
    const request = await createPersonalRefreshRequest(companyId);
    result = request.reused ? "request_reused" : "refresh_requested";
  } catch (error) {
    redirect(`/companies/${companyId}?error=${actionError(error)}`);
  }
  revalidatePath("/watchlist");
  redirect(`/companies/${companyId}?result=${result}`);
}

export async function cancelCompanyRequest(formData: FormData): Promise<void> {
  const requestId = String(formData.get("request_id") ?? "");
  const reason = String(formData.get("reason") ?? "").trim();
  if (!uuidPattern.test(requestId) || reason.length > 500) {
    redirect("/watchlist?error=invalid_input");
  }
  try {
    await cancelPersonalCompanyRequest(requestId, reason || null);
  } catch (error) {
    redirect(`/watchlist?error=${actionError(error)}`);
  }
  revalidatePath("/watchlist");
  redirect("/watchlist?result=request_cancelled");
}

export async function requestTemporaryQuotaIncrease(formData: FormData): Promise<void> {
  const dailyExtra = Number(formData.get("requested_daily_extra"));
  const monthlyExtra = Number(formData.get("requested_monthly_extra"));
  const reason = String(formData.get("reason") ?? "").trim();
  if (
    !Number.isInteger(dailyExtra) ||
    !Number.isInteger(monthlyExtra) ||
    dailyExtra < 0 ||
    dailyExtra > 100 ||
    monthlyExtra < 0 ||
    monthlyExtra > 1000 ||
    (dailyExtra === 0 && monthlyExtra === 0) ||
    reason.length < 5 ||
    reason.length > 500
  ) {
    redirect("/watchlist?error=invalid_quota_request");
  }
  try {
    await createPersonalQuotaIncreaseRequest({
      requested_daily_extra: dailyExtra,
      requested_monthly_extra: monthlyExtra,
      reason,
    });
  } catch (error) {
    redirect(`/watchlist?error=${actionError(error)}`);
  }
  revalidatePath("/watchlist");
  redirect("/watchlist?result=quota_requested");
}

export async function generateCompanyReport(formData: FormData): Promise<void> {
  const companyId = String(formData.get("company_id") ?? "");
  const idempotencyKey = String(formData.get("idempotency_key") ?? "");
  if (!uuidPattern.test(companyId) || !sha256Pattern.test(idempotencyKey)) {
    redirect("/?error=invalid_input");
  }
  let reportId: string;
  let result: string;
  try {
    const report = await createPersonalCompanyReport(companyId, idempotencyKey);
    reportId = report.id;
    result = report.reused ? "report_reused" : "report_generated";
  } catch (error) {
    redirect(`/companies/${companyId}?error=${actionError(error)}`);
  }
  revalidatePath("/reports");
  redirect(`/reports/${reportId}?result=${result}`);
}

export async function loadPersonalCompanyChanges(companyId: string, renderedVersions?: Record<string, string>) {
  if (!uuidPattern.test(companyId)) throw new Error("invalid company id");
  return recordPersonalCompanyView(companyId, renderedVersions);
}
