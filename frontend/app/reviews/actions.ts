"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import {
  ApiError,
  approvePlatformCompanyRequestResearch,
  decidePlatformQuotaIncreaseRequest,
  decideReview,
  promoteSharingCandidate,
  rejectSharingCandidate,
  resolveIdentityReview,
  retractSharedEvent,
} from "@/lib/api";

const reviewIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function submitCompanyResearchApproval(formData: FormData): Promise<void> {
  const requestId = String(formData.get("request_id") ?? "");
  const reason = String(formData.get("reason") ?? "").trim();
  if (!reviewIdPattern.test(requestId) || reason.length < 3 || reason.length > 1000) {
    redirect("/reviews?error=invalid_research_approval");
  }
  try {
    await approvePlatformCompanyRequestResearch(requestId, {
      company_id: null,
      reason,
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      redirect("/reviews?error=research_identity_not_ready");
    }
    redirect("/reviews?error=research_approval_failed");
  }
  revalidatePath("/reviews");
  revalidatePath("/watchlist");
  redirect("/reviews?result=research_approved");
}

export async function submitReviewDecision(formData: FormData): Promise<void> {
  const reviewId = String(formData.get("review_id") ?? "");
  const decision = String(formData.get("decision") ?? "");
  const reason = String(formData.get("reason") ?? "").trim();
  if (
    !reviewIdPattern.test(reviewId) ||
    (decision !== "approve" && decision !== "reject") ||
    reason.length < 3 ||
    reason.length > 1000
  ) {
    redirect("/reviews?error=invalid_input");
  }

  let errorCode: string | null = null;
  try {
    await decideReview(reviewId, decision, reason);
  } catch (error) {
    if (error instanceof ApiError) {
      errorCode = error.status === 403 ? "forbidden" : "request_failed";
    } else {
      errorCode = "request_failed";
    }
  }
  if (errorCode) {
    redirect(`/reviews?error=${errorCode}`);
  }

  revalidatePath("/reviews");
  redirect(`/reviews?result=${decision}`);
}

export async function submitIdentityResolution(formData: FormData): Promise<void> {
  const reviewId = String(formData.get("review_id") ?? "");
  const verificationId = String(formData.get("verification_id") ?? "");
  const reason = String(formData.get("reason") ?? "").trim();
  const confirmed = formData.get("confirmed") === "on";
  if (
    !reviewIdPattern.test(reviewId) ||
    !reviewIdPattern.test(verificationId) ||
    reason.length < 3 ||
    reason.length > 1000 ||
    !confirmed
  ) {
    redirect("/reviews?error=invalid_identity_input");
  }

  let errorCode: string | null = null;
  try {
    await resolveIdentityReview(reviewId, verificationId, reason);
  } catch (error) {
    if (error instanceof ApiError) {
      errorCode = error.status === 403 ? "identity_forbidden" : "request_failed";
    } else {
      errorCode = "request_failed";
    }
  }
  if (errorCode) {
    redirect(`/reviews?error=${errorCode}`);
  }

  revalidatePath("/reviews");
  revalidatePath("/");
  redirect("/reviews?result=identity_resolved");
}

export async function submitSharingPromotion(formData: FormData): Promise<void> {
  const sourceEventId = String(formData.get("source_event_id") ?? "");
  const title = String(formData.get("title") ?? "").trim();
  const summary = String(formData.get("summary") ?? "").trim();
  const reason = String(formData.get("reason") ?? "").trim();
  const evidenceIds = formData.getAll("evidence_ids").map(String);
  const confirmEvidenceSupport = formData.get("confirm_evidence_support") === "on";
  const confirmUncheckedLinks = formData.get("confirm_unchecked_links") === "on";
  if (
    !reviewIdPattern.test(sourceEventId) ||
    title.length < 3 ||
    title.length > 200 ||
    summary.length < 3 ||
    summary.length > 2000 ||
    reason.length < 3 ||
    reason.length > 1000 ||
    evidenceIds.length === 0 ||
    evidenceIds.some((id) => !reviewIdPattern.test(id)) ||
    !confirmEvidenceSupport
  ) {
    redirect("/reviews?error=invalid_sharing_input");
  }
  try {
    await promoteSharingCandidate(sourceEventId, {
      title,
      summary,
      reason,
      evidence_ids: evidenceIds,
      confirm_evidence_support: confirmEvidenceSupport,
      confirm_unchecked_links: confirmUncheckedLinks,
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 422) {
      redirect("/reviews?error=sharing_ineligible");
    }
    redirect("/reviews?error=sharing_failed");
  }
  revalidatePath("/reviews");
  revalidatePath("/");
  revalidatePath(`/companies`);
  redirect("/reviews?result=sharing_promoted");
}

export async function submitSharingRejection(formData: FormData): Promise<void> {
  const sourceEventId = String(formData.get("source_event_id") ?? "");
  const reason = String(formData.get("reason") ?? "").trim();
  if (!reviewIdPattern.test(sourceEventId) || reason.length < 3 || reason.length > 1000) {
    redirect("/reviews?error=invalid_sharing_input");
  }
  try {
    await rejectSharingCandidate(sourceEventId, reason);
  } catch {
    redirect("/reviews?error=sharing_failed");
  }
  revalidatePath("/reviews");
  redirect("/reviews?result=sharing_rejected");
}

export async function submitSharingRetraction(formData: FormData): Promise<void> {
  const sharedEventId = String(formData.get("shared_event_id") ?? "");
  const reason = String(formData.get("reason") ?? "").trim();
  if (!reviewIdPattern.test(sharedEventId) || reason.length < 3 || reason.length > 1000) {
    redirect("/reviews?error=invalid_sharing_input");
  }
  try {
    await retractSharedEvent(sharedEventId, reason);
  } catch {
    redirect("/reviews?error=sharing_failed");
  }
  revalidatePath("/reviews");
  revalidatePath("/");
  revalidatePath(`/companies`);
  redirect("/reviews?result=sharing_retracted");
}

export async function submitQuotaIncreaseDecision(formData: FormData): Promise<void> {
  const requestId = String(formData.get("request_id") ?? "");
  const status = String(formData.get("status") ?? "");
  const dailyExtra = Number(formData.get("approved_daily_extra"));
  const monthlyExtra = Number(formData.get("approved_monthly_extra"));
  const validDays = Number(formData.get("valid_days"));
  const reason = String(formData.get("reason") ?? "").trim();
  if (
    !reviewIdPattern.test(requestId) ||
    (status !== "approved" && status !== "rejected") ||
    !Number.isInteger(dailyExtra) ||
    !Number.isInteger(monthlyExtra) ||
    dailyExtra < 0 ||
    dailyExtra > 100 ||
    monthlyExtra < 0 ||
    monthlyExtra > 1000 ||
    !Number.isInteger(validDays) ||
    validDays < 1 ||
    validDays > 90 ||
    reason.length < 3 ||
    reason.length > 1000 ||
    (status === "approved" && dailyExtra === 0 && monthlyExtra === 0)
  ) {
    redirect("/reviews?error=invalid_quota_decision");
  }
  try {
    await decidePlatformQuotaIncreaseRequest(requestId, {
      status: status as "approved" | "rejected",
      approved_daily_extra: status === "approved" ? dailyExtra : 0,
      approved_monthly_extra: status === "approved" ? monthlyExtra : 0,
      effective_until:
        status === "approved"
          ? new Date(Date.now() + validDays * 24 * 60 * 60 * 1000).toISOString()
          : null,
      reason,
    });
  } catch {
    redirect("/reviews?error=quota_decision_failed");
  }
  revalidatePath("/reviews");
  revalidatePath("/watchlist");
  redirect(`/reviews?result=quota_${status}`);
}
