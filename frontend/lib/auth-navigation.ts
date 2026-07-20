import "server-only";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { ApiError } from "@/lib/api";
import { authProvider, REFRESH_TOKEN_COOKIE, safeReturnPath } from "@/lib/auth-session";

export async function redirectIfAuthenticationRequired(
  error: unknown,
  returnTo: string,
): Promise<void> {
  if (authProvider !== "cloudbase" || !(error instanceof ApiError) || error.status !== 401) {
    return;
  }
  const target = safeReturnPath(returnTo);
  const cookieStore = await cookies();
  if (cookieStore.has(REFRESH_TOKEN_COOKIE)) {
    redirect(`/auth/refresh?next=${encodeURIComponent(target)}`);
  }
  redirect(`/login?next=${encodeURIComponent(target)}`);
}
