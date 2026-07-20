import { type NextRequest, NextResponse } from "next/server";

import { AuthenticationApiError, refreshAuthentication } from "@/lib/auth-api";
import {
  ACCESS_TOKEN_COOKIE,
  REFRESH_TOKEN_COOKIE,
  safeReturnPath,
  sessionCookieOptions,
  VERIFICATION_ID_COOKIE,
} from "@/lib/auth-session";

export async function GET(request: NextRequest): Promise<NextResponse> {
  const returnTo = safeReturnPath(request.nextUrl.searchParams.get("next"));
  const refreshToken = request.cookies.get(REFRESH_TOKEN_COOKIE)?.value;
  if (!refreshToken) {
    return NextResponse.redirect(
      new URL(`/login?next=${encodeURIComponent(returnTo)}`, request.url),
    );
  }
  try {
    const tokens = await refreshAuthentication(refreshToken);
    const response = NextResponse.redirect(new URL(returnTo, request.url));
    response.cookies.set(
      ACCESS_TOKEN_COOKIE,
      tokens.access_token,
      sessionCookieOptions(Math.min(Math.max(tokens.expires_in, 60), 86_400)),
    );
    response.cookies.set(
      REFRESH_TOKEN_COOKIE,
      tokens.refresh_token,
      sessionCookieOptions(30 * 24 * 60 * 60),
    );
    response.cookies.delete(VERIFICATION_ID_COOKIE);
    return response;
  } catch (error) {
    const reason =
      error instanceof AuthenticationApiError && error.status === 503
        ? "unavailable"
        : "session_expired";
    const response = NextResponse.redirect(
      new URL(`/login?error=${reason}&next=${encodeURIComponent(returnTo)}`, request.url),
    );
    response.cookies.delete(ACCESS_TOKEN_COOKIE);
    response.cookies.delete(REFRESH_TOKEN_COOKIE);
    response.cookies.delete(VERIFICATION_ID_COOKIE);
    return response;
  }
}
