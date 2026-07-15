"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { ApiError, decideReview } from "@/lib/api";

const reviewIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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
