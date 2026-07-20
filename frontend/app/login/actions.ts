"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import {
  AuthenticationApiError,
  loginWithEmailCode,
  requestEmailVerification,
  revokeAuthentication,
} from "@/lib/auth-api";
import {
  ACCESS_TOKEN_COOKIE,
  authProvider,
  REFRESH_TOKEN_COOKIE,
  safeReturnPath,
  sessionCookieOptions,
  VERIFICATION_ID_COOKIE,
} from "@/lib/auth-session";

const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const codePattern = /^\d{6}$/;

function loginError(error: unknown): string {
  if (!(error instanceof AuthenticationApiError)) return "unavailable";
  if (error.status === 401) return "invalid_code";
  if (error.status === 403) return "invitation_required";
  if (error.status === 409) return "challenge_required";
  if (error.status === 429) return "rate_limited";
  return "unavailable";
}

export async function requestLoginCode(formData: FormData): Promise<void> {
  if (authProvider !== "cloudbase") redirect("/login?error=not_enabled");
  const email = String(formData.get("email") ?? "").trim().toLowerCase();
  const returnTo = safeReturnPath(String(formData.get("next") ?? "/"));
  if (email.length > 320 || !emailPattern.test(email)) {
    redirect(`/login?error=invalid_email&next=${encodeURIComponent(returnTo)}`);
  }
  let challenge;
  try {
    challenge = await requestEmailVerification(email);
  } catch (error) {
    redirect(`/login?error=${loginError(error)}&next=${encodeURIComponent(returnTo)}`);
  }
  const cookieStore = await cookies();
  cookieStore.set(
    VERIFICATION_ID_COOKIE,
    challenge.verification_id,
    sessionCookieOptions(Math.min(challenge.expires_in, 600)),
  );
  redirect(`/login?step=verify&sent=1&next=${encodeURIComponent(returnTo)}`);
}

export async function completeLogin(formData: FormData): Promise<void> {
  if (authProvider !== "cloudbase") redirect("/login?error=not_enabled");
  const verificationCode = String(formData.get("verification_code") ?? "").trim();
  const returnTo = safeReturnPath(String(formData.get("next") ?? "/"));
  const cookieStore = await cookies();
  const verificationId = cookieStore.get(VERIFICATION_ID_COOKIE)?.value;
  if (!verificationId || !codePattern.test(verificationCode)) {
    redirect(`/login?error=invalid_code&next=${encodeURIComponent(returnTo)}`);
  }
  let tokens;
  try {
    tokens = await loginWithEmailCode(verificationId, verificationCode);
  } catch (error) {
    redirect(
      `/login?step=verify&error=${loginError(error)}&next=${encodeURIComponent(returnTo)}`,
    );
  }
  cookieStore.set(
    ACCESS_TOKEN_COOKIE,
    tokens.access_token,
    sessionCookieOptions(Math.min(Math.max(tokens.expires_in, 60), 86_400)),
  );
  cookieStore.set(
    REFRESH_TOKEN_COOKIE,
    tokens.refresh_token,
    sessionCookieOptions(30 * 24 * 60 * 60),
  );
  cookieStore.delete(VERIFICATION_ID_COOKIE);
  redirect(returnTo);
}

export async function logout(): Promise<void> {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get(ACCESS_TOKEN_COOKIE)?.value;
  if (authProvider === "cloudbase" && accessToken) {
    try {
      await revokeAuthentication(accessToken);
    } catch {
      // 本地 Cookie 始终清除；Provider 撤销失败由后端审计并等待令牌自然过期。
    }
  }
  cookieStore.delete(ACCESS_TOKEN_COOKIE);
  cookieStore.delete(REFRESH_TOKEN_COOKIE);
  cookieStore.delete(VERIFICATION_ID_COOKIE);
  redirect("/login?result=logged_out");
}
